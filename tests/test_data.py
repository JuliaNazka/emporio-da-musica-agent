import pytest

from emporio.data import data_quality_report, digits_only, load_store_data, normalize_text


@pytest.fixture(scope="module")
def data():
    return load_store_data()


def test_all_tables_load_with_expected_sizes(data):
    assert len(data.products) == 65
    assert len(data.customers) == 50
    assert len(data.orders) == 20
    assert len(data.categories) == 9


def test_accents_survive_loading(data):
    names = set(data.products["name"])
    assert "Tagima Dallas Tuner Aço Natural" in names


def test_specs_are_parsed_into_dicts(data):
    specs = data.products.set_index("product_id").loc[81, "specs"]
    assert specs["top"] == "Spruce"


def test_normalize_text_removes_accents_and_case():
    assert normalize_text("  Violão  ELÉTRICO ") == "violao eletrico"


def test_digits_only_normalizes_phone():
    assert digits_only("(67) 99812-3456") == "67998123456"


def test_payment_method_is_split_into_type_and_installments(data):
    order = data.orders.set_index("order_id").loc[2]
    assert order["payment_type"] == "credit"
    assert order["installments"] == 6


def test_only_active_promotions_are_kept(data):
    assert set(data.active_promotions["product_id"]) == {90, 94, 121, 127}


def test_recorded_total_is_kept_and_mismatch_is_flagged(data):
    order = data.orders.set_index("order_id").loc[3]
    assert order["total_brl"] == 3450
    assert order["items_total_brl"] == 3498
    assert order["total_differs_from_items"]


def test_quality_report_lists_known_issues(data):
    report = "\n".join(data_quality_report(data))
    assert "Category without products: Cordas Orquestrais" in report
    assert "Active product with zero stock: Giannini GF-3D Dreadnought Sunburst" in report
    assert "'coming_soon' product" in report
