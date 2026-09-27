"""LLM provider abstraction (guide §29).

The LLM is only used to (a) understand free-form questions the rule-based intent
parser cannot, and (b) phrase explanations of already-computed results. It never
computes risk. Every provider returns schema-conforming JSON or None; callers
always have a deterministic fallback."""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("orca.llm")


@dataclass
class LLMResult:
    data: dict[str, Any] | None
    provider: str
    model: str | None
    latency_ms: float
    error: str | None = None
    served_by: str | None = None


class LLMProvider(ABC):
    name = "none"
    model: str | None = None

    @property
    def available(self) -> bool:
        return False

    @abstractmethod
    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult: ...


class NullProvider(LLMProvider):
    name = "none"

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        return LLMResult(data=None, provider=self.name, model=None, latency_ms=0.0, error="no LLM configured")


class AnthropicProvider(LLMProvider):
    """Claude via the official Anthropic SDK with structured JSON output.

    Defaults: model claude-opus-5 (override ORCA_ANTHROPIC_MODEL), effort 'low'
    (override ORCA_LLM_EFFORT) because explanations are short, latency-sensitive
    rewrites of structured evidence. Server-side refusal fallbacks are enabled
    ('default' routing) so a classifier decline is retried on Anthropic's
    recommended fallback model instead of failing the explanation."""

    name = "anthropic"

    def __init__(self, model: str | None = None, effort: str | None = None, timeout_s: float = 30.0) -> None:
        import anthropic

        self.model = model or os.getenv("ORCA_ANTHROPIC_MODEL", "claude-opus-5")
        self.effort = effort or os.getenv("ORCA_LLM_EFFORT", "low")
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(timeout=timeout_s, max_retries=1)

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        anthropic = self._anthropic
        started = time.perf_counter()
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.RateLimitError as exc:
            return self._fail(started, f"rate limited: {exc}")
        except anthropic.APIStatusError as exc:
            return self._fail(started, f"API error {exc.status_code}: {exc.message}")
        except anthropic.APIConnectionError as exc:
            return self._fail(started, f"connection error: {exc}")
        latency = (time.perf_counter() - started) * 1000
        if response.stop_reason == "refusal":
            return LLMResult(None, self.name, self.model, latency, error="model declined (refusal)", served_by=response.model)
        if response.stop_reason == "max_tokens":
            return LLMResult(None, self.name, self.model, latency, error="output truncated (max_tokens)", served_by=response.model)
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            return LLMResult(None, self.name, self.model, latency, error="no text block in response", served_by=response.model)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return LLMResult(None, self.name, self.model, latency, error=f"invalid JSON: {exc}", served_by=response.model)
        return LLMResult(data, self.name, self.model, latency, served_by=response.model)

    def _fail(self, started: float, error: str) -> LLMResult:
        log.warning("anthropic provider failed: %s", error)
        return LLMResult(None, self.name, self.model, (time.perf_counter() - started) * 1000, error=error)


class OpenAICompatibleProvider(LLMProvider):
    """Any OpenAI-compatible chat API with strict JSON-schema output (Groq, Gemini's OpenAI endpoint)."""

    name = "openai-compatible"
    URL = ""
    KEY_ENV = ""
    MODEL_ENV = ""
    DEFAULT_MODEL = ""

    def __init__(self, model: str | None = None, timeout_s: float = 30.0) -> None:
        import httpx

        self.model = model or os.getenv(self.MODEL_ENV, self.DEFAULT_MODEL)
        self._httpx = httpx
        self._client = httpx.AsyncClient(timeout=timeout_s, headers={"Authorization": f"Bearer {os.environ[self.KEY_ENV]}"})

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        started = time.perf_counter()
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "orca_output", "schema": schema, "strict": True}},
            "temperature": 0,
        }
        try:
            response = await self._client.post(self.URL, json=body)
        except self._httpx.HTTPError as exc:
            return self._fail(started, f"connection error: {type(exc).__name__}")
        latency = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            # Provider error text can name the account/organisation; keep it in the server log, not in user-facing notes.
            log.warning("%s API error %s: %s", self.name, response.status_code, response.text[:500])
            if response.status_code == 429:
                retry = response.headers.get("retry-after")
                return self._fail(started, "rate limited" + (f"; retry in {retry} s" if retry else ""), logged=True)
            return self._fail(started, f"API error {response.status_code}", logged=True)
        payload = response.json()
        choice = payload["choices"][0]
        served_by = payload.get("model")
        if choice.get("finish_reason") == "length":
            return LLMResult(None, self.name, self.model, latency, error="output truncated (max tokens)", served_by=served_by)
        try:
            data = json.loads(choice["message"]["content"] or "")
        except json.JSONDecodeError as exc:
            return LLMResult(None, self.name, self.model, latency, error=f"invalid JSON: {exc}", served_by=served_by)
        return LLMResult(data, self.name, self.model, latency, served_by=served_by)

    def _fail(self, started: float, error: str, logged: bool = False) -> LLMResult:
        if not logged:
            log.warning("%s provider failed: %s", self.name, error)
        return LLMResult(None, self.name, self.model, (time.perf_counter() - started) * 1000, error=error)


class GroqProvider(OpenAICompatibleProvider):
    """Groq. Default model openai/gpt-oss-120b (override ORCA_GROQ_MODEL); key GROQ_API_KEY."""

    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"
    KEY_ENV, MODEL_ENV, DEFAULT_MODEL = "GROQ_API_KEY", "ORCA_GROQ_MODEL", "openai/gpt-oss-120b"


class GeminiProvider(OpenAICompatibleProvider):
    """Google Gemini via its OpenAI-compatible endpoint. Default gemini-2.5-flash (override ORCA_GEMINI_MODEL);
    key GEMINI_API_KEY."""

    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    KEY_ENV, MODEL_ENV, DEFAULT_MODEL = "GEMINI_API_KEY", "ORCA_GEMINI_MODEL", "gemini-2.5-flash"


class FallbackProvider(LLMProvider):
    """Tries each provider in order until one returns JSON (e.g. Groq rate-limited → Gemini)."""

    def __init__(self, providers: list[LLMProvider]) -> None:
        self.providers = providers
        self.name = "+".join(p.name for p in providers)
        self.model = " / ".join(p.model or "?" for p in providers)

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        errors: list[str] = []
        latency = 0.0
        for p in self.providers:
            result = await p.complete_json(system, user, schema)
            latency += result.latency_ms
            if result.data is not None:
                result.latency_ms = latency
                return result
            errors.append(f"{p.name}: {result.error}")
        return LLMResult(None, self.name, self.model, latency, error="; ".join(errors))


@dataclass
class ScriptedProvider(LLMProvider):
    """Deterministic provider for tests: returns queued responses in order."""

    responses: list[dict[str, Any] | None] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    name: str = "scripted"
    model: str | None = "scripted-model"

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        self.calls.append({"system": system, "user": user, "schema": schema})
        data = self.responses.pop(0) if self.responses else None
        return LLMResult(data, self.name, self.model, 1.0, error=None if data is not None else "no scripted response")


def provider_from_env() -> LLMProvider:
    """ORCA_LLM_PROVIDER: auto (every provider with a key, in the order groq → gemini → anthropic, each falling
    back to the next) | groq | gemini | anthropic | none."""
    choice = os.getenv("ORCA_LLM_PROVIDER", "auto").lower()
    if choice == "none":
        return NullProvider()
    chain: list[LLMProvider] = []
    for name, cls, has_key in (
        ("groq", GroqProvider, bool(os.getenv("GROQ_API_KEY"))),
        ("gemini", GeminiProvider, bool(os.getenv("GEMINI_API_KEY"))),
        ("anthropic", AnthropicProvider, bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))),
    ):
        if choice == name or (choice == "auto" and has_key):
            try:
                chain.append(cls())
            except ImportError:
                log.warning("anthropic package not installed; skipping it (pip install 'orca[llm]')")
    if not chain:
        return NullProvider()
    return chain[0] if len(chain) == 1 else FallbackProvider(chain)
