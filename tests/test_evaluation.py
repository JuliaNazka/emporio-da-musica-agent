import re

from emporio.evaluation import GROUPS, check_turn, load_cases, score_case, summarize

KNOWN_EXPECTATIONS = {
    "must_call", "must_not_call", "escalate", "contains", "not_contains",
    "matches", "not_matches", "prices", "forbidden_prices",
}


def failed(checks):
    return [c.name for c in checks if not c.passed]


def test_tool_checks_accept_any_of_a_list():
    expect = {"must_call": [["get_product_details", "search_products"]], "must_not_call": ["get_order_status"]}
    assert failed(check_turn(["search_products"], "", expect)) == []
    assert failed(check_turn(["get_order_status"], "", expect)) == [
        "calls get_product_details or search_products",
        "does not call get_order_status",
    ]


def test_escalation_check_works_both_ways():
    assert failed(check_turn(["escalate_to_human"], "", {"escalate": True})) == []
    assert failed(check_turn([], "", {"escalate": True})) == ["hands off to the team"]
    assert failed(check_turn(["escalate_to_human"], "", {"escalate": False})) == ["does not hand off"]


def test_text_checks_ignore_case_and_accents():
    reply = "Essa promoção já ACABOU. Não temos mais o desconto."
    expect = {"contains": [["encerrada", "acabou"]], "not_contains": ["nao temos"], "matches": ["\\bacabou\\b"]}
    assert failed(check_turn([], reply, expect)) == ["never says nao temos"]


def test_price_checks_read_brazilian_and_plain_formats():
    reply = "Hoje sai por R$ 2.199,00, ou R$ 2089.05 no PIX."
    assert failed(check_turn([], reply, {"prices": [2199, 2089.05], "forbidden_prices": [1935.12]})) == []
    assert failed(check_turn([], "Fica R$ 1.935,12", {"forbidden_prices": [1935.12]})) == ["never states R$ 1935.12"]


def test_score_and_summary():
    cases = [
        {"id": "a", "group": "happy_path", "turns": [{"customer": "x", "expect": {"must_call": ["search_products"]}}]},
        {"id": "b", "group": "happy_path", "turns": [{"customer": "y", "expect": {"escalate": True}}]},
        {"id": "c", "group": "lgpd", "turns": [{"customer": "z", "expect": {"not_contains": ["Ana"]}}]},
    ]
    traces = {
        "a": [{"customer": "x", "reply": "ok", "tool_calls": [{"tool": "search_products"}], "review_retries": 0, "seconds": 10}],
        "b": [{"customer": "y", "reply": "passei", "tool_calls": [{"tool": "escalate_to_human"}],
               "review_retries": 1, "seconds": 20, "code_handoff": True}],
        "c": [{"customer": "z", "reply": "Oi Ana", "tool_calls": [], "review_retries": 0, "seconds": 30, "fallback": True}],
    }
    results = [score_case(case, traces[case["id"]]) for case in cases]
    summary = summarize(results, cases)

    assert [r.passed for r in results] == [True, True, False]
    assert summary["rules_respected"] == 66.7
    assert summary["accuracy"] == 66.7
    assert summary["resolved_without_human"] == 33.3  # b went to the team, c fell back
    assert summary["handoff_rate"] == 33.3
    assert summary["handoffs_when_expected"] == "1/1"
    assert summary["handoffs_done_by_code"] == 1
    assert summary["fallback_answers"] == 1
    assert summary["review_retries"] == 1
    assert summary["seconds_per_turn"] == 20.0
    assert summary["groups"]["lgpd"] == {"cases": 1, "cases_passed": 0, "rules_respected": 0.0}


def test_eval_set_is_well_formed():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    assert {c["group"] for c in cases} == set(GROUPS)
    for case in cases:
        assert case["turns"] and case.get("note")
        for turn in case["turns"]:
            assert set(turn.get("expect", {})) <= KNOWN_EXPECTATIONS, case["id"]
            for pattern in turn.get("expect", {}).get("matches", []) + turn.get("expect", {}).get("not_matches", []):
                re.compile(pattern)
