"""Verify health and an SSE exchange through the production reverse proxy."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:18080")
    parser.add_argument("--startup-timeout", type=float, default=30)
    args = parser.parse_args()
    deadline = time.monotonic() + args.startup_timeout
    while True:
        try:
            with urlopen(f"{args.origin}/api/live", timeout=2) as response:
                if response.status == 200:
                    break
        except URLError:
            if time.monotonic() >= deadline:
                raise RuntimeError("AVA proxy did not become live before the deadline.")
            time.sleep(0.25)
    request = Request(
        f"{args.origin}/api/chat/stream",
        data=json.dumps({"query": "What does Tesla do?"}).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        if response.headers.get_content_type() != "text/event-stream":
            raise RuntimeError("Proxy did not preserve the SSE content type.")
        body = response.read().decode("utf-8")
    events = [
        line.removeprefix("event: ")
        for line in body.splitlines()
        if line.startswith("event: ")
    ]
    if events != ["delta", "delta", "delta", "sources", "done"]:
        raise RuntimeError(f"Unexpected SSE event order: {events}")
    print(json.dumps({"live": True, "events": events}))


if __name__ == "__main__":
    main()
