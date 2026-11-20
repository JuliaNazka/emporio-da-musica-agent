"""Catch answers that state prices or stock without having consulted the store data (policy 7.1)."""

from __future__ import annotations

import re

DATA_TOOLS = frozenset(
    {
        "search_products",
        "get_product_details",
        "list_active_promotions",
        "get_order_status",
        "check_return_options",
        "get_installment_options",
        "get_shipping_quote",
    }
)
PRICE = re.compile(r"R\$\s*\d")
PRICE_VALUE = re.compile(r"R\$\s*(\d[\d.,]*\d|\d)")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
PRICE_TOLERANCE = 0.01
STOCK = re.compile(r"\b(em estoque|sem estoque|esgotad[oa]s?|unidades? dispon[ií]ve(l|is)|in stock|out of stock)\b", re.IGNORECASE)
# Saying the store has a product is store data too: the model must look it up first.
HAS_PRODUCT = re.compile(r"(?<!n[aã]o )\btemos (o|a|os|as|um|uma)\b", re.IGNORECASE)
AVAILABLE = re.compile(r"\bdispon[ií]ve(l|is)\b", re.IGNORECASE)
# "We don't sell strings" can come from the catalog or from the manual (policy 7.1).
DOES_NOT_SELL = re.compile(
    r"\b(n[aã]o (temos|vendemos|trabalhamos)|cat[aá]logo|we (do not|don't) (have|sell))\b", re.IGNORECASE
)
# Delivery times come from get_shipping_quote, an order lookup or the policy manual, never from memory.
DELIVERY = re.compile(
    r"\b(dias? [uú]t(il|eis)|business days?|prazo de entrega|delivery (time|takes)|(chega|entrega\w*) em \d+)\b",
    re.IGNORECASE,
)
POLICY_TOOLS = frozenset({"search_policies"})
# Each kind of claim and the tools that can back it. A price must also match a number in the results.
CLAIM_SOURCES = (
    (PRICE, DATA_TOOLS | POLICY_TOOLS),
    (STOCK, DATA_TOOLS),
    (HAS_PRODUCT, DATA_TOOLS),
    (AVAILABLE, DATA_TOOLS | {"get_store_status"}),
    (DOES_NOT_SELL, DATA_TOOLS | POLICY_TOOLS),
    (DELIVERY, DATA_TOOLS | POLICY_TOOLS),
)
GROUNDING_REMINDER = (
    "[internal check] Your last answer stated prices, stock, delivery times or what the store sells without "
    "a matching tool result in this turn. Call the tool that has that data first (search_products or "
    "get_product_details for products, get_shipping_quote for delivery), then answer using only what it "
    "returns, in the customer's language."
)


def _to_number(raw: str) -> float:
    """Read 3.999,00 / 2,089.05 / 2089.05 / 3.999 as the same kind of BRL amount."""
    last_sep = max(raw.rfind(","), raw.rfind("."))
    has_cents = last_sep != -1 and len(raw) - last_sep - 1 in (1, 2)
    if not has_cents:
        return float(re.sub(r"[.,]", "", raw))
    whole, cents = raw[:last_sep], raw[last_sep + 1 :]
    return float(re.sub(r"[.,]", "", whole) + "." + cents)


def prices_in(text: str) -> list[float]:
    return [_to_number(m) for m in PRICE_VALUE.findall(text)]


def unsupported_prices(answer: str, tool_results: list[str], customer_text: str = "") -> list[float]:
    """Prices in the answer that come neither from this turn's tools nor from the customer (e.g. a budget)."""
    known = {float(n) for result in tool_results for n in NUMBER.findall(result)}
    known |= set(prices_in(customer_text))
    known |= {float(n) for n in NUMBER.findall(customer_text)}
    stated = prices_in(answer)
    return [p for p in stated if not any(abs(p - k) <= PRICE_TOLERANCE for k in known)]


def needs_grounding(
    answer: str, tools_used: set[str], tool_results: list[str] | None = None, customer_text: str = ""
) -> bool:
    if any(pattern.search(answer) and not tools_used & sources for pattern, sources in CLAIM_SOURCES):
        return True
    if PRICE.search(answer) and tool_results is not None:
        return bool(unsupported_prices(answer, tool_results, customer_text))
    return False
