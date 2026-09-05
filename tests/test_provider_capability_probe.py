from types import SimpleNamespace

from src.scripts import probe_provider_capabilities as capability_probe


class _Response:
    choices = [object()]

    def model_dump(self):
        return {"choices": []}


class _Completions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **arguments):
        self.calls.append(arguments)
        if "max_tokens" in arguments:
            raise ValueError("Use max_completion_tokens instead.")
        return _Response()


def test_capability_probe_uses_application_token_compatibility_retry(monkeypatch) -> None:
    completions = _Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(capability_probe.ProviderSettings, "from_environment", lambda: object())
    monkeypatch.setattr(capability_probe, "make_llm_client", lambda _settings: client)
    monkeypatch.setattr(capability_probe, "_stream_response", lambda _service, _messages: _Response())

    result = capability_probe.probe("AZURE_GPT_51_2025_1113")

    assert result["ordinary_chat"]["supported"] is True
    assert result["strict_json"]["supported"] is True
    assert result["required_function"]["supported"] is True
    assert any("max_completion_tokens" in call for call in completions.calls)
