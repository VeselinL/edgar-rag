# AVA P0 evaluation v1

This version freezes the pre-correctness-change behavior of the production
NPZ/BM25/RRF path. It separates company detection, candidate retrieval, final
selection, and citation/source display so a later policy change fails at the
earliest responsible stage.

Run the complete baseline from the repository root:

```bash
.venv/bin/python -m src.evaluation.ava_p0 --overwrite
```

The command uses fixed, reviewed subqueries rather than the provider planner.
This makes retrieval comparisons reproducible and prevents planner drift from
being mistaken for a ranking change. It writes:

- `baseline/baseline_summary.json`: detailed labels, outcomes, company balance,
  BGE-token context proxy, stage failures, and latency;
- `baseline/parity_fixture.json`: timing-independent company, scope, candidate,
  selection, and order contract for API/evaluator parity.

Run a later implementation against the frozen baseline with one command:

```bash
.venv/bin/python -m src.evaluation.ava_p0 \
  --output-directory data/evaluation/ava_p0/v1/runs/phase-N \
  --compare-to data/evaluation/ava_p0/v1/baseline/baseline_summary.json
```

That run also writes `comparison.json` with metric deltas, company-balance and
selected-ID changes, token-proxy movement, latency movement, and a corpus-hash
guard. The frozen baseline is not overwritten.

`final_context_bge_token_proxy` measures the fully formatted context with the
pinned BGE tokenizer because the current generation gateway does not publish its
tokenizer. It is diagnostic only. Phase 3 must configure the real generation
token budget and counter before enforcing context packing.

The 35-node image manifest is label-only and is validated against immutable raw
HTML by `(ticker, DOM ordinal, src)`. It does not download assets. Low-confidence
visual labels must be reviewed after Phase 6 acquires immutable image bytes.

The history cases are frozen acceptance inputs, not claims that persistence,
deletion, or tenant isolation exists in the current stateless application.

Phase 4 adds `generation_quality_v1.json`. It uses reviewed complete filing
chunks and keeps generation/citation grading separate from retrieval. Run its
deterministic reference contract or a fresh provider audit with:

```bash
PYTHONPATH=. .venv/bin/python -m src.evaluation.generation_quality
PYTHONPATH=. .venv/bin/python -m src.evaluation.generation_quality \
  --answers provider --judge-provider --output /tmp/ava-generation-provider.json
```

The optional judge is diagnostic and may vary by provider run. It never selects
runtime sources or changes an answer.
