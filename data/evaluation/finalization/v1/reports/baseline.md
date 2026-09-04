# Phase 4 baseline — frozen release candidate

## Scope and reproducibility

- Freeze manifest: `data/evaluation/finalization/v1/freeze_manifest.json`
- Frozen source commit: `cb17e84cff19fa986e3cd34807cd8d412bb8a0c9`
- Corpus/index artifact: `sha256:89d3a5be9e7d7a8e0d1188f4a5564c7be9bf01b92e381a7410e048a07738610c`
- Runtime: real Azure provider, primary Qdrant, Tavily enabled, calculator disabled,
  conversation history disabled, uploads disabled, buffered provider delivery.
- Every run below validates the freeze manifest before it begins. The raw records
  retain the original query, route, evidence selection, tool records, answer,
  validated citations, visible sources, stage latencies, and safe error class.

## Ground truth

- QA/generation: 75 reviewed cases in the required category distribution.
- Route/tool: 60 reviewed cases; deterministic routed result is 60/60 pass.
- Conversation: 12 scenarios; memory: 20 scenarios; security: 24 cases;
  Serbian/English: 20 paired prompts. Their frozen manifests are intact.
- The blinded answer packet contains 20 actual provider answer pairs. One human
  reviewer completed all pairs: correctness, faithfulness, citation support,
  abstention, and relevance passed in all 20; conciseness passed in 18/20.
  Preference was A in 2, B in 4, and tie in 14.

The human review is intentionally recorded as provisional in
`runs/judge-calibration-v1/summary.json`: the user authorized one reviewer, so
there is no two-human inter-reviewer agreement or Cohen's kappa. The packet
contains reviewed claims rather than complete excerpts, so it is not a
full-excerpt entailment certification.

An independent diagnostic LLM subsequently scored the same 20 pairs using the
full chunks cited by either answer plus matching gold chunks. It agreed with the
human pair-level rubric on `0.95` of 120 decisions. Aggregate Cohen's kappa was
`-0.0227`, and per-field kappas were either `0` or undefined because the human
labels are almost entirely `pass`; this is a prevalence limitation, not evidence
of a reliable calibrated judge. The raw output and exact limitations are in
`runs/judge-calibration-v1/llm_judge.jsonl` and `llm_judge_summary.json`.

## Layered baseline results

| Layer | Result | Artifact |
|---|---:|---|
| Retriever only | candidate recall `0.7891`; final gold survival `1.0`; hit@50 `0.8594`; MRR@50 `0.5050` | `runs/retriever-only-v1` |
| Oracle generation (three runs) | source-display exactness `1.0`; invalid citations `0`; abstention accuracy `0.9067` | `runs/generator-oracle-context-*`, `runs/generator-oracle-context-aggregate.json` |
| Planner + retriever (three runs) | scope accuracy `1.0`; candidate recall `0.7891`; gold survival `0.9811` | `runs/planner-retriever-*`, `runs/planner-retriever-aggregate.json` |
| Route/tool manifest | 60/60 pass; web-required recall `1.0`; unnecessary web rate `0`; calculator false positives `0` | `runs/agent-routes-v1` |
| End to end (three real runs, errors count as failures) | contract success mean `0.7378`, SD `0.0077`, worst `0.7333` | `runs/end-to-end-run-{1,2,3}`, `runs/end-to-end-aggregate` |

The end-to-end aggregate uses 10,000 paired case-index bootstrap resamples with
seed `20260905`; its per-metric 95% confidence intervals are saved in
`runs/end-to-end-aggregate/summary.json`.

## End-to-end diagnosis

| Metric | Mean | Worst run | Status |
|---|---:|---:|---|
| Request success | `0.9244` | `0.9200` | failure — routing validation errors remain |
| Route accuracy | `0.8311` | `0.8267` | below `0.90` improvement trigger |
| Tool-sequence exactness | `0.8311` | `0.8267` | below `0.90` improvement trigger |
| Abstention accuracy | `0.8533` | `0.8533` | below `0.90` improvement trigger |
| Citation validity | `0.9244` | `0.9200` | all completed answers had zero rejected citations; errors count as failures here |
| Source-display proxy | `0.9244` | `0.9200` | `1.0` on every completed answer; errors count as failures here |
| Current-web execution | `0.6000` | `0.6000` | failed — two current-leadership cases route to filing |
| Calculator execution | `0` | `0` | unavailable — calculator is disabled |
| Unexpected SSE `status` events | `0.9111` | `0.9200` | failed API event contract |
| Mean total latency | `3576.5 ms` | `4349.9 ms` | measured |
| p95 total latency | `5579.5 ms` | `5731.7 ms` | measured |
| Maximum total latency | `35829.9 ms` | `93773.8 ms` | measured; includes one provider outlier |

Earliest-stage findings are saved per case in
`runs/end-to-end-aggregate/failures.jsonl`. The repeated causes are:

1. routing/validation failures on follow-up context and one calculation case;
2. calculator routes deliberately downgraded because the calculator is disabled;
3. two current-leadership requests routed to filing rather than trusted web;
4. unsupported absence answers that make affirmative claims rather than abstain;
5. the backend emits `status` alongside the required `delta`, `sources`, `done`,
   and `error` SSE events.

No correction has been made in Phase 4.

## Security and required-runtime results

- All eight live direct-injection calls were safe: no system-prompt, API-key, or
  raw chunk-ID disclosure, and no transport errors. See
  `runs/security-direct-live-v1`.
- The upload and controlled web-indirect security cases could not run live:
  uploads are disabled and there is no controllable untrusted-web fixture in the
  real Tavily path. Existing document-boundary tests are saved separately in
  `runs/document-security-tests-v1`.
- Live health reports conversation history disabled, uploads disabled, and
  calculator disabled. `POST /api/conversations` and document listing each return
  safe `503` responses. The OpenAPI `ChatRequest` has no language field.
- Consequently all 12 conversation, 20 memory, 16 upload/web-indirect security,
  and 20 language cases are recorded as unavailable baseline failures in
  `runs/runtime-feature-availability-v1`, not as passes.

## Deterministic delivery gate

`runs/full-gate-v1` records:

- backend: `344 passed, 3 skipped`, `251 subtests passed`;
- frontend lint: pass;
- frontend tests: `31 passed`;
- frontend production build: pass.

The freeze manifest validates after every committed artifact, including this
baseline measurement set.

## Phase status

The Phase 4 measurement artifacts are complete under the one-human plus
diagnostic-LLM protocol now defined in `FINALIZATION.md`. The phase therefore
passes its **measurement** exit gate and supplies the baseline for Phase 5. It
does not pass release-readiness gates: conversation, memory, upload, language,
and full-security execution manifests are unavailable in the frozen runtime, and
the measured end-to-end results identify hard failures for correction. Do not
claim release readiness from this baseline.
