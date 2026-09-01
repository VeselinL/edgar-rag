"""Measure AVA SSE time-to-first-token and completion latency."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.observability.metrics import percentile


async def one_request(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    conversation_id: str | None,
    csrf_token: str | None,
) -> tuple[float | None, float, bool]:
    payload = {"query": "What does Tesla do?"}
    if conversation_id:
        payload.update(
            {"conversation_id": conversation_id, "client_turn_id": str(uuid4())}
        )
    headers = {
        "Accept": "text/event-stream",
        **({"X-CSRF-Token": csrf_token} if csrf_token else {}),
    }
    async with semaphore:
        started = time.perf_counter()
        first_token: float | None = None
        terminal = False
        try:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line.removeprefix("event:").strip()
                    elif not line:
                        if event == "delta" and first_token is None:
                            first_token = (time.perf_counter() - started) * 1000
                        if event == "done":
                            terminal = True
                        if event == "error":
                            break
                        event = ""
        except (httpx.HTTPError, httpx.StreamError):
            terminal = False
        complete = (time.perf_counter() - started) * 1000
        return first_token, complete, terminal


async def run(args: argparse.Namespace) -> dict:
    cookie = os.getenv("AVA_LOAD_SESSION_COOKIE")
    csrf = os.getenv("AVA_LOAD_CSRF_TOKEN")
    cookies = {"ava_session": cookie, "ava_csrf": csrf} if cookie and csrf else None
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(cookies=cookies, timeout=args.timeout) as client:
        results = await asyncio.gather(
            *(
                one_request(
                    client,
                    f"{args.origin.rstrip('/')}/api/chat/stream",
                    semaphore,
                    args.conversation_id,
                    csrf,
                )
                for _ in range(args.requests)
            )
        )
    first_tokens = [value for value, _, _ in results if value is not None]
    completions = [value for _, value, _ in results]
    successes = sum(terminal for _, _, terminal in results)
    return {
        "mode": args.label,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "successes": successes,
        "errors": args.requests - successes,
        "time_to_first_token_ms": {
            "p50": percentile(first_tokens, 0.50),
            "p95": percentile(first_tokens, 0.95),
            "mean": statistics.fmean(first_tokens) if first_tokens else None,
        },
        "complete_latency_ms": {
            "p50": percentile(completions, 0.50),
            "p95": percentile(completions, 0.95),
            "mean": statistics.fmean(completions),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:18080")
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--conversation-id")
    parser.add_argument("--label", default="production-like")
    args = parser.parse_args()
    if args.requests <= 0 or args.concurrency <= 0:
        raise ValueError("Requests and concurrency must be positive.")
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
