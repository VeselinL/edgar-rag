from pathlib import Path
from types import SimpleNamespace

from src.scripts.run_controlled_judge_calibration import (
    FIELDS,
    _judge_prompt,
    build_packet,
    build_reviewed_packet,
    judge_packet,
)


def test_controlled_packet_is_labeled_before_the_judge_prompt() -> None:
    packet = build_packet()

    assert packet["pair_count"] == 24
    assert len(packet["pairs"]) == 24
    for pair in packet["pairs"]:
        assert set(pair["authoritative_rubric"]) == set(FIELDS)
        prompt = _judge_prompt(pair)[-1]["content"]
        assert "authoritative_rubric" not in prompt
        assert "\"claim_correctness\":\"pass|fail\"" in prompt


def test_reviewed_packet_supplies_cited_chunks_without_human_labels() -> None:
    packet = build_reviewed_packet(
        Path("data/evaluation/finalization/v1/runs/judge-calibration-v1/blinded_pairs.json")
    )

    assert packet["pair_count"] == 20
    cited_pair = next(pair for pair in packet["pairs"] if pair["evidence"])
    assert "authoritative_rubric" not in _judge_prompt(cited_pair)[-1]["content"]


def test_judge_uses_application_token_compatibility_retry(monkeypatch) -> None:
    rubric_json = '{"claim_correctness":"pass","faithfulness":"pass","citation_support":"pass","abstention":"pass","answer_relevance":"pass","conciseness":"pass"}'

    class Completions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **arguments):
            self.calls.append(arguments)
            if "max_tokens" in arguments:
                raise ValueError("Use max_completion_tokens instead.")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=rubric_json))],
                usage=None,
            )

    completions = Completions()
    monkeypatch.setattr(
        "src.scripts.run_controlled_judge_calibration.make_llm_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    packet = {
        "review_method": "test",
        "pairs": [{
            "pair_id": "test-1", "variant": "control", "question": "Question?",
            "evidence": [], "answer_a": "Answer.", "answer_b": "Answer.",
            "authoritative_rubric": {field: "pass" for field in FIELDS},
        }],
    }

    records, summary = judge_packet(packet, "AZURE_GPT_51_2025_1113")

    assert records[0]["error"] is None
    assert summary["completed_count"] == 1
    assert any("max_completion_tokens" in call for call in completions.calls)
