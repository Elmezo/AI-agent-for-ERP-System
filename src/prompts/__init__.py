"""Prompt templates, stored as files and rendered with ``${name}`` placeholders.

Keeping prompts out of code (and out of nodes) makes them easy to review and
version without touching logic.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

_PROMPT_DIR = Path(__file__).parent


@lru_cache
def load_prompt(name: str) -> str:
    """Load a prompt file (``<name>.md``) from the prompts directory."""
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(name: str, /, **values: str) -> str:
    """Load a prompt and substitute ``${placeholder}`` tokens safely."""
    template = Template(load_prompt(name))
    return template.safe_substitute(**values)
