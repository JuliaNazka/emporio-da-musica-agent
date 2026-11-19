"""Load the store's CSV files into clean tables and report data quality issues."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FILE_PREFIX = "desafio_tecnico_ai_eng - "
TOTAL_TOLERANCE_BRL = 0.01


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text))
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.lower().split())


def digits_only(text: str) -> str:
    return "".join(ch for ch in str(text) if ch.isdigit())


@dataclass(frozen=True)
class StoreData:
    products: pd.DataFrame
    categories: pd.DataFrame
    customers: pd.DataFrame
    orders: pd.DataFrame
    order_items: pd.DataFrame
    active_promotions: pd.DataFrame
    all_promotions: pd.DataFrame


def _read(data_dir: Path, table: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{FILE_PREFIX}{table}.csv", encoding="utf-8")


def _clean_categories(raw: pd.DataFrame) -> pd.DataFrame:
    return raw.rename(columns={"name": "category_name", "description": "category_description"})


def _clean_products(raw: pd.DataFrame, categories: pd.DataFrame) -> pd.DataFrame:
    products = raw.copy()
    products["name"] = products["name"].str.strip()
    products["specs"] = products["specs"].apply(json.loads)
    products["created_at"] = pd.to_datetime(products["created_at"])
    products = products.merge(
        categories[["category_id", "category_name"]], on="category_id", how="left"
    )
    products["search_text"] = (
        products["name"] + " " + products["category_name"] + " " + products["description"]
    ).map(normalize_text)
    return products.sort_values("product_id").reset_index(drop=True)


def _clean_customers(raw: pd.DataFrame) -> pd.DataFrame:
    customers = raw.copy()
    customers["phone_digits"] = customers["phone"].map(digits_only)
    customers["email"] = customers["email"].str.strip().str.lower()
    return customers


def _split_payment_method(code: str) -> tuple[str, int]:
    """'credit_6x' -> ('credit', 6); 'pix' -> ('pix', 1)."""
    if code.startswith("credit_") and code.endswith("x"):
        return "credit", int(code.removeprefix("credit_").removesuffix("x"))
    return code, 1


def _clean_orders(raw: pd.DataFrame, items: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    orders = raw.copy()
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    orders["estimated_delivery"] = pd.to_datetime(orders["estimated_delivery"])
    orders["tracking_code"] = orders["tracking_code"].fillna("")
    orders["notes"] = orders["notes"].fillna("")

    split = orders["payment_method"].map(_split_payment_method)
    orders["payment_type"] = split.map(lambda pair: pair[0])
    orders["installments"] = split.map(lambda pair: pair[1])

    # The recorded total is what the customer paid, so it stays the source of truth.
    priced = items.merge(products[["product_id", "price_brl"]], on="product_id", how="left")
    priced["line_total"] = priced["quantity"] * priced["price_brl"]
    items_total = priced.groupby("order_id")["line_total"].sum().rename("items_total_brl")
    orders = orders.merge(items_total, on="order_id", how="left")
    difference = (orders["total_brl"] - orders["items_total_brl"]).abs()
    orders["total_differs_from_items"] = difference > TOTAL_TOLERANCE_BRL
    return orders


def _active_promotions(promotions: pd.DataFrame) -> pd.DataFrame:
    """Promotions do not stack (policy 6.2): one active promotion per product, the largest."""
    active = promotions[promotions["is_active"] == 1]
    active = active.sort_values("discount_percent", ascending=False)
    return active.drop_duplicates(subset="product_id", keep="first").reset_index(drop=True)


def load_store_data(data_dir: Path = DEFAULT_DATA_DIR) -> StoreData:
    categories = _clean_categories(_read(data_dir, "categories"))
    products = _clean_products(_read(data_dir, "products"), categories)
    order_items = _read(data_dir, "order_items")
    promotions = _read(data_dir, "promotions")
    return StoreData(
        products=products,
        categories=categories,
        customers=_clean_customers(_read(data_dir, "customers")),
        orders=_clean_orders(_read(data_dir, "orders"), order_items, products),
        order_items=order_items,
        active_promotions=_active_promotions(promotions),
        all_promotions=promotions,
    )


def data_quality_report(data: StoreData) -> list[str]:
    issues: list[str] = []
    products = data.products

    empty = data.categories[~data.categories["category_id"].isin(products["category_id"])]
    for name in empty["category_name"]:
        issues.append(f"Category without products: {name}")

    out_of_stock = products[(products["status"] == "active") & (products["stock_quantity"] == 0)]
    for name in out_of_stock["name"]:
        issues.append(f"Active product with zero stock: {name}")

    not_for_sale = products[products["status"] != "active"]
    ordered = data.order_items.merge(not_for_sale, on="product_id")
    for _, row in ordered.iterrows():
        issues.append(f"Order {row.order_id} contains a '{row.status}' product: {row['name']}")

    mismatched = data.orders[data.orders["total_differs_from_items"]]
    for _, row in mismatched.iterrows():
        issues.append(
            f"Order {row.order_id}: recorded total R$ {row.total_brl:.2f} "
            f"vs items at list price R$ {row.items_total_brl:.2f}"
        )

    repeated = data.all_promotions["product_id"].value_counts()
    for product_id in repeated[repeated > 1].index:
        issues.append(f"Product {product_id} has {repeated[product_id]} promotion rows")

    return issues
