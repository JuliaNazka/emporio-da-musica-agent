from datetime import datetime

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from emporio import rules
from emporio.agent import FALLBACK_ANSWER, build_agent, build_tools, model_settings
from emporio.data import load_store_data
from emporio.grounding import GROUNDING_REMINDER, needs_grounding, unsupported_prices
from emporio.policies import PolicyIndex
from emporio.prompts import system_prompt

WEDNESDAY_AFTERNOON = datetime(2026, 11, 18, 15, 0, tzinfo=rules.STORE_TZ)


class ScriptedModel(GenericFakeChatModel):
    """Replays fixed AI messages, so the graph runs without calling a real LLM."""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(scope="module")
def data():
    return load_store_data()


@pytest.fixture(scope="module")
def index():
    return PolicyIndex.from_pdf()


def run(data, index, script, question="Quanto custa o Takamine GD20?"):
    model = ScriptedModel(messages=iter(script))
    agent = build_agent(model=model, data=data, index=index, clock=lambda: WEDNESDAY_AFTERNOON)
    return agent.invoke({"messages": [HumanMessage(question)]})["messages"]


def tool_call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{name}"}])


def test_tools_exposed_to_the_model(data, index):
    names = {t.name for t in build_tools(data, index)}
    assert names == {
        "search_products",
        "get_product_details",
        "list_active_promotions",
        "get_order_status",
        "check_return_options",
        "get_installment_options",
        "get_shipping_quote",
        "get_store_status",
        "search_policies",
        "escalate_to_human",
    }


def test_agent_answers_after_calling_a_tool(data, index):
    messages = run(data, index, [
        tool_call("get_product_details", {"name_or_id": "Takamine GD20"}),
        AIMessage("O Takamine GD20 sai por R$ 2.199,00, ou R$ 2.089,05 no PIX."),
    ])
    tool_result = next(m for m in messages if isinstance(m, ToolMessage))
    assert "2089.05" in tool_result.content
    assert messages[-1].content.startswith("O Takamine GD20")
    assert all(m.content != GROUNDING_REMINDER for m in messages)


def test_ungrounded_price_is_replaced_by_a_tool_backed_answer(data, index):
    messages = run(data, index, [
        AIMessage("Custa R$ 1.999,00."),
        tool_call("get_product_details", {"name_or_id": "Takamine GD20"}),
        AIMessage("Conferi aqui: R$ 2.199,00."),
    ])
    contents = [m.content for m in messages]
    assert "Custa R$ 1.999,00." not in contents
    assert GROUNDING_REMINDER in contents
    assert messages[-1].content == "Conferi aqui: R$ 2.199,00."


def test_repeated_ungrounded_answer_falls_back_to_a_safe_reply(data, index):
    messages = run(data, index, [AIMessage("Custa R$ 10,00."), AIMessage("Custa R$ 20,00.")])
    assert messages[-1].content == FALLBACK_ANSWER


def test_answer_without_prices_needs_no_tool(data, index):
    messages = run(data, index, [AIMessage("Oi! Como posso ajudar?")], question="oi")
    assert messages[-1].content == "Oi! Como posso ajudar?"


def test_grounding_rule():
    assert needs_grounding("Sai por R$ 500", tools_used=set())
    assert needs_grounding("Temos 3 unidades disponíveis", tools_used={"search_policies"})
    assert not needs_grounding("Sai por R$ 500", tools_used={"get_product_details"})
    assert not needs_grounding("A loja abre às 9h.", tools_used=set())


def test_system_prompt_knows_when_the_store_is_closed():
    sunday = datetime(2026, 11, 22, 10, 0, tzinfo=rules.STORE_TZ)
    assert "closed, next opening 2026-11-23 09:00" in system_prompt(sunday)


def test_local_models_skip_reasoning_and_get_a_larger_context():
    assert model_settings("google_genai:gemini-3.8-flash") == {"temperature": 0}
    local = model_settings("ollama:qwen3:4b-instruct")
    assert local["reasoning"] is False
    assert local["num_ctx"] >= 8192


def test_prices_must_match_the_tool_results():
    result = ['{"list_price": 2199.0, "pix_price": 2089.05, "value": 183.25}']
    assert unsupported_prices("No PIX fica R$ 2.089,05, ou 12x de R$ 183,25.", result) == []
    assert unsupported_prices("Sai por R$ 2,089.05 ou R$ 2.199", result) == []
    assert unsupported_prices("O preço atual é R$ 3.999,00.", result) == [3999.0]


def test_invented_price_after_a_tool_call_is_caught():
    result = ['{"promotions": [{"product_id": 90, "list_price": 1299.0}]}']
    assert needs_grounding("Custa R$ 3.999,00.", {"list_active_promotions"}, result)
    assert not needs_grounding("Custa R$ 1.299,00.", {"list_active_promotions"}, result)
