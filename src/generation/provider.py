"""OpenAI-compatible provider construction and streaming primitives."""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

from openai import OpenAI

from src.config.settings import ProviderSettings


@dataclass(frozen=True)
class GenerationResult:
    text: str
    usage: dict[str, int]


def provider_usage(value: Any) -> dict[str, int]:
    """Normalize only numeric token counts from provider-specific usage objects."""
    if value is None:
        return {}
    payload = value.model_dump() if callable(getattr(value, "model_dump", None)) else value
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    if isinstance(payload, dict):
        return {
            field: int(payload[field])
            for field in fields
            if isinstance(payload.get(field), (int, float))
        }
    return {
        field: int(getattr(payload, field))
        for field in fields
        if isinstance(getattr(payload, field, None), (int, float))
    }


class GenerationStream:
    """Provider fragment iterator that retains terminal usage without fake tokens."""

    def __init__(self, response: Any, *, breaker: "ProviderCircuitBreaker | None" = None) -> None:
        self.response = response
        self.usage: dict[str, int] = {}
        self.breaker = breaker

    def __iter__(self) -> Iterator[str]:
        completed = False
        try:
            for chunk in self.response:
                observed_usage = provider_usage(getattr(chunk, "usage", None))
                if observed_usage:
                    self.usage = observed_usage
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                yield from content_fragments(getattr(delta, "content", None))
            completed = True
        except BaseException:
            if self.breaker is not None:
                self.breaker.record_failure()
            raise
        finally:
            if completed and self.breaker is not None:
                self.breaker.record_success()
            self.close()

    def close(self) -> None:
        close = getattr(self.response, "close", None)
        if callable(close):
            close()


class ProviderCircuitOpenError(RuntimeError):
    pass


class ProviderCircuitBreaker:
    """Thread-safe consecutive-failure breaker with one half-open probe."""

    def __init__(self, failure_threshold: int = 5, recovery_seconds: float = 30.0) -> None:
        if failure_threshold <= 0 or recovery_seconds <= 0:
            raise ValueError("Circuit-breaker threshold and recovery time must be positive.")
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = Lock()

    def before_request(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            if time.monotonic() - self._opened_at < self.recovery_seconds:
                raise ProviderCircuitOpenError("The model provider circuit is open.")
            if self._probe_in_flight:
                raise ProviderCircuitOpenError("The model provider recovery probe is busy.")
            self._probe_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._probe_in_flight = False
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()


def make_llm_client(settings: ProviderSettings | None = None) -> OpenAI:
    settings = settings or ProviderSettings.from_environment()
    settings.validate(required=True)
    return OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=settings.maximum_retries,
        default_headers={
            key: value
            for key, value in {
                "x-app-id": settings.app_id,
                "x-user-id": settings.user_id,
                "x-company-id": settings.company_id,
                "x-api-version": settings.api_version,
            }.items()
            if value
        },
    )


def content_fragments(content: Any) -> Iterable[str]:
    if isinstance(content, str):
        if content:
            yield content
        return
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and part:
                yield part
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                if part["text"]:
                    yield part["text"]
            elif isinstance(getattr(part, "text", None), str) and part.text:
                yield part.text


def require_streaming_response(response: Any) -> None:
    """Reject gateways that buffer a streaming request as JSON."""
    raw_response = getattr(response, "response", None) or getattr(
        response, "_response", None
    )
    content_type = (
        getattr(raw_response, "headers", {}).get("content-type", "")
        if raw_response is not None
        else ""
    )
    if content_type and not content_type.casefold().startswith("text/event-stream"):
        raise RuntimeError("The configured LLM gateway did not provide a streaming response.")
