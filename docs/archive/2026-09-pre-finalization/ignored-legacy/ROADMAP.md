> **Archived 4 September 2026.** This previously ignored local document is
> preserved as a read-only historical record, not a current plan or authority.
> See [`FINALIZATION.md`](../../../../FINALIZATION.md) for the sole remaining-work plan.
>
# AVA — Project Roadmap

**Status date:** 21 August 2026
**Product:** AVA — Autonomous Vehicle Analyst
**Internal repository:** historical repository naming may remain in code and artifacts, but is not product-facing.

## Fixed corpus

The corpus now contains exactly these eleven annual filings:

| Company | Ticker | Filing |
|---|---:|---:|
| Aptiv | APTV | 2025 10-K |
| Aurora Innovation | AUR | 2025 10-K |
| Ford | F | 2025 10-K |
| General Motors | GM | 2025 10-K |
| Alphabet | GOOGL | 2025 10-K |
| Mobileye | MBLY | 2025 10-K |
| NVIDIA | NVDA | 2026 10-K |
| Ouster | OUST | 2025 10-K |
| Qualcomm | QCOM | 2025 10-K |
| Rivian | RIVN | 2025 10-K |
| Tesla | TSLA | 2025 10-K |

## Completed work

### Acquisition, processing, chunking, and embeddings

- [x] Store one normal SEC-hosted 10-K and reproducibility metadata for every approved company without mutating raw HTML.
- [x] Extract ordered sections, narrative, lists, and tables with source provenance.
- [x] Produce table-schema-v2/chunk-schema-v3 artifacts for all eleven companies.
- [x] Produce 4,526 aligned chunks: 3,561 narrative and 965 complete logical-table chunks.
- [x] Use recursive 500-token narrative chunks with 32 configured overlap and retain one complete table per table chunk.
- [x] Produce aligned normalized 768-dimensional BGE-base v1.5 vectors and manifests for all eleven filings.
- [x] Pass strict live table, provenance, chunk, vector-shape, hash, ordering, and normalization audits.

### Retrieval evaluation

- [x] Implement corpus-wide dense BGE retrieval evaluation.
- [x] Implement BM25 retrieval evaluation.
- [x] Implement dense/BM25 reciprocal-rank fusion evaluation.
- [x] Add regex company-name, ticker, and alias detection.
- [x] Add query-only scope classification, including existing Comparison Cues and enumeration cues.
- [x] Save scope-aware hybrid retrieval evaluation runs with Recall and MRR by question type and company.
- [x] Preserve Mobileye gold-v2 evidence and a separately saved semantic baseline.

## Current generation-robustness work

This stream is complete only when the checked-in implementation and tests demonstrate all criteria below:

- [x] Extract the grounded prompt, LLM planner, 10-chunk context limit, context formatting, and citation syntax from `notebooks/hybrid_rag_generation.ipynb` into independently importable Python.
- [x] Use a shared scope-aware retrieval entry point from the notebook, `src/scripts/evaluate_scope_aware_hybrid_retrieval.py`, and FastAPI.
- [x] For representative queries, report identical detected companies, comparison status, retrieval scopes, selected-evidence companies, final count, and pre-normalization chunk IDs across evaluation and API paths.
- [x] Retrieve 10 candidates per planner subquery and preserve at least 2 available chunks for every subquery.
- [x] Merge and deduplicate scoped candidates by stable chunk ID.
- [x] Apply the notebook's `0.01` bonus to chunks matching multiple subqueries before relevance fill.
- [x] Preserve the fixed final generation budget of at most 10 unique chunks and reject plans that cannot satisfy minimum coverage.
- [ ] Stream real non-empty provider fragments and retain the existing non-streaming generation option.
- [x] Resolve generated citation IDs only against final evidence; never accept invented or out-of-context IDs.
- [x] Record retrieval and generation failures separately.

The remaining streaming item is blocked by the configured gateway: with
`stream=True` it returns HTTP 201 `application/json` and zero SSE chunks. The
adapter rejects that response and does not simulate streaming.

## Current frontend and API work

### Documentation and contracts

- [x] Define the local and future deployment boundary in `DEPLOYMENT.md`.
- [x] Define the complete frontend behaviour and visual system in `frontend_plan.md` before implementation.
- [x] Merge AVA/web rules into root `AGENTS.md` without removing SEC and raw-data safety rules.
- [x] Document company detection and Comparison Cue handling as backend-only request lifecycle stages.

### Backend vertical slice

- [x] `GET /api/health` distinguishes process liveness, active mode, and pipeline readiness.
- [x] `POST /api/chat/stream` validates a current-query-only request and invokes the shared scope-aware entry point.
- [x] A successful stream orders events as non-empty `delta` events, one `sources`, then one `done`.
- [x] Pre-stream HTTP errors and in-stream safe `error` events expose no provider or stack details.
- [x] Source normalization maps complete narrative text and table-schema-v2 headers/rows without Markdown reconstruction.
- [x] Deterministic mock mode covers normal streaming, pre-token failure, and mid-stream failure and is visibly identified by health state.
- [x] Backend tests pass for health, event order, failure paths, source normalization, scope regression, deduplication, context budget, company balance, and citation resolution.

### Frontend vertical slice

The frontend milestone is complete only when every measurable criterion passes:

- [x] A query reaches the actual backend LLM planner, scope-aware per-subquery retrieval, context selection, and generation pipeline in real mode.
- [ ] The first provider token/fragment appears without waiting for the complete answer.
- [x] The waiting bubble appears immediately and disappears on the first non-empty token.
- [x] AVA and its supplied avatar appear beside every assistant response, and never beside user messages.
- [x] The full name `Autonomous Vehicle Analyst` is visible in the interface.
- [x] No internal repository branding appears in the interface.
- [x] Cited or final-context evidence can be opened for the associated answer.
- [x] Complete narrative chunks remain readable without destructive truncation.
- [x] Structured table chunks render through semantic HTML `<table>`, `<thead>`, and `<tbody>`, with narrow-screen horizontal scrolling.
- [x] Light mode uses a white page and dark mode uses the approved very-dark-blue page; OS initialization and `localStorage` persistence work without a theme flash.
- [x] The favicon uses `src/frontend/avatar/favicon.png` and the canonical avatar remains unchanged.
- [x] Keyboard submission, Shift+Enter, focus states, live status, reduced motion, and source controls pass accessibility checks.
- [x] Desktop and mobile layouts pass visual inspection in both themes.
- [x] Near-bottom auto-scroll works without pulling a user who has scrolled upward.
- [x] Frontend type checking, lint, component tests, and production build pass.
- [ ] Backend streaming and integration tests pass against the real configured provider; deterministic SSE integration tests pass, but the provider does not return SSE.
- [x] A production-bundle scan finds no API key, gateway credential, or backend prompt.

## Immediate next work

The next single step is to enable native chat-completions SSE on the configured
gateway (or provide a streaming-capable backend endpoint), then rerun the real
browser request and record time to first token. After that local gate passes,
the next milestone is a containerized reproducible backend build with measured
startup time, idle/peak RAM, model-cache size, artifact size, planner latency,
retrieval/context-selection latency, complete latency, and concurrency behaviour.

Additional evaluation work remains measurable and separate:

- [ ] Add generation test cases for supported, partially supported, ambiguous, numerical, table, comparison, and absent-evidence questions.
- [ ] Score retrieval coverage before generation quality for every failure.
- [ ] Compare reranked and preserved hybrid-RRF baselines by Recall@k, MRR, evidence type, and multi-company coverage.
- [ ] Keep reranking only if it improves final-evidence quality without unacceptable latency or category regressions.

## Deferred capabilities

These are explicitly outside the local vertical slice:

- [ ] persistent chat history and conversation memory;
- [ ] authentication, accounts, and profiles;
- [ ] document uploads and corpus-management UI;
- [ ] a history sidebar;
- [ ] a new vector database or migration from the current artifacts;
- [ ] admin and analytics dashboards;
- [ ] provider-specific hosting infrastructure;
- [ ] web search, tools, agentic RAG, or tool orchestration;
- [ ] Graph RAG or unrelated retrieval/generation redesign.

## Failure diagnosis rule

```text
Is the evidence in the raw filing?
├─ no  → corpus scope or correct abstention
└─ yes
   Is it in processed blocks?
   ├─ no  → cleaning, section, or table extraction issue
   └─ yes
      Is it in current chunks and aligned embeddings?
      ├─ no  → chunking or artifact-version issue
      └─ yes
         Is it retrieved in the scoped candidate set?
         ├─ no  → scope, embedding, BM25, or RRF issue
         └─ yes
            Is it retained after merge/dedup and subquery coverage selection?
            ├─ no  → allocation or context-selection issue
            └─ yes
               Is it in the final 10-chunk context?
               ├─ no  → final evidence selection issue
               └─ yes → generation, grounding, or citation issue
```
> **Archived 4 September 2026.** This previously ignored local document is\n> preserved as a read-only historical record, not a current plan or authority.\n> See [\`FINALIZATION.md\`](../../../../FINALIZATION.md) for the sole remaining-work plan.\n>\n# AVA — Project Roadmap
