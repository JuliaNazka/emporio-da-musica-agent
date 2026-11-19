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
STOCK = re.compile(r"\b(em estoque|sem estoque|esgotad[oa]s?|unidades? dispon[ií]ve(l|is)|in stock|out of stock)\b", re.IGNORECASE)
GROUNDING_REMINDER = (
    "[internal check] Your last answer stated prices or stock without a store data tool result "
    "in this turn. Call the right tool first, then answer using only what it returns."
)


def mentions_store_data(answer: str) -> bool:
    return bool(PRICE.search(answer) or STOCK.search(answer))


def needs_grounding(answer: str, tools_used: set[str]) -> bool:
    return mentions_store_data(answer) and not (tools_used & DATA_TOOLS)
