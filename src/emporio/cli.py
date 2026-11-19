"""Chat with the store agent in the terminal. Conversations are kept in SQLite between runs."""

from __future__ import annotations

import argparse
import os
import time
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from rich.console import Console
from rich.markdown import Markdown

from emporio.agent import DEFAULT_MODEL, build_agent, load_model
from emporio.data import DEFAULT_DATA_DIR
from emporio.tracing import append_trace, turn_trace

MEMORY_FILE = DEFAULT_DATA_DIR.parent / "memory.sqlite"
EXIT_WORDS = {"sair", "exit", "quit"}
TITLE = "Empório da Música · assistente virtual"


def friendly_error(error: Exception) -> str:
    text = str(error)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "O limite de uso do modelo acabou por agora. Tente mais tarde ou troque o LLM_MODEL no .env."
    if type(error).__name__ in {"ConnectError", "ConnectionError"}:
        return "Não consegui falar com o modelo. Se estiver usando o Ollama, confira se ele está aberto."
    return f"Não consegui responder agora ({type(error).__name__}). Tente de novo."


def chat(agent, session: str, model_name: str, console: Console) -> None:
    config = {"configurable": {"thread_id": session}}
    while True:
        try:
            text = console.input("[bold cyan]Você:[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if text.lower() in EXIT_WORDS:
            break
        if not text:
            continue

        before = len(agent.get_state(config).values.get("messages", []))
        start = time.time()
        try:
            with console.status("Consultando..."):
                result = agent.invoke({"messages": [("user", text)]}, config)
        except Exception as error:
            console.print(f"[red]{friendly_error(error)}[/]")
            continue

        new_messages = result["messages"][before:]
        append_trace(turn_trace(session, model_name, new_messages, time.time() - start))
        console.print("[bold green]Empório:[/]", Markdown(new_messages[-1].text))


def main() -> None:
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--session", default="terminal", help="the same id resumes an earlier conversation")
    parser.add_argument("--new", action="store_true", help="start a new conversation")
    args = parser.parse_args()
    session = uuid4().hex[:8] if args.new else args.session

    console = Console()
    model = load_model()
    model_name = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    console.print(f"[bold]{TITLE}[/] [dim](conversa {session}, modelo {model_name}; digite 'sair' para encerrar)[/]")
    with SqliteSaver.from_conn_string(str(MEMORY_FILE)) as memory:
        chat(build_agent(model=model, checkpointer=memory), session, model_name, console)


if __name__ == "__main__":
    main()