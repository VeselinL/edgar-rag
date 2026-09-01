"""Small ASGI production controls with no external runtime dependency."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any, Callable
from uuid import uuid4

from starlette.responses import JSONResponse


@dataclass(frozen=True)
class OperationalSettings:
    maximum_body_bytes: int = 16_384
    maximum_upload_bytes: int = 20 * 1024 * 1024
    requests_per_minute: int = 60
    stream_timeout_seconds: int = 180

    @classmethod
    def from_environment(cls) -> "OperationalSettings":
        settings = cls(
            maximum_body_bytes=int(os.getenv("AVA_MAX_BODY_BYTES", "16384")),
            maximum_upload_bytes=int(
                os.getenv("AVA_UPLOAD_MAX_BODY_BYTES", str(20 * 1024 * 1024))
            ),
            requests_per_minute=int(os.getenv("AVA_REQUESTS_PER_MINUTE", "60")),
            stream_timeout_seconds=int(os.getenv("AVA_STREAM_TIMEOUT_SECONDS", "180")),
        )
        if min(
            settings.maximum_body_bytes,
            settings.maximum_upload_bytes,
            settings.requests_per_minute,
            settings.stream_timeout_seconds,
        ) <= 0:
            raise ValueError("AVA operational limits must be positive.")
        return settings


class BodyLimitMiddleware:
    def __init__(self, app: Any, maximum_bytes: int, maximum_upload_bytes: int | None = None) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes
        self.maximum_upload_bytes = maximum_upload_bytes or maximum_bytes

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header_map = dict(scope.get("headers", []))
        path = scope.get("path", "")
        limit = (
            self.maximum_upload_bytes
            if path.startswith("/api/conversations/") and path.endswith("/documents")
            else self.maximum_bytes
        )
        content_length = header_map.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    await JSONResponse(
                        {"detail": "Request body is too large."}, status_code=413
                    )(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse(
                    {"detail": "Invalid Content-Length header."}, status_code=400
                )(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                await JSONResponse(
                    {"detail": "Request body is too large."}, status_code=413
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class SlidingWindowLimiter:
    def __init__(self, limit: int, *, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            entries = self._entries[key]
            while entries and entries[0] <= now - self.window_seconds:
                entries.popleft()
            if len(entries) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - entries[0])))
                return False, retry_after
            entries.append(now)
            return True, 0


class OperationalMiddleware:
    EXEMPT_PATHS = {"/api/live", "/api/ready", "/api/health"}

    def __init__(self, app: Any, requests_per_minute: int) -> None:
        self.app = app
        self.limiter = SlidingWindowLimiter(requests_per_minute)
        self.logger = logging.getLogger("ava.http")

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        path = scope.get("path", "")
        method = scope.get("method", "")
        client = scope.get("client") or ("unknown", 0)
        if method != "OPTIONS" and path not in self.EXEMPT_PATHS:
            allowed, retry_after = await self.limiter.allow(str(client[0]))
            if not allowed:
                response = JSONResponse(
                    {"detail": "Too many requests. Please retry shortly."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after), "X-Request-ID": request_id},
                )
                await response(scope, receive, send)
                return
        status_code = 500
        started = time.perf_counter()

        async def add_headers(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"DENY"),
                    ]
                )
                if path.startswith("/api/auth/"):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, add_headers)
        finally:
            self.logger.info(
                "AVA HTTP request completed",
                extra={
                    "ava_http": {
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "status_code": status_code,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    }
                },
            )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "ava_http",
            "ava_request",
            "ava_startup",
            "ava_company_resolution",
            "ava_planner_ambiguity",
            "ava_validated_ambiguity",
        ):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        if record.exc_info:
            value["error_class"] = record.exc_info[0].__name__
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def configure_json_logging() -> None:
    enabled = os.getenv("AVA_JSON_LOGS", "false").strip().casefold()
    if enabled not in {"true", "false"}:
        raise ValueError("AVA_JSON_LOGS must be true or false.")
    if enabled == "false":
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("AVA_LOG_LEVEL", "INFO").upper())
