"""Store operations the agent can call. Plain functions over StoreData that return JSON-friendly dicts."""

from __future__ import annotations

from datetime import date, datetime
from difflib import SequenceMatcher

import pandas as pd

from emporio import rules
from emporio.data import StoreData, digits_only, normalize_text

STATUS_LABELS = {
    "pending": "pendente",
    "confirmed": "confirmado, em preparação",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
AVAILABILITY = {"active": "disponível", "discontinued": "descontinuado", "coming_soon": "em breve"}
OUT_OF_STOCK = "sem estoque no momento"
MAX_RESULTS = 8
MAX_ALTERNATIVES = 3
FUZZY_MATCH_THRESHOLD = 0.6
MIN_PHONE_DIGITS = 8
ORDER_NOT_FOUND = "Não encontrei um pedido com esse número e esse contato."
RETURN_POLICY_SECTIONS = ["4.1", "4.2", "4.3", "4.4", "8.1"]

STORE_WHATSAPP = "(67) 3321-4500"  # policy 7
STORE_EMAIL = "contato@emporiodamusica.com.br"
COMPLAINT_RESPONSE_TIME = "até 24 horas úteis"  # policy 7.3
DEFAULT_RESPONSE_TIME = "no próximo horário de atendimento"
ESCALATION_REASONS = {
    "complaint": "reclamação",
    "defect": "produto com defeito",
    "policy_doubt": "dúvida de política que o assistente não pode confirmar",
    "customer_request": "cliente pediu um atendente",
    "unresolved": "o assistente não conseguiu resolver",
}


def _singular(token: str) -> str:
    if token.endswith("oes"):
        return token[:-3] + "ao"
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {_singular(t) for t in normalize_text(text).replace("-", " ").split()}


def _matches(query: str, search_text: str) -> bool:
    text_tokens = _tokens(search_text)
    joined = normalize_text(search_text).replace("-", "")
    return all(t in text_tokens or t.replace("-", "") in joined for t in _tokens(query))


def _active_promo(data: StoreData, product_id: int) -> float | None:
    promo = data.active_promotions[data.active_promotions["product_id"] == product_id]
    return float(promo.iloc[0]["discount_percent"]) if not promo.empty else None


def _availability(row: pd.Series) -> str:
    if row["status"] == "active" and row["stock_quantity"] == 0:
        return OUT_OF_STOCK
    return AVAILABILITY.get(row["status"], row["status"])


def _in_stock(products: pd.DataFrame) -> pd.Series:
    return (products["status"] == "active") & (products["stock_quantity"] > 0)


def _product_summary(data: StoreData, row: pd.Series) -> dict:
    quote = rules.quote_price(float(row["price_brl"]), _active_promo(data, int(row["product_id"])))
    summary = {
        "product_id": int(row["product_id"]),
        "name": row["name"],
        "category": row["category_name"],
        "availability": _availability(row),
        "stock_quantity": int(row["stock_quantity"]),
        "list_price": quote.list_price,
        "pix_price": quote.pix_price,
    }
    if quote.promo_percent:
        summary["promotion"] = {
            "discount_percent": quote.promo_percent,
            "promo_price": quote.promo_price,
        }
    return summary


def _card_price(summary: dict) -> float:
    return summary.get("promotion", {}).get("promo_price", summary["list_price"])


def search_products(
    data: StoreData,
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool = True,
) -> dict:
    """Price filters use the price the customer pays by card."""
    products = data.products
    if query:
        products = products[products["search_text"].map(lambda text: _matches(query, text))]
    if category:
        products = products[products["category_name"].map(lambda name: _matches(category, name))]
    if in_stock_only:
        products = products[_in_stock(products)]

    results = [_product_summary(data, row) for _, row in products.iterrows()]
    if min_price is not None:
        results = [p for p in results if _card_price(p) >= min_price]
    if max_price is not None:
        results = [p for p in results if _card_price(p) <= max_price]
    results.sort(key=_card_price)
    return {"total_found": len(results), "products": results[:MAX_RESULTS]}


def _find_product(data: StoreData, name_or_id: str) -> pd.Series | None:
    products = data.products
    if str(name_or_id).strip().isdigit():
        hit = products[products["product_id"] == int(name_or_id)]
        return hit.iloc[0] if not hit.empty else None
    matched = products[products["search_text"].map(lambda text: _matches(name_or_id, text))]
    if not matched.empty:
        return matched.iloc[0]
    query = normalize_text(name_or_id)
    scores = products["name"].map(
        lambda name: SequenceMatcher(None, query, normalize_text(name)).ratio()
    )
    best = scores.idxmax()
    return products.loc[best] if scores[best] >= FUZZY_MATCH_THRESHOLD else None


def _alternatives(data: StoreData, row: pd.Series) -> list[dict]:
    """Policy 7.3: suggest similar products in stock when one is unavailable."""
    products = data.products
    candidates = products[
        (products["category_id"] == row["category_id"])
        & _in_stock(products)
        & (products["product_id"] != row["product_id"])
    ]
    price_gap = (candidates["price_brl"] - row["price_brl"]).abs()
    closest = candidates.assign(gap=price_gap).nsmallest(MAX_ALTERNATIVES, "gap")
    return [_product_summary(data, r) for _, r in closest.iterrows()]


def get_product_details(data: StoreData, name_or_id: str) -> dict:
    row = _find_product(data, name_or_id)
    if row is None:
        return {"found": False, "message": f"Nenhum produto encontrado para '{name_or_id}'."}
    details = _product_summary(data, row)
    details.update(found=True, description=row["description"], specs=row["specs"])
    details["max_installments"] = rules.installment_options(_card_price(details))[-1]

    promotions = data.all_promotions
    expired = promotions[(promotions["product_id"] == row["product_id"]) & (promotions["is_active"] == 0)]
    if not expired.empty and "promotion" not in details:
        details["expired_promotions"] = sorted(expired["description"].unique().tolist())
    if details["availability"] != AVAILABILITY["active"]:
        details["alternatives"] = _alternatives(data, row)
    return details


def list_active_promotions(data: StoreData) -> dict:
    rows = data.products[data.products["product_id"].isin(data.active_promotions["product_id"])]
    return {"promotions": [_product_summary(data, row) for _, row in rows.iterrows()]}


def _customer_matches(customer: pd.Series, phone_or_email: str) -> bool:
    given = phone_or_email.strip().lower()
    if "@" in given:
        return given == customer["email"]
    digits = digits_only(given)
    return len(digits) >= MIN_PHONE_DIGITS and customer["phone_digits"].endswith(
        digits[-MIN_PHONE_DIGITS:]
    )


def _verified_order(data: StoreData, order_id: int, phone_or_email: str) -> tuple[pd.Series, pd.Series] | None:
    """LGPD (policy 9): an order is only shown to someone who knows its contact data."""
    orders = data.orders[data.orders["order_id"] == int(order_id)]
    if orders.empty:
        return None
    order = orders.iloc[0]
    customer = data.customers[data.customers["customer_id"] == order["customer_id"]].iloc[0]
    return (order, customer) if _customer_matches(customer, phone_or_email) else None


def _iso_date(value) -> str | None:
    return None if pd.isna(value) else value.date().isoformat()


def get_order_status(data: StoreData, order_id: int, phone_or_email: str) -> dict:
    verified = _verified_order(data, order_id, phone_or_email)
    if verified is None:
        return {"verified": False, "message": ORDER_NOT_FOUND}
    order, customer = verified
    items = data.order_items[data.order_items["order_id"] == order["order_id"]].merge(
        data.products[["product_id", "name"]], on="product_id"
    )
    return {
        "verified": True,
        "order_id": int(order["order_id"]),
        "customer_first_name": customer["name"].split()[0],
        "status": STATUS_LABELS.get(order["status"], order["status"]),
        "order_date": _iso_date(order["order_date"]),
        "estimated_delivery": _iso_date(order["estimated_delivery"]),
        "tracking_code": order["tracking_code"] or None,
        "items": [{"name": r["name"], "quantity": int(r["quantity"])} for _, r in items.iterrows()],
        "total_paid": float(order["total_brl"]),
        "payment": {"type": order["payment_type"], "installments": int(order["installments"])},
        "notes": order["notes"] or None,
    }


def _assumed_received_on(order: pd.Series) -> date | None:
    """The data has no real delivery date: a delivered order counts as received on its estimate."""
    return order["estimated_delivery"].date() if order["status"] == "delivered" else None


def check_return_options(
    data: StoreData, order_id: int, phone_or_email: str, today: date | None = None
) -> dict:
    verified = _verified_order(data, order_id, phone_or_email)
    if verified is None:
        return {"verified": False, "message": ORDER_NOT_FOUND}
    order, _ = verified
    today = today or datetime.now(rules.STORE_TZ).date()
    if order["status"] == "cancelled":
        return {"verified": True, "order_id": int(order_id), "status": STATUS_LABELS["cancelled"], "applies": False}
    received_on = _assumed_received_on(order)
    windows = rules.return_windows(order["order_date"].date(), received_on, today)
    return {
        "verified": True,
        "order_id": int(order_id),
        "status": STATUS_LABELS.get(order["status"], order["status"]),
        "today": today.isoformat(),
        "assumed_received_on": received_on.isoformat() if received_on else None,
        **windows,
        "policy_sections": RETURN_POLICY_SECTIONS,
    }


def get_installment_options(amount: float) -> dict:
    return {
        "amount": amount,
        "options": rules.installment_options(amount),
        "pix_plus_card_allowed": rules.can_split_payment(amount),
    }


def get_shipping_quote(city: str, amount: float) -> dict:
    return rules.shipping_quote(city, amount)


def get_store_status(now: datetime | None = None) -> dict:
    return rules.store_status(now or datetime.now(rules.STORE_TZ))


def escalate_to_human(reason: str, summary: str, now: datetime | None = None) -> dict:
    """Hand the conversation to the store team (policies 7.3 and 10)."""
    if reason not in ESCALATION_REASONS:
        return {"escalated": False, "message": f"Unknown reason. Use one of: {', '.join(ESCALATION_REASONS)}."}
    now = now or datetime.now(rules.STORE_TZ)
    store = rules.store_status(now)
    response_time = COMPLAINT_RESPONSE_TIME if reason == "complaint" else DEFAULT_RESPONSE_TIME
    return {
        "escalated": True,
        "reason": reason,
        "reason_label": ESCALATION_REASONS[reason],
        "summary": summary,
        "created_at": now.astimezone(rules.STORE_TZ).isoformat(timespec="minutes"),
        "team_available_now": store["open_now"],
        "next_opening": store.get("next_opening"),
        "response_time": response_time,
        "contact": {"whatsapp": STORE_WHATSAPP, "email": STORE_EMAIL},
    }
