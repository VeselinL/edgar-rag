# GPT-4o scaled diagnostic judge

This run scored the three frozen 75-case end-to-end outputs (`225` records) with
the same six-field rubric used in the calibration packet. The judge saw reviewed
gold claims, the generated answer, and the displayed evidence; it did not see
any authoritative answer-grade labels.

## Result

- `208` records completed a provider judgment; `17` pre-existing pipeline errors
  were deterministically recorded as failures without a provider judgment.
- Diagnostic pass rates were claim correctness `0.8133`, faithfulness `0.8000`,
  citation support `0.7956`, abstention `0.7600`, answer relevance `0.8356`, and
  conciseness `0.8178`.

These scores are not a validated release-quality measure. The controlled
calibration in `../judge-controlled-calibration-v2/` found that GPT-4o accepted
materially bad claims, citations, abstentions, and relevance failures. The
reviewed gold, deterministic route/tool checks, citation validation, and human
review remain authoritative.
