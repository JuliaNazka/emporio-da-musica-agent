"""Make sure complaints, defects and requests for a person reach the store team (policies 7.3 and 10).

Small models often answer these on their own. This check does not depend on the model following the prompt.
"""

from __future__ import annotations

import re

DEFECT = re.compile(
    r"\b(defeit\w*|trinc(ad|ou)\w*|quebr(ad|ou)\w*|rach(ad|ou)\w*|riscad\w*|amassad\w*|danificad\w*|"
    r"n[aã]o (funciona|liga)|broken|cracked|defect\w*)\b",
    re.IGNORECASE,
)
# Only a request for a person counts: "sou o gerente" or "você é atendente" must not open a ticket.
PERSON = re.compile(
    r"\b(falar com|chamar?|quero|preciso( de)?|passar? (para|pra)|transferir? (para|pra))\s+"
    r"(o |a |um |uma |algum |alguma )?(atendente|gerente|humano|pessoa|vendedor|algu[eé]m|respons[aá]vel)\b"
    r"|\batendimento humano\b"
    r"|\b(speak|talk) (to|with) (a |an |the )?(person|human|manager|agent)\b",
    re.IGNORECASE,
)
COMPLAINT = re.compile(r"\b(reclama\w*|procon|absurdo|insatisfeit\w*|complain\w*)\b", re.IGNORECASE)
# "Se o violão tiver defeito, qual a garantia?" or "não tem defeito" is not a defect report.
# Only "se" followed by a subject or a verb: "o braço se quebrou" is still a report.
NOT_A_REPORT = re.compile(
    r"\b(se (o|a|os|as|meu|minha|eu|ele|ela|tiver|vier|chegar|der|estiver)|caso|e se|if|in case|"
    r"n[aã]o|nenhum|nenhuma|sem|not|without)\b",
    re.IGNORECASE,
)
WORDS_BEFORE = 4

ESCALATION_TOOL = "escalate_to_human"
ESCALATION_REMINDER = (
    "[internal check] The customer reported a complaint or a defect, or asked for a person, but you did "
    "not call escalate_to_human. Call it now, then give the WhatsApp channel and the response time it "
    "returns. Do not promise an outcome."
)


def reports_defect(customer_text: str) -> bool:
    for match in DEFECT.finditer(customer_text):
        words_before = " ".join(customer_text[: match.start()].split()[-WORDS_BEFORE:])
        if not NOT_A_REPORT.search(words_before):
            return True
    return False


def escalation_reason(customer_text: str) -> str | None:
    if reports_defect(customer_text):
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
