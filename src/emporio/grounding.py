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
GROUNDING_REMINDER = (
    "[internal check] Your last answer stated prices or stock that do not come from a store data "
    "tool result in this turn. Call the right tool first, then answer using only the values it returns."
)


def mentions_store_data(answer: str) -> bool:
    return bool(PRICE.search(answer) or STOCK.search(answer))


def _to_number(raw: str) -> float:
    """Read 3.999,00 / 2,089.05 / 2089.05 / 3.999 as the same kind of BRL amount."""
    last_sep = max(raw.rfind(","), raw.rfind("."))
    has_cents = last_sep != -1 and len(raw) - last_sep - 1 in (1, 2)
    if not has_cents:
        return float(re.sub(r"[.,]", "", raw))
    whole, cents = raw[:last_sep], raw[last_sep + 1 :]
    return float(re.sub(r"[.,]", "", whole) + "." + cents)


def unsupported_prices(answer: str, tool_results: list[str]) -> list[float]:
    """Prices in the answer that no tool result of this turn contains."""
    known = {float(n) for result in tool_results for n in NUMBER.findall(result)}
    stated = [_to_number(m) for m in PRICE_VALUE.findall(answer)]
    return [p for p in stated if not any(abs(p - k) <= PRICE_TOLERANCE for k in known)]


def needs_grounding(answer: str, tools_used: set[str], tool_results: list[str] | None = None) -> bool:
    if not mentions_store_data(answer):
        return False
    if not tools_used & DATA_TOOLS:
        return True
    return tool_results is not None and bool(unsupported_prices(answer, tool_results))
