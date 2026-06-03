"""Ollama LLM wrapper with structured-output support.

Smaller local models have weak/unreliable native function-calling, so structured
output is obtained via strict JSON prompting + Pydantic validation, with a
bounded *repair loop*: when parsing/validation fails, the error is fed back to
the model and it is asked to fix its output.

Also handles "thinking" models (e.g. qwen3) that wrap reasoning in
``<think>...</think>`` blocks by stripping them before JSON extraction.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ValidationError

from src.config.settings import Settings
from src.observability.logging import get_logger

_log = get_logger("llm")

TModel = TypeVar("TModel", bound=BaseModel)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(RuntimeError):
    """Raised when the model cannot produce valid structured output."""


def extract_json(text: str) -> str:
    """Best-effort extraction of a JSON object from raw model output.

    Strips ``<think>`` blocks and code fences, then returns the substring from
    the first ``{`` to the last matching ``}``.
    """
    cleaned = _THINK_RE.sub("", text or "").strip()
    fence = _FENCE_RE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


class OllamaLLM:
    """Thin async wrapper around ``ChatOllama`` for text + structured output."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        common: dict[str, Any] = {
            "model": settings.ollama_model,
            "base_url": settings.ollama_base_url,
            "temperature": settings.ollama_temperature,
            "client_kwargs": {
                "timeout": settings.ollama_timeout_seconds,
                # Bypass the ngrok free-tier browser interstitial when Ollama is
                # tunnelled (e.g. a remote GPU on Kaggle). Harmless otherwise.
                "headers": {"ngrok-skip-browser-warning": "true"},
            },
        }
        # Pin GPU offload only when explicitly configured. ``num_gpu=0`` forces
        # CPU-only inference, which is required on GPUs whose CUDA backend
        # crashes the llama runner during model load (e.g. Maxwell CC 5.0).
        if settings.ollama_num_gpu is not None:
            common["num_gpu"] = settings.ollama_num_gpu
        # Free-form text generation.
        self._text_llm = ChatOllama(**common)
        # JSON-constrained generation for structured output.
        self._json_llm = ChatOllama(format="json", **common)

    async def complete(self, system: str, user: str) -> tuple[str, dict[str, int]]:
        """Generate free-form text. Returns ``(text, token_usage)``."""
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        response = await self._text_llm.ainvoke(messages)
        text = _THINK_RE.sub("", str(response.content)).strip()
        return text, self._usage(response)

    async def structured(
        self,
        system: str,
        user: str,
        schema: type[TModel],
        max_repair: int = 2,
    ) -> tuple[TModel, dict[str, int]]:
        """Generate JSON validated against ``schema`` with a repair loop.

        Raises:
            LLMError: if no valid output is produced within the repair budget.
        """
        total_usage: dict[str, int] = {}
        messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=user)]

        last_error = ""
        for attempt in range(max_repair + 1):
            response = await self._json_llm.ainvoke(messages)
            self._accumulate(total_usage, self._usage(response))
            raw = str(response.content)
            payload = extract_json(raw)
            try:
                model = schema.model_validate_json(payload)
                return model, total_usage
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                _log.warning("structured_parse_failed", attempt=attempt, error=last_error[:300])
                messages.append(HumanMessage(content=raw))
                messages.append(
                    HumanMessage(
                        content=(
                            "Your previous output was not valid for the required schema. "
                            f"Error: {last_error}. Reply with ONLY corrected JSON, no prose."
                        )
                    )
                )
        raise LLMError(f"could not produce valid structured output: {last_error}")

    @staticmethod
    def _usage(response: Any) -> dict[str, int]:
        """Extract token usage from a LangChain response, if available."""
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, dict):
            return {
                "prompt": int(usage.get("input_tokens", 0) or 0),
                "completion": int(usage.get("output_tokens", 0) or 0),
            }
        return {}

    @staticmethod
    def _accumulate(target: dict[str, int], usage: dict[str, int]) -> None:
        """Add token usage into an accumulator dict in place."""
        for key, value in usage.items():
            target[key] = target.get(key, 0) + value
