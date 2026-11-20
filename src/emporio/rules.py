"""Store business rules from the policy manual, in pure Python (no pandas, no LLM)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

PIX_DISCOUNT = 0.05  # policy 3

# Policy 3.1 wins over the summary table in section 3 (documented assumption).
INSTALLMENT_MINIMUMS = ((3, 50.0), (6, 80.0), (12, 100.0))
MAX_INSTALLMENTS = 12
SPLIT_PAYMENT_THRESHOLD = 2000.0  # policy 3.1

CHANGE_OF_MIND_DAYS = 7  # policy 4.1, from receipt
PREFERENCE_EXCHANGE_DAYS = 7  # policy 4.3, from receipt
DEFECT_EXCHANGE_DAYS = 30  # policy 4.2, from purchase
LEGAL_WARRANTY_DAYS = 90  # policy 8.1, from receipt

METRO_CITY = "campo grande"  # policy 5.1
FREE_SHIPPING_ABOVE = 500.0
METRO_SHIPPING_FEE = 35.0

STORE_TZ = timezone(timedelta(hours=-4))  # Campo Grande/MS, no daylight saving
WEEKDAY_HOURS = (time(9), time(18))  # policy 2
SATURDAY_HOURS = (time(9), time(13))
OPENING_HOURS = {**{day: WEEKDAY_HOURS for day in range(5)}, 5: SATURDAY_HOURS}


@dataclass(frozen=True)
class PriceQuote:
    list_price: float
    promo_percent: float | None
    promo_price: float | None
    pix_price: float

    @property
    def best_card_price(self) -> float:
        return self.promo_price if self.promo_price is not None else self.list_price


def quote_price(list_price: float, promo_percent: float | None = None) -> PriceQuote:
    """Discounts do not stack (6.2): with a promotion, PIX pays the promotional price."""
    if promo_percent:
        promo_price = round(list_price * (1 - promo_percent / 100), 2)
        return PriceQuote(list_price, promo_percent, promo_price, pix_price=promo_price)
    pix_price = round(list_price * (1 - PIX_DISCOUNT), 2)
    return PriceQuote(list_price, None, None, pix_price=pix_price)


def minimum_installment(installments: int) -> float:
    for max_installments, minimum in INSTALLMENT_MINIMUMS:
        if installments <= max_installments:
            return minimum
    raise ValueError(f"More than {MAX_INSTALLMENTS} installments is not allowed")


def installment_options(amount: float) -> list[dict]:
    options = [{"installments": 1, "value": round(amount, 2)}]
    for n in range(2, MAX_INSTALLMENTS + 1):
        value = round(amount / n, 2)
        if value >= minimum_installment(n):
            options.append({"installments": n, "value": value})
    return options


def can_split_payment(amount: float) -> bool:
    return amount > SPLIT_PAYMENT_THRESHOLD


def shipping_quote(city: str, amount: float | None = None) -> dict:
    """Without an amount the metro fee is the standard one, with the free-shipping threshold alongside."""
    if city.strip().lower() == METRO_CITY:
        fee = 0.0 if amount is not None and amount > FREE_SHIPPING_ABOVE else METRO_SHIPPING_FEE
        return {
            "region": "metro",
            "fee": fee,
            "free_shipping_above": FREE_SHIPPING_ABOVE,
            "delivery_days": "1 a 3 dias úteis",
            "carrier": "motoboy próprio",
        }
    return {
        "region": "other_city",
        "fee": None,
        "fee_note": "calculado pelo CEP, peso e dimensões; instrumentos grandes podem pedir cotação especial",
        "options": {"PAC": "5 a 12 dias úteis", "SEDEX": "2 a 5 dias úteis", "Jadlog": "3 a 8 dias úteis"},
    }


def return_windows(order_date: date, received_on: date | None, today: date) -> dict:
    days_since_purchase = (today - order_date).days
    windows = {
        "days_since_purchase": days_since_purchase,
        "defect_exchange_open": days_since_purchase <= DEFECT_EXCHANGE_DAYS,
    }
    if received_on is None:
        windows.update(
            received=False,
            change_of_mind_open=None,
            preference_exchange_open=None,
            legal_warranty_open=None,
        )
        return windows
    days_since_receipt = (today - received_on).days
    windows.update(
        received=True,
        days_since_receipt=days_since_receipt,
        change_of_mind_open=days_since_receipt <= CHANGE_OF_MIND_DAYS,
        preference_exchange_open=days_since_receipt <= PREFERENCE_EXCHANGE_DAYS,
        legal_warranty_open=days_since_receipt <= LEGAL_WARRANTY_DAYS,
    )
    return windows


def store_status(now: datetime) -> dict:
    """Holidays are not in the data, so only the weekly schedule is applied."""
    local = now.astimezone(STORE_TZ)
    hours = OPENING_HOURS.get(local.weekday())
    if hours and hours[0] <= local.time() < hours[1]:
        return {"open_now": True, "closes_at": hours[1].strftime("%H:%M")}
    opens_later_today = hours is not None and local.time() < hours[0]
    day = local.date() if opens_later_today else local.date() + timedelta(days=1)
    while day.weekday() not in OPENING_HOURS:
        day += timedelta(days=1)
    opening = OPENING_HOURS[day.weekday()][0]
    return {"open_now": False, "next_opening": f"{day.isoformat()} {opening:%H:%M}"}
