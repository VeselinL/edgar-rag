"""Run one bounded Tavily search through AVA's production adapter."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from src.config.settings import PipelineSettings
from src.orchestration.models import TrustedSourceKey
from src.tools.web_search import TavilyWebSearchTool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = PipelineSettings.from_environment()
    tool = TavilyWebSearchTool(
        settings.web_search_api_key or "",
        timeout_seconds=settings.web_search_timeout_seconds,
        api_url=settings.web_search_api_url,
    )
    try:
        response = tool.search(
            "Tesla investor relations current leadership",
            max_results=3,
            source_keys=(TrustedSourceKey.ISSUER_OFFICIAL,),
            tickers=("TSLA",),
        )
    finally:
        tool.close()
    payload = {
        "schema_version": 1,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "provider": response.provider,
        "query": response.query,
        "result_count": len(response.results),
        "allowlisted_results": [
            {"publisher": item.publisher, "url": item.url, "retrieved_at": item.retrieved_at}
            for item in response.results
        ],
        "gate_pass": bool(response.results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"provider": response.provider, "result_count": len(response.results), "gate_pass": bool(response.results)}))


if __name__ == "__main__":
    main()
