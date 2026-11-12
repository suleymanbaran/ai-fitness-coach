"""LLM client on the OpenAI Responses API: two-tier model routing, explicit prompt caching
and a bounded server-side tool loop.

Prompt layout (order matters for cache hits):
  1. one `developer` item holding the stable prefix (system rules + profile + memories +
     program summary). When it is long enough, it carries an explicit cache breakpoint with
     a 12h TTL, so the daily prefix is written once and read at 0.1x all day.
  2. prior turns and the volatile block (today's metrics, retrieved protocols, question),
     covered by the implicit 30m session breakpoint.

The stable prefix must reach the model's minimum cacheable length (1,024 visible tokens on
GPT-5.6+) before a breakpoint pays for itself: below it, cache writes at 1.25x cost more than
they save, so `complete` simply omits the breakpoint for short prefixes.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from openai import OpenAI

logger = logging.getLogger("fitness_coach")

# GPT-6 models occasionally leak inline citation markers ("citeturn0") into the text.
_CITATION_ARTIFACT = re.compile(r"\s*citeturn\d+\s*")


def _clean(text: str) -> str:
    return _CITATION_ARTIFACT.sub(" ", text).strip()

# Characters-per-token heuristic for the cacheability gate; no tokenizer dependency.
CHARS_PER_TOKEN = 4
CACHE_GATE_MARGIN_TOKENS = 76

Tier = Literal["fast", "plan"]


@dataclass(frozen=True)
class LLMUsage:
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int

    @classmethod
    def zero(cls, model: str) -> "LLMUsage":
        return cls(model=model, input_tokens=0, cached_tokens=0, output_tokens=0)


@dataclass(frozen=True)
class LLMReply:
    text: str
    usage: LLMUsage


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _usage(model: str, usage) -> LLMUsage:
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    return LLMUsage(
        model=model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        cached_tokens=cached or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
    )


def _add_usage(total: LLMUsage, delta: LLMUsage) -> LLMUsage:
    return LLMUsage(
        model=delta.model,
        input_tokens=total.input_tokens + delta.input_tokens,
        cached_tokens=total.cached_tokens + delta.cached_tokens,
        output_tokens=total.output_tokens + delta.output_tokens,
    )


class CoachClient:
    def __init__(
        self,
        openai: OpenAI,
        fast_model: str,
        plan_model: str,
        *,
        enable_caching: bool = True,
        ttl_stable: str = "12h",
        ttl_session: str = "30m",
        min_cacheable_tokens: int = 1024,
        max_tool_iterations: int = 6,
        plan_timeout_seconds: float = 180.0,
    ) -> None:
        self.openai = openai
        self.models: dict[Tier, str] = {"fast": fast_model, "plan": plan_model}
        self.enable_caching = enable_caching
        self.ttl_stable = ttl_stable
        self.ttl_session = ttl_session
        self.min_cacheable_tokens = min_cacheable_tokens
        self.max_tool_iterations = max_tool_iterations
        # Plan-tier generations are long structured outputs; they need a longer leash than chat.
        self.plan_timeout_seconds = plan_timeout_seconds

    def complete(
        self,
        stable: str,
        history: list[dict],
        volatile: str,
        *,
        tier: Tier = "fast",
        effort: str = "low",
        tools: list[dict] | None = None,
        tool_handlers: dict[str, Callable[[dict], str]] | None = None,
        cache_key: str | None = None,
        json_schema: dict | None = None,
        schema_name: str = "result",
    ) -> LLMReply:
        model = self.models[tier]
        items = self._input_items(stable, history, volatile)
        kwargs: dict = {
            "model": model,
            "reasoning": {"effort": effort},
        }
        if tier == "plan":
            kwargs["timeout"] = self.plan_timeout_seconds
        if self.enable_caching:
            kwargs["prompt_cache_options"] = {"mode": "implicit", "ttl": self.ttl_session}
        if cache_key:
            kwargs["prompt_cache_key"] = cache_key
        if json_schema is not None:
            kwargs["text"] = {
                "format": {"type": "json_schema", "name": schema_name, "schema": json_schema, "strict": True}
            }

        total = LLMUsage.zero(model)
        for _ in range(self.max_tool_iterations):
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            response = self.openai.responses.create(input=items, **kwargs)
            total = _add_usage(total, _usage(model, response.usage))
            calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                return LLMReply(text=_clean(response.output_text or ""), usage=total)
            # Replay everything the model produced (function calls, web-search calls, reasoning)
            # plus our tool outputs; the growing prefix stays cache-friendly. Built-in tools like
            # web_search run inside OpenAI and never need an output item from us.
            items = items + list(response.output)
            for call in calls:
                items.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": self._run_tool(tool_handlers, call)}
                )
        # Loop budget exhausted: one final tool-less call so the user always gets an answer.
        kwargs.pop("tools", None)
        kwargs.pop("tool_choice", None)
        response = self.openai.responses.create(input=items, **kwargs)
        total = _add_usage(total, _usage(model, response.usage))
        return LLMReply(text=_clean(response.output_text or ""), usage=total)

    def _run_tool(self, handlers: dict[str, Callable[[dict], str]] | None, call) -> str:
        handler = (handlers or {}).get(call.name)
        if handler is None:
            return f"error: unknown tool {call.name}"
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            return "error: malformed tool arguments"
        try:
            return handler(arguments)
        except Exception as exc:  # a broken tool must not take down /ask; the model adapts
            logger.warning("tool %s failed: %s", call.name, type(exc).__name__)
            return f"error: {exc}"

    def _input_items(self, stable: str, history: list[dict], volatile: str) -> list[dict]:
        stable_part: dict = {"type": "input_text", "text": stable}
        if (
            self.enable_caching
            and estimate_tokens(stable) >= self.min_cacheable_tokens + CACHE_GATE_MARGIN_TOKENS
        ):
            stable_part["prompt_cache_breakpoint"] = {"mode": "explicit", "ttl": self.ttl_stable}
        items: list[dict] = [{"role": "developer", "content": [stable_part]}]
        for message in history:
            # Replayed assistant turns use output_text; user turns use input_text.
            part_type = "output_text" if message["role"] == "assistant" else "input_text"
            items.append({"role": message["role"], "content": [{"type": part_type, "text": message["content"]}]})
        items.append({"role": "user", "content": [{"type": "input_text", "text": volatile}]})
        return items
