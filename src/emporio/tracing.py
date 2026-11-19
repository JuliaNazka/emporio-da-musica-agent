"""One JSON line per turn: the customer's message, each tool call with its result, and the reply."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from emporio import rules
from emporio.data import DEFAULT_DATA_DIR

TRACE_FILE = DEFAULT_DATA_DIR.parent / "traces.jsonl"


def _parse(result: str | None):
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        return result


def turn_trace(session: str, model: str, new_messages: list[BaseMessage], seconds: float) -> dict:
    """new_messages starts with the customer's message and ends with the reply."""
    results = {m.tool_call_id: m.text for m in new_messages if isinstance(m, ToolMessage)}
    calls = [
        {"tool": call["name"], "args": call["args"], "result": _parse(results.get(call["id"]))}
        for message in new_messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    return {
        "at": datetime.now(rules.STORE_TZ).isoformat(timespec="seconds"),
        "session": session,
        "model": model,
        "customer": new_messages[0].text,
        "tool_calls": calls,
        "review_retries": sum(isinstance(m, HumanMessage) for m in new_messages[1:]),
        "reply": new_messages[-1].text,
        "seconds": round(seconds, 1),
    }


def append_trace(record: dict, path: Path = TRACE_FILE) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    