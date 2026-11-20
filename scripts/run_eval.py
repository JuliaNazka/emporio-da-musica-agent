"""Run the evaluation set (evals/cases.json) against a model and write a report.

Usage:
    python scripts/run_eval.py --model ollama:qwen3:4b-instruct [--only order-status wrong-phone] [--conversations]

Writes evals/results/<model>.json and .md, and refreshes the side-by-side table in evals/results/README.md.
--conversations also saves the example transcripts listed in EXAMPLES to conversations/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from emporio import rules
from emporio.agent import DEFAULT_MODEL, FALLBACK_ANSWER, build_agent, load_model, model_settings
from emporio.evaluation import GROUPS, load_cases, score_case, summarize
from emporio.tracing import turn_trace

RESULTS_DIR = ROOT / "evals" / "results"
CONVERSATIONS_DIR = ROOT / "conversations"
WEDNESDAY_AFTERNOON = datetime(2026, 11, 18, 15, 0, tzinfo=rules.STORE_TZ)
EXAMPLES = {
    "budget-search": ("01-budget-search", "Budget search with a follow-up question"),
    "order-status": ("02-order-status", "Order status after contact verification"),
    "expired-oferta-especial": ("03-expired-promotion", "Expired promotion"),
    "defect-handoff": ("04-defect-handoff", "Defect handed to the store team"),
    "wrong-phone": ("05-lgpd-wrong-contact", "Wrong contact (LGPD)"),
}


def run_case(agent, case: dict, model_name: str) -> list[dict]:
    config = {"configurable": {"thread_id": case["id"]}}
    traces = []
    for turn in case["turns"]:
        before = len(agent.get_state(config).values.get("messages", []))
        start = time.time()
        result = agent.invoke({"messages": [("user", turn["customer"])]}, config)
        new_messages = result["messages"][before:]
        trace = turn_trace(case["id"], model_name, new_messages, time.time() - start)
        trace["code_handoff"] = any(
            call["id"].startswith("guard-") for m in new_messages if isinstance(m, AIMessage) for call in m.tool_calls
        )
        trace["fallback"] = trace["reply"] == FALLBACK_ANSWER
        traces.append(trace)
    return traces


def slug(model_name: str) -> str:
    return model_name.replace(":", "-").replace("/", "-")


def report_markdown(model_name: str, summary: dict, results, cases_by_id: dict) -> str:
    lines = [
        f"# Evaluation: `{model_name}`",
        "",
        f"Run on {datetime.now(rules.STORE_TZ):%Y-%m-%d %H:%M}, store clock fixed at {WEDNESDAY_AFTERNOON:%A %Y-%m-%d %H:%M} (open).",
        f"{summary['cases']} cases, {summary['turns']} customer messages. Every check is deterministic (see `evals/cases.json`).",
        "",
        "## KPIs",
        "",
        f"- **Rules respected:** {summary['rules_respected']}% of all checks",
        f"- **Accuracy (cases with every check passed):** {summary['accuracy']}%",
        f"- **Resolved without a human:** {summary['resolved_without_human']}%",
        f"- **Hand-off rate:** {summary['handoff_rate']}% (expected hand-offs done: {summary['handoffs_when_expected']}, "
        f"done by the code guard: {summary['handoffs_done_by_code']})",
        f"- **Fallback answers (unconfirmed data or model error):** {summary['fallback_answers']}, review retries: {summary['review_retries']}",
        f"- **Average time per message:** {summary['seconds_per_turn']} s",
        "",
        "## By group",
        "",
        "| Group | Cases passed | Rules respected |",
        "|---|---|---|",
    ]
    for group, label in GROUPS.items():
        stats = summary["groups"].get(group)
        if stats:
            lines.append(f"| {label} | {stats['cases_passed']}/{stats['cases']} | {stats['rules_respected']}% |")

    failed = [r for r in results if not r.passed]
    lines += ["", "## Failed checks", ""]
    if not failed:
        lines.append("None.")
    for result in failed:
        lines.append(f"### `{result.case_id}` ({GROUPS[result.group]})")
        lines.append(f"_{cases_by_id[result.case_id].get('note', '')}_")
        lines.append("")
        for turn in result.turns:
            bad = [c.name for c in turn.checks if not c.passed]
            if not bad:
                continue
            lines.append(f"- Customer: {turn.customer}")
            lines.append(f"  - Tools: {', '.join(turn.tools) or 'none'}")
            lines.append(f"  - Failed: {'; '.join(bad)}")
            lines.append(f"  - Reply: {' '.join(turn.reply.split())}")
        lines.append("")
    return "\n".join(lines)


def write_comparison() -> None:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS_DIR.glob("*.json"))]
    if not runs:
        return
    header = "| Metric | " + " | ".join(f"`{r['model']}`" for r in runs) + " |"
    rows = [
        ("Rules respected", "rules_respected", "%"),
        ("Accuracy", "accuracy", "%"),
        ("Resolved without a human", "resolved_without_human", "%"),
        ("Hand-off rate", "handoff_rate", "%"),
        ("Expected hand-offs done", "handoffs_when_expected", ""),
        ("Hand-offs done by the code guard", "handoffs_done_by_code", ""),
        ("Fallback answers", "fallback_answers", ""),
        ("Seconds per message", "seconds_per_turn", ""),
    ]
    lines = [
        "# Evaluation results",
        "",
        "Generated by `scripts/run_eval.py`. One file per model with the failed checks.",
        "",
        header,
        "|---|" + "---|" * len(runs),
    ]
    for label, key, unit in rows:
        lines.append(f"| {label} | " + " | ".join(f"{r['summary'][key]}{unit}" for r in runs) + " |")
    for group, label in GROUPS.items():
        cells = []
        for r in runs:
            stats = r["summary"]["groups"].get(group, {})
            cells.append(f"{stats.get('rules_respected', '-')}% ({stats.get('cases_passed', 0)}/{stats.get('cases', 0)})")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    (RESULTS_DIR / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def conversation_markdown(title: str, case: dict, traces: list[dict], model_name: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"{case['note']}",
        "",
        f"Generated by `scripts/run_eval.py --conversations` with `{model_name}`, store clock "
        f"{WEDNESDAY_AFTERNOON:%A %Y-%m-%d %H:%M}. Lines starting with → are tool calls the customer does not see.",
        "",
        "---",
        "",
    ]
    for trace in traces:
        lines += [f"**Cliente:** {trace['customer']}", ""]
        for call in trace["tool_calls"]:
            args = json.dumps(call["args"], ensure_ascii=False)
            who = " (by the code guard)" if trace["code_handoff"] and call["tool"] == "escalate_to_human" else ""
            lines.append(f"> → `{call['tool']}({args})`{who}")
        if trace["tool_calls"]:
            lines.append("")
        lines += [f"**Empório:** {trace['reply']}", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="e.g. ollama:qwen3:4b-instruct; default: LLM_MODEL from .env")
    parser.add_argument("--only", nargs="+", help="case ids to run (the report is not saved)")
    parser.add_argument("--conversations", action="store_true", help="save the example transcripts")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console would choke on accents and emoji

    if args.model:
        model = init_chat_model(args.model, **model_settings(args.model))
        model_name = args.model
    else:
        model = load_model()
        model_name = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    agent = build_agent(model=model, checkpointer=InMemorySaver(), clock=lambda: WEDNESDAY_AFTERNOON)

    cases = [c for c in load_cases() if not args.only or c["id"] in args.only]
    cases_by_id = {c["id"]: c for c in cases}
    results, all_traces = [], {}
    for number, case in enumerate(cases, 1):
        traces = run_case(agent, case, model_name)
        result = score_case(case, traces)
        results.append(result)
        all_traces[case["id"]] = traces
        status = "PASS" if result.passed else "FAIL"
        print(f"[{number}/{len(cases)}] {status} {case['id']} ({result.seconds:.0f}s)", flush=True)

    summary = summarize(results, cases)
    print(json.dumps({k: v for k, v in summary.items() if k != "groups"}, ensure_ascii=False), flush=True)
    if args.only:
        for result in results:
            for turn in result.turns:
                print(f"\n{result.case_id}: {turn.reply}\n  tools={turn.tools}\n  failed={[c.name for c in turn.checks if not c.passed]}")
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"model": model_name, "summary": summary, "cases": [asdict(r) for r in results], "traces": all_traces}
    (RESULTS_DIR / f"{slug(model_name)}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (RESULTS_DIR / f"{slug(model_name)}.md").write_text(report_markdown(model_name, summary, results, cases_by_id), encoding="utf-8")
    write_comparison()

    if args.conversations:
        CONVERSATIONS_DIR.mkdir(exist_ok=True)
        for case_id, (filename, title) in EXAMPLES.items():
            if case_id in all_traces:
                text = conversation_markdown(title, cases_by_id[case_id], all_traces[case_id], model_name)
                (CONVERSATIONS_DIR / f"{filename}.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
