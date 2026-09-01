"""Measure AVA SSE time-to-first-token and completion latency."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def one_request(
    url: str,
    conversation_id: str | None,
    csrf_token: str | None,
    session_cookie: str | None,
    timeout: float,
) -> tuple[float | None, float, bool]:
    payload = {"query": "What does Tesla do?"}
    if conversation_id:
        payload.update(
            {"conversation_id": conversation_id, "client_turn_id": str(uuid4())}
        )
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        **({"X-CSRF-Token": csrf_token} if csrf_token else {}),
        **(
            {"Cookie": f"ava_session={session_cookie}; ava_csrf={csrf_token}"}
            if session_cookie and csrf_token
            else {}
        ),
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    first_token: float | None = None
    terminal = False
    try:
        with urlopen(request, timeout=timeout) as response:
            event = ""
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
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
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError):
        terminal = False
    complete = (time.perf_counter() - started) * 1000
    return first_token, complete, terminal


def run(args: argparse.Namespace) -> dict:
    session_cookie = os.getenv("AVA_LOAD_SESSION_COOKIE")
    csrf = os.getenv("AVA_LOAD_CSRF_TOKEN")
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = list(
            executor.map(
                lambda _: one_request(
                    f"{args.origin.rstrip('/')}/api/chat/stream",
                    args.conversation_id,
                    csrf,
                    session_cookie,
                    args.timeout,
                ),
                range(args.requests),
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
    result = run(args)
    print(json.dumps(result, indent=2))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
