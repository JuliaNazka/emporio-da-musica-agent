"""Run a few scripted customer conversations against the real model and print each tool call.

Usage: python scripts/smoke_conversations.py [--model ollama:qwen3:4b-instruct] [--only promo order]
Without --model it uses LLM_MODEL from .env.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from emporio import rules
from emporio.agent import build_agent, load_model, model_settings

WEDNESDAY_AFTERNOON = datetime(2026, 11, 18, 15, 0, tzinfo=rules.STORE_TZ)
TOOL_RESULT_PREVIEW = 160

CONVERSATIONS = {
    "promo": ["Oi! Vi que o Takamine GD20 estava na Oferta Especial, ainda vale? Quanto fica no PIX?"],
    "order": ["Oi, quero saber do meu pedido 8", "meu telefone é (67) 99654-7821"],
    "complaint": ["O violão do pedido 5 chegou com o tampo trincado, quero trocar! email rafael.pereira@jmail.com"],
    "accessory": ["Vocês vendem cordas de violão e amplificador?"],
    "lgpd": ["Qual o status do pedido 8? meu telefone é 67 90000-0000"],
}


def run(agent, name: str, turns: list[str]) -> None:
    config = {"configurable": {"thread_id": name}}
    print(f"\n===== {name} =====", flush=True)
    for text in turns:
        start = time.time()
        before = len(agent.get_state(config).values.get("messages", []))
        result = agent.invoke({"messages": [("user", text)]}, config)
        print(f"CUSTOMER: {text}", flush=True)
        for message in result["messages"][before:]:
            if isinstance(message, AIMessage) and message.tool_calls:
                for call in message.tool_calls:
                    print(f"  -> {call['name']}({call['args']})", flush=True)
            elif isinstance(message, ToolMessage):
                print(f"  <- {message.content[:TOOL_RESULT_PREVIEW]}", flush=True)
        print(f"AGENT ({time.time() - start:.0f}s): {result['messages'][-1].text}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="e.g. ollama:qwen3:4b-instruct or google_genai:gemini-3.8-flash")
    parser.add_argument("--only", nargs="+", choices=CONVERSATIONS, help="run only these conversations")
    args = parser.parse_args()

    model = init_chat_model(args.model, **model_settings(args.model)) if args.model else load_model()
    agent = build_agent(model=model, checkpointer=InMemorySaver(), clock=lambda: WEDNESDAY_AFTERNOON)
    for name in args.only or CONVERSATIONS:
        run(agent, name, CONVERSATIONS[name])


if __name__ == "__main__":
    main()
