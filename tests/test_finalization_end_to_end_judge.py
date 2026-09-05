import pytest

from src.scripts.judge_finalization_end_to_end import FIELDS, _parse


def test_end_to_end_judge_requires_complete_binary_rubric() -> None:
    valid = "{" + ",".join(f'\"{field}\":\"pass\"' for field in FIELDS) + "}"

    assert _parse(valid) == {field: "pass" for field in FIELDS}
    with pytest.raises(ValueError):
        _parse('{"claim_correctness":"pass"}')
