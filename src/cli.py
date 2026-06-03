"""Terminal chat interface for the Dynamic ERP Agent.

Run with:
    python -m src.cli

Commands:
    /help    show help
    /trace   show the pipeline trace for the last answer
    /new     start a new conversation thread (clears short-term context)
    /exit    quit
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

from src.config.settings import get_settings
from src.graph.builder import AgentRuntime

_BANNER = """
============================================================
  Dynamic ERP Agent  (LangGraph + Ollama)
  Ask questions about people, systems, datasets, org units.
  Type /help for commands, /exit to quit.
============================================================
"""


def _ensure_utf8_stdout() -> None:
    """Make stdout/stderr UTF-8 so Arabic output renders on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # best effort
                pass


def _print_trace(state: dict[str, Any]) -> None:
    """Pretty-print the per-stage trace of the last turn."""
    trace = state.get("trace", [])
    if not trace:
        print("(no trace available)")
        return
    print("\n--- pipeline trace ---")
    total = 0.0
    for entry in trace:
        elapsed = entry.get("elapsed_ms", 0.0)
        total += float(elapsed)
        print(f"  {entry.get('stage', '?'):<22} {elapsed:>8.2f} ms  {entry.get('detail', '')}")
    print(f"  {'TOTAL':<22} {total:>8.2f} ms")
    errors = state.get("errors", [])
    if errors:
        print("  errors:")
        for err in errors:
            print(f"    - {err}")
    print("----------------------\n")


async def _chat_loop() -> None:
    """Main async REPL."""
    settings = get_settings()
    print(_BANNER)
    print(f"  adapter={settings.erp_adapter.value}  model={settings.ollama_model}  base_url={settings.erp_base_url}\n")

    # Stable default thread so conversation history and long-term memory persist
    # across restarts. Use /new to start a fresh, isolated conversation.
    thread_id = "cli-default"
    last_state: dict[str, Any] | None = None

    async with AgentRuntime(settings) as runtime:
        loop = asyncio.get_event_loop()
        while True:
            try:
                question = (await loop.run_in_executor(None, lambda: input("you > "))).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not question:
                continue
            if question in {"/exit", "/quit"}:
                break
            if question == "/help":
                print("Commands: /help  /trace  /new  /exit")
                continue
            if question == "/trace":
                if last_state:
                    _print_trace(last_state)
                else:
                    print("(ask something first)")
                continue
            if question == "/new":
                thread_id = f"cli-{uuid.uuid4().hex[:8]}"
                last_state = None
                print(f"(started new conversation: {thread_id})")
                continue

            try:
                last_state = await runtime.ask(question, thread_id=thread_id)
                print(f"\nagent > {last_state.get('final_response', '(no answer)')}\n")
            except Exception as exc:  # keep the REPL alive on any failure
                print(f"\n[error] {exc}\n")

    print("Goodbye.")


def main() -> None:
    """Synchronous entry point."""
    _ensure_utf8_stdout()
    try:
        asyncio.run(_chat_loop())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
