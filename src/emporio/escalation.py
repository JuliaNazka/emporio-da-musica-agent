"""Make sure complaints, defects and requests for a person reach the store team (policies 7.3 and 10).

Small models often answer these on their own. This check does not depend on the model following the prompt.
"""

from __future__ import annotations

import re

DEFECT = re.compile(
    r"\b(defeit\w*|trincad\w*|quebrad\w*|rachad\w*|riscad\w*|amassad\w*|danificad\w*|"
    r"n[aã]o (funciona|liga)|broken|cracked|defect\w*)\b",
    re.IGNORECASE,
)
PERSON = re.compile(
    r"\b(atendente|gerente|falar com (um|uma|algu[eé]m|humano|pessoa|vendedor)\w*|"
    r"speak to (a )?(person|human))\b",
    re.IGNORECASE,
)
COMPLAINT = re.compile(r"\b(reclama\w*|procon|absurdo|insatisfeit\w*|complain\w*)\b", re.IGNORECASE)

ESCALATION_TOOL = "escalate_to_human"
ESCALATION_REMINDER = (
    "[internal check] The customer reported a complaint or a defect, or asked for a person, but you did "
    "not call escalate_to_human. Call it now, then give the WhatsApp channel and the response time it "
    "returns. Do not promise an outcome."
)


def escalation_reason(customer_text: str) -> str | None:
    if DEFECT.search(customer_text):
        return "defect"
    if PERSON.search(customer_text):
        return "customer_request"
    if COMPLAINT.search(customer_text):
        return "complaint"
    return None


def needs_escalation(customer_text: str, tools_used: set[str]) -> bool:
    return escalation_reason(customer_text) is not None and ESCALATION_TOOL not in tools_used


def handoff_answer(ticket: dict) -> str:
    return (
        "Sinto muito pelo problema. Já passei o seu caso para a nossa equipe, que vai responder "
        f"{ticket['response_time']} pelo WhatsApp {ticket['contact']['whatsapp']}. "
        "Quem decide a solução é a equipe da loja."
    )
