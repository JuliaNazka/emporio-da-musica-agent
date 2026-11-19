from datetime import date, datetime

from emporio import rules


def test_pix_takes_five_percent_off_list_price():
    quote = rules.quote_price(2199.0)
    assert quote.pix_price == 2089.05
    assert quote.promo_price is None


def test_pix_does_not_stack_on_promotion():
    quote = rules.quote_price(5999.0, promo_percent=8)
    assert quote.promo_price == 5519.08
    assert quote.pix_price == quote.promo_price


def test_installments_respect_minimum_values():
    plans = {p["installments"]: p["value"] for p in rules.installment_options(600.0)}
    assert 3 in plans
    assert 6 in plans
    assert 7 not in plans
    assert max(plans) == 6


def test_twelve_installments_need_at_least_1200():
    assert max(p["installments"] for p in rules.installment_options(1200.0)) == 12
    assert max(p["installments"] for p in rules.installment_options(1199.0)) == 11


def test_cheap_item_has_no_installments():
    assert rules.installment_options(90.0) == [{"installments": 1, "value": 90.0}]


def test_split_payment_only_above_2000():
    assert rules.can_split_payment(2000.01)
    assert not rules.can_split_payment(2000.0)


def test_metro_shipping_is_free_above_500():
    assert rules.shipping_quote("Campo Grande", 600)["fee"] == 0.0
    assert rules.shipping_quote("Campo Grande", 500)["fee"] == 35.0


def test_other_cities_have_no_fixed_fee():
    assert rules.shipping_quote("Dourados", 600)["fee"] is None


def test_return_windows_inside_seven_days():
    windows = rules.return_windows(date(2026, 1, 5), date(2026, 1, 18), today=date(2026, 1, 22))
    assert windows["change_of_mind_open"]
    assert windows["defect_exchange_open"]


def test_return_windows_after_thirty_days():
    windows = rules.return_windows(date(2026, 1, 5), date(2026, 1, 18), today=date(2026, 3, 1))
    assert not windows["change_of_mind_open"]
    assert not windows["defect_exchange_open"]
    assert windows["legal_warranty_open"]


def test_store_open_on_weekday_afternoon():
    wednesday = datetime(2026, 11, 18, 15, 0, tzinfo=rules.STORE_TZ)
    assert rules.store_status(wednesday)["open_now"]


def test_store_closed_saturday_afternoon_opens_monday():
    saturday = datetime(2026, 11, 21, 14, 0, tzinfo=rules.STORE_TZ)
    status = rules.store_status(saturday)
    assert not status["open_now"]
    assert status["next_opening"] == "2026-11-23 09:00"


def test_store_closed_before_opening_opens_same_day():
    early_monday = datetime(2026, 11, 23, 7, 30, tzinfo=rules.STORE_TZ)
    assert rules.store_status(early_monday)["next_opening"] == "2026-11-23 09:00"
