from datetime import date, datetime

import pytest

from emporio import rules, tools
from emporio.data import load_store_data


@pytest.fixture(scope="module")
def data():
    return load_store_data()


def names(result):
    return [p["name"] for p in result["products"]]


def test_search_acoustic_guitars_up_to_1000(data):
    result = tools.search_products(data, query="violão", max_price=1000)
    assert result["total_found"] > 0
    assert all(p["list_price"] <= 1000 for p in result["products"])
    # The baritone ukulele's description says it is tuned like a violão.
    assert {p["category"] for p in result["products"]} <= {"Violões", "Ukuleles"}


def test_search_hides_out_of_stock_by_default(data):
    assert "Giannini GF-3D Dreadnought Sunburst" not in names(tools.search_products(data, query="giannini"))


def test_search_by_category_name(data):
    result = tools.search_products(data, category="ukulele")
    assert result["total_found"] > 0
    assert all(p["category"] == "Ukuleles" for p in result["products"])


def test_product_details_with_expired_promotion(data):
    details = tools.get_product_details(data, "Takamine GD20")
    assert details["list_price"] == 2199.0
    assert details["pix_price"] == 2089.05
    assert "promotion" not in details
    assert details["expired_promotions"] == ["Oferta Especial"]


def test_product_details_with_active_promotion(data):
    details = tools.get_product_details(data, "Taylor 110e")
    assert details["promotion"]["discount_percent"] == 8
    assert details["pix_price"] == details["promotion"]["promo_price"]


def test_out_of_stock_product_gets_alternatives(data):
    details = tools.get_product_details(data, "Giannini GF-3D")
    assert details["availability"] == tools.OUT_OF_STOCK
    assert details["alternatives"]
    assert all(a["stock_quantity"] > 0 for a in details["alternatives"])


def test_unknown_product(data):
    assert not tools.get_product_details(data, "Stradivarius 1715")["found"]


def test_order_needs_matching_contact(data):
    wrong_phone = tools.get_order_status(data, 8, "(67) 90000-0000")
    missing_order = tools.get_order_status(data, 999, "anacarol.ferreira@coldmail.com")
    assert not wrong_phone["verified"]
    assert not missing_order["verified"]
    assert wrong_phone["message"] == missing_order["message"]


def test_order_status_with_phone(data):
    order = tools.get_order_status(data, 8, "67 99654-7821")
    assert order["verified"]
    assert order["status"] == "enviado"
    assert order["tracking_code"] == "BRJL5544332BR"


def test_order_status_with_email(data):
    assert tools.get_order_status(data, 8, "AnaCarol.Ferreira@coldmail.com")["verified"]


def test_returns_closed_for_old_delivered_order(data):
    result = tools.check_return_options(data, 5, "rafael.pereira@jmail.com", today=date(2026, 11, 18))
    assert result["assumed_received_on"] == "2026-01-18"
    assert not result["change_of_mind_open"]
    assert not result["legal_warranty_open"]


def test_returns_inside_window(data):
    result = tools.check_return_options(data, 5, "rafael.pereira@jmail.com", today=date(2026, 1, 22))
    assert result["change_of_mind_open"]


def test_shipped_order_not_received_yet(data):
    result = tools.check_return_options(data, 8, "anacarol.ferreira@coldmail.com", today=date(2026, 3, 1))
    assert result["received"] is False


def test_active_promotions_list(data):
    promos = tools.list_active_promotions(data)["promotions"]
    assert {p["product_id"] for p in promos} == {90, 94, 121, 127}


def test_escalated_complaint_promises_24_business_hours():
    wednesday = datetime(2026, 11, 18, 15, 0, tzinfo=rules.STORE_TZ)
    ticket = tools.escalate_to_human("complaint", "Violão chegou com o tampo riscado", now=wednesday)
    assert ticket["escalated"]
    assert ticket["team_available_now"]
    assert ticket["response_time"] == tools.COMPLAINT_RESPONSE_TIME
    assert ticket["contact"]["whatsapp"] == tools.STORE_WHATSAPP


def test_escalation_outside_hours_gives_next_opening():
    sunday = datetime(2026, 11, 22, 10, 0, tzinfo=rules.STORE_TZ)
    ticket = tools.escalate_to_human("customer_request", "Quer falar com um vendedor", now=sunday)
    assert not ticket["team_available_now"]
    assert ticket["next_opening"] == "2026-11-23 09:00"
    assert ticket["response_time"] == tools.CLOSED_RESPONSE_TIME


def test_defect_is_handled_like_a_complaint():
    ticket = tools.escalate_to_human("defect", "Tampo trincado")
    assert ticket["response_time"] == tools.COMPLAINT_RESPONSE_TIME


def test_escalation_rejects_unknown_reason():
    assert not tools.escalate_to_human("bored", "?")["escalated"]


def test_empty_search_within_budget_shows_the_cheapest_above_it(data):
    result = tools.search_products(data, query="violão aço", max_price=500)
    assert result["total_found"] == 0
    cheapest = result["cheapest_above_max_price"]
    assert cheapest[0]["name"] == "Shelby SN-100 Folk Aço Preto"
    assert all(p["list_price"] > 500 for p in cheapest)


def test_search_with_no_word_match_and_a_category_returns_nothing(data):
    assert tools.search_products(data, query="xyzzy", category="violões")["total_found"] == 0


def test_a_brand_alone_lists_the_candidates(data):
    details = tools.get_product_details(data, "Yamaha")
    assert details["ambiguous"] and not details["found"]
    assert len(details["candidates"]) > 1


def test_exact_name_wins_over_partial_matches(data):
    details = tools.get_product_details(data, "Yamaha C40 Nylon Natural")
    assert details["found"] and "note" not in details


def test_similar_name_is_flagged_for_confirmation(data):
    details = tools.get_product_details(data, "Takamine GD-20 Natural")
    assert not details["found"] or "note" in details or details["name"].startswith("Takamine GD20")


def test_model_code_does_not_match_a_longer_code(data):
    assert tools.get_product_details(data, "Martin D-28")["name"] == "Martin D-28 Dreadnought Natural"
    assert tools.get_product_details(data, "Martin D-28E")["name"] == "Martin D-28E Elétrico Aço Natural"
