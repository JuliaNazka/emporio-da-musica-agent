"""LangGraph tool-calling agent: model -> tools -> model, with a grounding check before each reply."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from emporio import policies, rules, tools
from emporio.data import DEFAULT_DATA_DIR, StoreData, load_store_data
from emporio.grounding import GROUNDING_REMINDER, needs_grounding
from emporio.policies import PolicyIndex
from emporio.prompts import system_prompt

ENV_FILE = DEFAULT_DATA_DIR.parent / ".env"
DEFAULT_MODEL = "google_genai:gemini-3.8-flash"
# Local models: skip the hidden reasoning step and leave room for the prompt plus 10 tool schemas.
OLLAMA_SETTINGS = {"reasoning": False, "num_ctx": 8192}
MAX_GROUNDING_RETRIES = 1
FALLBACK_ANSWER = (
    "Não consegui confirmar essa informação no sistema agora. Posso verificar de novo, "
    "ou, se preferir, falar com a nossa equipe pelo WhatsApp " + tools.STORE_WHATSAPP + "."
)
EscalationReason = Literal["complaint", "defect", "policy_doubt", "customer_request", "unresolved"]


def _as_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def build_tools(data: StoreData, index: PolicyIndex) -> list[BaseTool]:
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
    def get_order_status(order_id: int, phone_or_email: str) -> str:
        """Status, items, total and tracking of an order. Needs the order number and the phone or
        e-mail used in the purchase."""
        return _as_json(tools.get_order_status(data, order_id, phone_or_email))

    @tool
    def check_return_options(order_id: int, phone_or_email: str) -> str:
        """Which return, exchange and warranty windows are still open for an order. Needs the order
        number and the phone or e-mail used in the purchase."""
        return _as_json(tools.check_return_options(data, order_id, phone_or_email))

    @tool
    def get_installment_options(amount: float) -> str:
        """Interest-free credit card plans allowed for an amount in BRL, and whether PIX + card is allowed."""
        return _as_json(tools.get_installment_options(amount))

    @tool
    def get_shipping_quote(city: str, amount: float) -> str:
        """Shipping fee and delivery time for a destination city and purchase amount in BRL."""
        return _as_json(tools.get_shipping_quote(city, amount))

    @tool
    def get_store_status() -> str:
        """Whether the store is open right now and when it opens next."""
        return _as_json(tools.get_store_status())

    @tool
    def search_policies(question: str) -> str:
        """Search the store policy manual (returns, warranty, shipping, payment, promotions,
        privacy, hours, address, contact). Returns the most relevant sections with their numbers."""
        return _as_json(policies.search_policies(index, question))

    @tool
    def escalate_to_human(reason: EscalationReason, summary: str) -> str:
        """Hand the conversation to the store team. Use for complaints, defects, requests to talk to
        a person, questions the manual does not cover, or problems you could not solve."""
        return _as_json(tools.escalate_to_human(reason, summary))

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


def _current_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Messages after the customer's last message, newest first."""
    turn = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.content != GROUNDING_REMINDER:
            break
        turn.append(message)
    return turn


def build_agent(
    model: BaseChatModel | None = None,
    data: StoreData | None = None,
    index: PolicyIndex | None = None,
    checkpointer=None,
    clock: Callable[[], datetime] | None = None,
):
    data = data if data is not None else load_store_data()
    index = index if index is not None else PolicyIndex.from_pdf()
    clock = clock or (lambda: datetime.now(rules.STORE_TZ))
    agent_tools = build_tools(data, index)
    llm = (model or load_model()).bind_tools(agent_tools)

    def call_model(state: MessagesState) -> dict:
        prompt = SystemMessage(system_prompt(clock()))
        return {"messages": [llm.invoke([prompt, *state["messages"]])]}

    def route_after_model(state: MessagesState) -> str:
        return "tools" if state["messages"][-1].tool_calls else "grounding_check"

    def grounding_check(state: MessagesState) -> dict:
        turn = _current_turn(state["messages"])
        answer = turn[0]
        tool_messages = [m for m in turn if isinstance(m, ToolMessage)]
        tools_used = {m.name for m in tool_messages}
        tool_results = [m.text for m in tool_messages]
        if not needs_grounding(answer.text, tools_used, tool_results):
            return {}
        retries = sum(isinstance(m, HumanMessage) for m in turn)
        if retries < MAX_GROUNDING_RETRIES:
            return {"messages": [RemoveMessage(id=answer.id), HumanMessage(GROUNDING_REMINDER)]}
        return {"messages": [RemoveMessage(id=answer.id), AIMessage(FALLBACK_ANSWER)]}

    def route_after_check(state: MessagesState) -> str:
        return "agent" if isinstance(state["messages"][-1], HumanMessage) else END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(agent_tools))
    graph.add_node("grounding_check", grounding_check)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_model, ["tools", "grounding_check"])
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("grounding_check", route_after_check, ["agent", END])
    return graph.compile(checkpointer=checkpointer)
