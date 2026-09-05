# Controlled diagnostic-judge calibration v2

This packet contains 24 source-bounded, pre-labeled answer pairs derived from
four frozen filing chunks. Before the diagnostic call, the evaluator recorded
the rubric label for each pair. The GPT-4o judge received only the question,
the relevant full filing chunk (or no evidence for a proper-abstention case),
and candidate answers. It received no labels.

The first run in `../judge-controlled-calibration-v1/` is retained as a prompt
diagnostic: its abstention instruction was ambiguous and caused ordinary
evidence-supported answers to be marked as abstention failures. Version 2 makes
the abstention contract explicit and is the evaluable run.

## Result

All 24 calls completed at temperature `0`. Aggregate field agreement was
`0.7778` across 144 decisions. This does **not** validate GPT-4o for scaled
judging:

- claim correctness, faithfulness, and citation support each had failure recall
  `0.50` and false-pass rate `0.50`;
- answer relevance had failure recall `0.375` and false-pass rate `0.625`;
- GPT-4o accepted all four deliberately false abstentions on claim correctness,
  faithfulness, citation support, and relevance;
- conciseness was the only field with zero false passes in this packet.

The authoritative perturbation labels are evaluator labels, not an independent
second-human review. The packet tests obvious, source-bounded defects only; it
does not certify nuanced financial interpretation or production-answer quality.
The human review remains authoritative and the LLM judge remains diagnostic.
