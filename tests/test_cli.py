import json
from datetime import datetime

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from emporio import rules
from emporio.agent import build_agent
from emporio.cli import friendly_error
from emporio.escalation import ESCALATION_REMINDER
from emporio.tracing import append_trace, turn_trace

MODEL = "ollama:qwen3:4b-instruct"


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_trace_pairs_each_tool_call_with_its_result():
    new_messages = [
        HumanMessage("Quanto custa o GD20?"),
        AIMessage("", tool_calls=[{"name": "get_product_details", "args": {"name_or_id": "GD20"}, "id": "c1"}]),
        ToolMessage('{"pix_price": 2089.05}', tool_call_id="c1", name="get_product_details"),
        AIMessage("Sai por R$ 2.089,05 no PIX."),
    ]
    record = turn_trace("s1", MODEL, new_messages, seconds=2.34)
    assert record["customer"] == "Quanto custa o GD20?"
    assert record["tool_calls"] == [
        {"tool": "get_product_details", "args": {"name_or_id": "GD20"}, "result": {"pix_price": 2089.05}}
    ]
    assert record["reply"] == "Sai por R$ 2.089,05 no PIX."
    assert record["review_retries"] == 0
    assert record["seconds"] == 2.3


def test_trace_counts_review_retries():
    new_messages = [HumanMessage("chegou quebrado"), HumanMessage(ESCALATION_REMINDER), AIMessage("Passei para a equipe.")]
    assert turn_trace("s1", MODEL, new_messages, seconds=1)["review_retries"] == 1


def test_traces_are_appended_one_json_per_line(tmp_path):
    path = tmp_path / "traces.jsonl"
    append_trace({"turn": 1}, path)
    append_trace({"turn": 2, "reply": "Olá, tudo bem?"}, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"turn": 1}, {"turn": 2, "reply": "Olá, tudo bem?"}]


def test_conversation_is_remembered_between_runs(tmp_path):
    memory_file = str(tmp_path / "memory.sqlite")
    config = {"configurable": {"thread_id": "ana"}}
    clock = lambda: datetime(2026, 11, 18, 15, 0, tzinfo=rules.STORE_TZ)

    with SqliteSaver.from_conn_string(memory_file) as memory:
        first_run = build_agent(model=ScriptedModel(messages=iter([AIMessage("Oi! Qual o número do pedido?")])),
                                checkpointer=memory, clock=clock)
        first_run.invoke({"messages": [HumanMessage("Oi, quero ver meu pedido")]}, config)

    with SqliteSaver.from_conn_string(memory_file) as memory:
        second_run = build_agent(model=ScriptedModel(messages=iter([])), checkpointer=memory, clock=clock)
        history = second_run.get_state(config).values["messages"]
    assert [m.text for m in history] == ["Oi, quero ver meu pedido", "Oi! Qual o número do pedido?"]


def test_errors_get_a_clear_message():
    assert "limite" in friendly_error(Exception("429 RESOURCE_EXHAUSTED"))
    assert "Ollama" in friendly_error(ConnectionError("Failed to connect"))
    assert "Tente de novo" in friendly_error(ValueError("boom"))