"""Score agent runs against the expected behaviour in evals/cases.json.

Every check is deterministic: which tools ran, what the reply says, which prices it states and whether the
case went to the store team. No model judges another model, so a run can be repeated and compared.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from emporio.data import DEFAULT_DATA_DIR, normalize_text
from emporio.escalation import ESCALATION_TOOL
from emporio.grounding import PRICE_TOLERANCE, prices_in

CASES_FILE = DEFAULT_DATA_DIR.parent / "evals" / "cases.json"
GROUPS = {
    "happy_path": "Happy path",
    "expired_promo": "Expired promotion",
    "lgpd": "LGPD (wrong or missing contact)",
    "out_of_scope": "Out of scope",
    "manipulation": "Manipulation attempt",
}


@dataclass
class Check:
    name: str
    passed: bool


@dataclass
class TurnResult:
    customer: str
    reply: str
    tools: list[str]
    checks: list[Check] = field(default_factory=list)


@dataclass
class CaseResult:
    case_id: str
    group: str
    turns: list[TurnResult]
    escalated: bool
    code_handoff: bool
    fallback: bool
    retries: int
    seconds: float

    @property
    def checks(self) -> list[Check]:
        return [check for turn in self.turns for check in turn.checks]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _options(item: str | list[str]) -> list[str]:
    return [item] if isinstance(item, str) else item


def _has_price(reply: str, price: float) -> bool:
    return any(abs(p - price) <= PRICE_TOLERANCE for p in prices_in(reply))


def check_turn(tools: list[str], reply: str, expect: dict) -> list[Check]:
    """tools: names of the tools called in this turn, in order. Items given as a list mean 'any of'."""
    text = normalize_text(reply)
    checks = []
    for item in expect.get("must_call", []):
        options = _options(item)
        checks.append(Check(f"calls {' or '.join(options)}", any(t in tools for t in options)))
    for name in expect.get("must_not_call", []):
        checks.append(Check(f"does not call {name}", name not in tools))
    if "escalate" in expect:
        escalated = ESCALATION_TOOL in tools
        checks.append(Check("hands off to the team" if expect["escalate"] else "does not hand off", escalated == expect["escalate"]))
    for item in expect.get("contains", []):
        options = _options(item)
        checks.append(Check(f"says {' / '.join(options)}", any(normalize_text(o) in text for o in options)))
    for phrase in expect.get("not_contains", []):
        checks.append(Check(f"never says {phrase}", normalize_text(phrase) not in text))
    for pattern in expect.get("matches", []):
        checks.append(Check(f"matches /{pattern}/", re.search(pattern, text) is not None))
    for pattern in expect.get("not_matches", []):
        checks.append(Check(f"never matches /{pattern}/", re.search(pattern, text) is None))
    for price in expect.get("prices", []):
        checks.append(Check(f"states R$ {price:.2f}", _has_price(reply, price)))
    for price in expect.get("forbidden_prices", []):
        checks.append(Check(f"never states R$ {price:.2f}", not _has_price(reply, price)))
    return checks


def score_case(case: dict, turn_traces: list[dict]) -> CaseResult:
    """turn_traces: one record per customer message, as written by tracing.turn_trace plus the
    'code_handoff' and 'fallback' flags."""
    turns = []
    for spec, trace in zip(case["turns"], turn_traces):
        tools = [call["tool"] for call in trace["tool_calls"]]
        checks = check_turn(tools, trace["reply"], spec.get("expect", {}))
        turns.append(TurnResult(trace["customer"], trace["reply"], tools, checks))
    return CaseResult(
        case_id=case["id"],
        group=case["group"],
        turns=turns,
        escalated=any(ESCALATION_TOOL in t.tools for t in turns),
        code_handoff=any(t.get("code_handoff") for t in turn_traces),
        fallback=any(t.get("fallback") for t in turn_traces),
        retries=sum(t["review_retries"] for t in turn_traces),
        seconds=sum(t["seconds"] for t in turn_traces),
    )


def _share(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def summarize(results: list[CaseResult], cases: list[dict]) -> dict:
    expects_handoff = {
        case["id"] for case in cases if any(turn.get("expect", {}).get("escalate") for turn in case["turns"])
    }
    groups = {}
    by_group = defaultdict(list)
    for result in results:
        by_group[result.group].append(result)
    for group, items in by_group.items():
        checks = [c for r in items for c in r.checks]
        groups[group] = {
            "cases": len(items),
            "cases_passed": sum(r.passed for r in items),
            "rules_respected": _share(sum(c.passed for c in checks), len(checks)),
        }

    all_checks = [c for r in results for c in r.checks]
    handoff_cases = [r for r in results if r.case_id in expects_handoff]
    turns = sum(len(r.turns) for r in results)
    return {
        "cases": len(results),
        "turns": turns,
        "rules_respected": _share(sum(c.passed for c in all_checks), len(all_checks)),
        "accuracy": _share(sum(r.passed for r in results), len(results)),
        "resolved_without_human": _share(sum(not r.escalated and not r.fallback for r in results), len(results)),
        "handoff_rate": _share(sum(r.escalated for r in results), len(results)),
        "handoffs_when_expected": f"{sum(r.escalated for r in handoff_cases)}/{len(handoff_cases)}",
        "handoffs_done_by_code": sum(r.code_handoff for r in results),
        "fallback_answers": sum(r.fallback for r in results),
        "review_retries": sum(r.retries for r in results),
        "seconds_per_turn": round(sum(r.seconds for r in results) / turns, 1) if turns else 0.0,
        "groups": groups,
    }
