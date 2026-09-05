from src.scripts.run_controlled_judge_calibration import FIELDS, _judge_prompt, build_packet


def test_controlled_packet_is_labeled_before_the_judge_prompt() -> None:
    packet = build_packet()

    assert packet["pair_count"] == 24
    assert len(packet["pairs"]) == 24
    for pair in packet["pairs"]:
        assert set(pair["authoritative_rubric"]) == set(FIELDS)
        prompt = _judge_prompt(pair)[-1]["content"]
        assert "authoritative_rubric" not in prompt
        assert "\"claim_correctness\":\"pass|fail\"" in prompt
