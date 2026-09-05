"""Probe AVA's configured Chat Completions gateway without persisting secrets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.config.settings import DEFAULT_LLM_MODEL, ProviderSettings
from src.generation.provider import make_llm_client, require_streaming_response
from src.generation.service import GenerationService


def _shape(value: Any) -> dict[str, Any]:
    dumped = value.model_dump() if callable(getattr(value, "model_dump", None)) else {}
    return {
        "type": type(value).__name__,
        "top_level_keys": sorted(dumped) if isinstance(dumped, dict) else [],
        "choice_count": len(getattr(value, "choices", []) or []),
    }


def _attempt(call) -> dict[str, Any]:
    try:
        return {"supported": True, "response": _shape(call())}
    except Exception as error:
        return {"supported": False, "safe_error_class": type(error).__name__}


def probe(model: str) -> dict[str, Any]:
    settings = ProviderSettings.from_environment()
    client = make_llm_client(settings)
    service = GenerationService(client, model=model, max_output_tokens=8)
    messages = [{"role": "user", "content": "Reply with OK."}]
    ordinary = _attempt(lambda: service._create(model=model, messages=messages, max_tokens=8, temperature=0))
    strict_json = _attempt(lambda: service._create(model=model, messages=messages, max_tokens=8, temperature=0, response_format={"type": "json_object"}))
    stream = _attempt(lambda: _stream_response(service, messages))
    function = _attempt(lambda: service._create(
        model=model, messages=messages, max_tokens=8, temperature=0,
        tools=[{"type": "function", "function": {"name": "capability_probe", "description": "Harmless capability probe.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}],
        tool_choice="required",
    ))
    return {
        "schema_version": 1,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "endpoint_family": "openai_compatible_chat_completions",
        "model": model,
        "ordinary_chat": ordinary,
        "strict_json": strict_json,
        "streaming": stream,
        "required_function": function,
        "native_tools_usable": function["supported"],
    }


def _stream_response(service: GenerationService, messages: list[dict[str, str]]) -> Any:
    response = service._create(
        model=service.model, messages=messages, max_tokens=8, temperature=0, stream=True,
    )
    try:
        require_streaming_response(response)
        next(iter(response), None)
        return response
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("ordinary_chat", "strict_json", "streaming", "required_function", "native_tools_usable")}, indent=2))


if __name__ == "__main__":
    main()
