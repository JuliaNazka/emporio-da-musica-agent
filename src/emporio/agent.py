"""LangGraph tool-calling agent: model -> tools -> model, with a review of each reply (grounding and hand-off) before it is sent."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import uuid4

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode

from emporio import policies, rules, tools
from emporio.data import DEFAULT_DATA_DIR, StoreData, load_store_data
from emporio.escalation import ESCALATION_REMINDER, ESCALATION_TOOL, escalation_reason, handoff_answer, needs_escalation
from emporio.grounding import GROUNDING_REMINDER, needs_grounding
from emporio.policies import PolicyIndex
from emporio.prompts import system_prompt

logger = logging.getLogger(__name__)
ENV_FILE = DEFAULT_DATA_DIR.parent / ".env"
DEFAULT_MODEL = "google_genai:gemini-3.8-flash"
# Local models: skip the hidden reasoning step and leave room for the prompt plus 10 tool schemas.
OLLAMA_SETTINGS = {"reasoning": False, "num_ctx": 8192}
MAX_RETRIES = 1
INTERNAL_REMINDERS = frozenset({GROUNDING_REMINDER, ESCALATION_REMINDER})
FALLBACK_ANSWER = (
    "Não consegui confirmar essa informação no sistema agora. Posso verificar de novo, "
    "ou, se preferir, falar com a nossa equipe pelo WhatsApp " + tools.STORE_WHATSAPP + "."
)
EscalationReason = Literal["complaint", "defect", "policy_doubt", "customer_request", "unresolved"]
ORDER_TOOLS = frozenset({"get_order_status", "check_return_options"})
# The conversation so far, passed in by LangGraph and hidden from the model's view of the tool.
Conversation = Annotated[list[BaseMessage], InjectedState("messages")]


def _as_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _store_now() -> datetime:
    return datetime.now(rules.STORE_TZ)


def _lookups_locked(messages: list[BaseMessage]) -> bool:
    failed = sum(
        isinstance(m, ToolMessage) and m.name in ORDER_TOOLS and '"verified": false' in m.text for m in messages
    )
    return failed >= tools.MAX_FAILED_VERIFICATIONS


def build_tools(data: StoreData, index: PolicyIndex, clock: Callable[[], datetime] = _store_now) -> list[BaseTool]:
    @tool
    def search_products(
        query: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        in_stock_only: bool = True,
    ) -> str:
        """Search the catalog by words (name, brand, type) and/or category, optionally within a
        price range in BRL (card price). Returns up to 8 products, cheapest first."""
        return _as_json(tools.search_products(data, query, category, min_price, max_price, in_stock_only))

    @tool
    def get_product_details(name_or_id: str) -> str:
        """Full details of one product: prices (list, promotion, PIX), stock, specs, maximum
        installments, expired promotions and alternatives when it is unavailable."""
        return _as_json(tools.get_product_details(data, name_or_id))

    @tool
    def list_active_promotions() -> str:
        """All promotions active right now, with original price, discount and promotional price."""
        return _as_json(tools.list_active_promotions(data))

    @tool
    def get_order_status(order_id: int, phone_or_email: str, messages: Conversation) -> str:
        """Status, items, total and tracking of an order. Needs the order number and the phone or
        e-mail used in the purchase."""
        if _lookups_locked(messages):
            return _as_json(tools.ORDER_LOOKUP_LOCKED)
        return _as_json(tools.get_order_status(data, order_id, phone_or_email))

    @tool
    def check_return_options(order_id: int, phone_or_email: str, messages: Conversation) -> str:
        """Which return, exchange and warranty windows are still open for an order. Needs the order
        number and the phone or e-mail used in the purchase."""
        if _lookups_locked(messages):
            return _as_json(tools.ORDER_LOOKUP_LOCKED)
        return _as_json(tools.check_return_options(data, order_id, phone_or_email, today=clock().date()))

    @tool
    def get_installment_options(amount: float) -> str:
        """Interest-free credit card plans allowed for an amount in BRL, and whether PIX + card is allowed."""
        return _as_json(tools.get_installment_options(amount))

    @tool
    def get_shipping_quote(city: str, amount: float | None = None) -> str:
        """Shipping fee and delivery time to a destination city (any city in Brazil). The purchase
        amount in BRL is optional: it only changes the fee in Campo Grande."""
        return _as_json(tools.get_shipping_quote(city, amount))

    @tool
    def get_store_status() -> str:
        """Whether the store is open right now and when it opens next."""
        return _as_json(tools.get_store_status(clock()))

    @tool
    def search_policies(question: str) -> str:
        """Search the store policy manual (returns, warranty, shipping, payment, promotions,
        privacy, hours, address, contact). Returns the most relevant sections with their numbers."""
        return _as_json(policies.search_policies(index, question))

    @tool
    def escalate_to_human(reason: EscalationReason, summary: str) -> str:
        """Hand the conversation to the store team. Use for complaints, defects, requests to talk to
        a person, questions the manual does not cover, or problems you could not solve."""
        return _as_json(tools.escalate_to_human(reason, summary, now=clock()))

    return [
        search_products,
        get_product_details,
        list_active_promotions,
        get_order_status,
        check_return_options,
        get_installment_options,
        get_shipping_quote,
        get_store_status,
        search_policies,
        escalate_to_human,
    ]


def model_settings(model_name: str) -> dict:
    settings = {"temperature": 0}
    if model_name.startswith("ollama:"):
        settings.update(OLLAMA_SETTINGS)
    return settings


def load_model() -> BaseChatModel:
    # The project's .env wins over API keys set elsewhere on the machine.
    load_dotenv(ENV_FILE, override=True)
    model_name = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    return init_chat_model(model_name, **model_settings(model_name))


def _is_customer(message: BaseMessage) -> bool:
    return isinstance(message, HumanMessage) and message.text not in INTERNAL_REMINDERS


def _current_turn(messages: list[BaseMessage]) -> tuple[HumanMessage | None, list[BaseMessage]]:
    """The customer's last message and everything after it, newest first."""
    turn = []
    for message in reversed(messages):
        if _is_customer(message):
            return message, turn
        turn.append(message)
    return None, turn


def build_agent(
    model: BaseChatModel | None = None,
    data: StoreData | None = None,
    index: PolicyIndex | None = None,
    checkpointer=None,
    clock: Callable[[], datetime] | None = None,
):
    data = data if data is not None else load_store_data()
    index = index if index is not None else PolicyIndex.from_pdf()
    clock = clock or _store_now
    agent_tools = build_tools(data, index, clock)
    llm = (model or load_model()).bind_tools(agent_tools)

    def call_model(state: MessagesState) -> dict:
        prompt = SystemMessage(system_prompt(clock()))
        try:
            return {"messages": [llm.invoke([prompt, *state["messages"]])]}
        except Exception:
            # e.g. a local model emitting a tool call with broken JSON: the customer gets the safe reply,
            # and the review step can still hand the case to the team.
            logger.exception("Model call failed")
            return {"messages": [AIMessage(FALLBACK_ANSWER)]}

    def route_after_model(state: MessagesState) -> str:
        return "tools" if state["messages"][-1].tool_calls else "review_reply"

    def hand_off(customer_text: str) -> list[BaseMessage]:
        """Escalate in code when the model would not, and tell the customer what happens next."""
        args = {"reason": escalation_reason(customer_text), "summary": customer_text}
        ticket = tools.escalate_to_human(**args, now=clock())
        call_id = f"guard-{uuid4().hex[:8]}"
        return [
            AIMessage(content="", tool_calls=[{"name": ESCALATION_TOOL, "args": args, "id": call_id}]),
            ToolMessage(_as_json(ticket), tool_call_id=call_id, name=ESCALATION_TOOL),
            AIMessage(handoff_answer(ticket)),
        ]

    def review_reply(state: MessagesState) -> dict:
        customer, turn = _current_turn(state["messages"])
        customer_text = customer.text if customer else ""
        answer = turn[0]
        tool_messages = [m for m in turn if isinstance(m, ToolMessage)]
        tools_used = {m.name for m in tool_messages}
        retries = sum(isinstance(m, HumanMessage) for m in turn)

        tool_args = [_as_json(c["args"]) for m in turn if isinstance(m, AIMessage) for c in m.tool_calls]
        tool_results = [m.text for m in tool_messages] + tool_args
        if needs_grounding(answer.text, tools_used, tool_results, customer_text):
            if retries < MAX_RETRIES:
                return {"messages": [RemoveMessage(id=answer.id), HumanMessage(GROUNDING_REMINDER)]}
            if needs_escalation(customer_text, tools_used):
                return {"messages": [RemoveMessage(id=answer.id), *hand_off(customer_text)]}
            return {"messages": [RemoveMessage(id=answer.id), AIMessage(FALLBACK_ANSWER)]}
        if needs_escalation(customer_text, tools_used):
            if retries < MAX_RETRIES:
                return {"messages": [RemoveMessage(id=answer.id), HumanMessage(ESCALATION_REMINDER)]}
            return {"messages": [RemoveMessage(id=answer.id), *hand_off(customer_text)]}
        return {}

    def route_after_check(state: MessagesState) -> str:
        return "agent" if isinstance(state["messages"][-1], HumanMessage) else END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(agent_tools))
    graph.add_node("review_reply", review_reply)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_model, ["tools", "review_reply"])
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("review_reply", route_after_check, ["agent", END])
    return graph.compile(checkpointer=checkpointer)
