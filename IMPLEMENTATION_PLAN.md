# AVA — Canonical Completion Plan

**Status date:** 1 September 2026
**Authority:** This is the single source of truth for AVA's next implementation
phases, target architecture, priorities, contracts, release gates, and open
product decisions. `README.md` describes the repository as it exists today;
`ROADMAP.md`, `DEPLOYMENT.md`, and `frontend_plan.md` are supporting documents and
must link here instead of defining a competing future plan.

## 1. How to use this file

An implementation agent must:

1. Read `AGENTS.md`, then this file, before changing the retrieval, generation,
   storage, image, API, or conversation paths.
2. Work in the phase order below. Do not combine storage migration with ranking
   changes, because that makes regressions impossible to attribute.
3. Keep evidence limits in the shared typed policy. The owner-fixed values are
   10 final chunks per company and 50 per request.
4. Update this file in the same change whenever a contract or accepted decision
   changes. Do not copy its configurable values into another planning document.
5. Preserve the fixed eleven-company corpus and immutable files under
   `data/raw/` unless the owner explicitly changes those constraints.
6. Preserve the current NPZ/BM25 implementation as a parity oracle until the
   Qdrant cutover is accepted and rollback has been tested.

This document authorizes planning, not automatic implementation of every phase.
Each phase should be implemented and evaluated as a bounded change.

## 2. Audited baseline on `main`

The following is verified from the checked-in code, not inferred from notebooks:

- `src/filings/corpus.py` defines the active eleven filings and exact aliases.
- `src/retrieval/scope_aware.py::detect_companies` uses case-insensitive regex
  aliases plus explicit ticker tokens. It cannot recover a typo such as `frod`.
- Scope classification is deterministic and query-only. An explicit subset is
  retrieved with one combined ticker filter, so one company can dominate the
  result list.
- `GenerationService.plan_retrieval` asks the LLM only for atomic subqueries and
  an operation. It does not return validated company mentions or ambiguity.
- `retrieve_generation_context` retrieves 10 results per planner subquery,
  reserves two slots per subquery, applies a `0.01` repeated-subquery bonus, and
  hard-caps final generation context at 10 unique chunks.
- The backend loads all 4,526 BGE vectors from NPZ, all chunk JSONL into memory,
  and creates one in-memory `bm25s` index during process startup.
- Generation cites internal chunk IDs. Exact cited IDs are accepted only if the
  chunk was in final generation evidence.
- **Known source bug:** `resolve_cited_evidence` returns every final-context chunk
  when no citation resolves. `RealPipeline` normalizes that fallback and the UI
  therefore displays chunks the answer did not identify as used.
- The API accepts only `{ "query": string }`. Browser transcript state is memory
  only and no prior turn is sent to the backend.
- Source types are narrative text and structured tables. There is no image source
  contract or asset endpoint.
- The frozen filing HTML contains 35 `img` elements across 10 filings (19 in
  Mobileye); `drop_non_text_nodes` currently removes `img`, `svg`, and `picture`
  before extraction. Existing alt text is usually filename-like or generic.
- There is no Qdrant client, relational history store, cache, authentication,
  request ID, structured stage timing, or generation-quality evaluation suite.
- `.gitignore` excludes `docs/`; that directory contains local stale copies that
  are not versioned on `main`. This canonical file intentionally lives at the
  repository root so implementation agents and Git both see the same plan.
- Provider-streaming code exists, but the currently documented gateway returns a
  buffered JSON completion. Mock SSE tests do not prove production time-to-first
  token.
- `SYSTEM_PROMPT` and `PLANNER_INSTRUCTION` both incorrectly expand `CEO` as
  “Chief Operating Officer.” CEO means Chief Executive Officer; COO means Chief
  Operating Officer. This can corrupt retrieval and generation and is a P0 fix.
- `AvaAvatar.tsx` uses the owner-approved supplied `ava-light.png` and
  `ava-dark.png` variants according to the active UI theme; `index.html` uses
  the canonical `favicon.png`. `AvaAvatar.tsx` has one props declaration.

The current deletion of `CHUNKING_REPORT.md` in the working tree predates this
plan and is unrelated. Do not restore or modify it while implementing this plan
without asking the owner.

## 3. Product outcome and non-negotiable invariants

AVA is complete when it can answer supported SEC-filing questions with balanced,
traceable evidence; show only evidence actually used; retrieve and display a
filing image only when it materially adds information; continue a conversation
with bounded, user-controlled memory; run against a reproducible Qdrant index;
route greetings and out-of-corpus requests without irrelevant filing retrieval;
use bounded, auditable tools when required; work with conversation-scoped user
documents; and meet measured reliability, security, accessibility, and
deployment gates.

The following invariants apply to every phase:

- The corpus remains Aptiv, Aurora, Ford, General Motors, Alphabet, Mobileye,
  NVIDIA, Ouster, Qualcomm, Rivian, and Tesla.
- Company resolution, query planning, scope, retrieval, evidence allocation,
  memory selection, image selection, citation validation, and source filtering
  are backend responsibilities. The frontend sends user text unchanged.
- The user's original query and transcript text are never silently rewritten.
  Canonical company names may be added only to an internal retrieval query.
- A resolved company must be one of the eleven configured tickers. The LLM
  planner owns final in-corpus scope; exact/fuzzy resolution supplies advisory
  hints and never authorizes an out-of-corpus ticker.
- Every planner-targeted company receives an even share under hard limits of 10
  final text/table chunks per company and 50 per request. One through five
  companies target 10 each; larger scopes divide 50 slots as evenly as possible.
  If candidates or complete-chunk token limits make that impossible, preserve a
  fair partial result and record the unmet target instead of discarding evidence
  for every company.
- Candidate evidence, final generation evidence, cited/used evidence, and
  user-visible sources are distinct sets and must be logged separately.
- Only exact, validated cited/used evidence is shown. No-citation means an empty
  source list, not a fallback to all retrieved or all final-context chunks.
- Images are shown only when they are supplied to generation, add material
  information beyond selected text/table evidence, and are cited/marked as used.
- Tables and images retain source provenance. Never reconstruct or fabricate
  headers, cells, captions, units, labels, or image meaning in the frontend.
- Retrieval behavior is evaluated separately from generation and citation
  behavior. Every failed answer is diagnosed at the earliest failing stage.
- All new limits are both count-aware and token-aware. A chunk-count quota must
  not silently overflow the model context; partial packing proceeds round-robin
  so a token limit does not preferentially erase later companies.
- Raw SEC HTML stays unchanged. New image bytes and derivative metadata live in
  a separate versioned asset tree.
- Long-term memory requires identity/tenant isolation, deletion, and retention
  controls. It must never mix one user's content into another user's prompt.
- Route decisions are structured and observable, but AVA never exposes private
  chain-of-thought. A greeting, product-help question, or unrelated request must
  not be forced through filing retrieval merely because an index is available.
- Parametric model knowledge is not filing evidence. Filing claims require
  validated SEC evidence; uploaded-document claims require conversation-owned
  document evidence; web claims require returned web sources; arithmetic derived
  from evidence requires the deterministic calculator and citations for inputs.
- Uploaded and web content is untrusted data, never instructions. It cannot alter
  system policy, authorize another tool, choose a tenant/conversation, or cause
  secret or unrelated-data disclosure.
- Tool execution is bounded, allow-listed, timeout-limited, and recorded. No
  autonomous actions, arbitrary code execution, recursive agent loop, or hidden
  expansion of the user's request is authorized.

## 4. Target architecture

```text
Browser (React)
  ├─ current conversation UI
  ├─ cited SEC/web/upload source cards
  ├─ conversation-scoped upload control and Sources view
  └─ left history/memory sidebar and chat actions
          │
          ▼
FastAPI adapter
  ├─ request/auth/conversation validation
  ├─ SSE transport and cancellation
  └─ frontend-safe response mapping
          │
          ▼
Conversation orchestrator
  ├─ bounded intent/evidence/tool router
  ├─ exact + fuzzy + LLM-assisted company resolver
  ├─ atomic filing/document retrieval planner
  ├─ short-term turn window + conversation summary
  ├─ optional long-term memory retrieval
  ├─ deterministic calculator and configured web-search adapter
  └─ grounded generation + citation/used-source audit
          │
          ▼
Evidence service
  ├─ independent per-company/per-subquery candidate retrieval
  ├─ dense + lexical fusion
  ├─ per-company reranking/diversity
  ├─ balanced company-target allocation
  ├─ 10-per-company / 50-per-request enforcement
  ├─ token-aware context packing
  └─ nearby-image relevance and novelty gate
          │
          ├──────────────────────┐
          ▼                      ▼
Qdrant                         Private asset store
  filing_chunks                  immutable SEC image bytes
  conversation_memory            immutable uploaded bytes
  conversation_documents         extraction metadata and hashes
  tenant/chat payload filters    stable server-owned source IDs
  versioned filing alias
          │
          ▼
PostgreSQL
  users/tenants, conversations, messages, summaries,
  source-use records, feedback, retention/deletion state
```

Qdrant stores filing retrieval points and, later, a separate tenant-filtered
memory collection. PostgreSQL remains the source of truth for conversations and
message order; Qdrant is not the transcript database. Image and uploaded-document
bytes belong in a private object/file asset store, not in Qdrant payloads. Web
results are request evidence and are not added to long-term memory by default.

## 5. Priority and dependency order

| Priority | Phase | Why it is ordered here |
|---|---|---|
| P0 | 0. Freeze baselines and add evaluation cases | Every later change needs comparable evidence. |
| P0 | 1. Fix used-source and known baseline correctness defects | Current UI can misrepresent unused chunks as sources; prompt and canonical asset defects are already verified. |
| P0 | 2. Robust company resolution | Balanced retrieval cannot work if targets are missed. |
| P0 | 3. Company-balanced dynamic evidence | Directly fixes the 10-chunk multi-company failure. |
| P0 | 4. Observability and generation/citation evaluation | Required to prove phases 1–3 and diagnose failures. |
| P1 | 5. Qdrant parity migration and cutover | Adds durable/filterable retrieval without hiding ranking changes. |
| P1 | 6. Filing image ingestion, retrieval, and UI | Requested product capability; depends on stable evidence IDs. |
| P1 | 7. Short- and long-term conversation history | Requested capability; requires a deliberate data/security model. |
| P1 | 8. Production hardening and deployment | Turns the vertical slice into an operable product. |
| P1 | 9. Bounded routing, tools, uploads, and conversation workspace | Prevents irrelevant RAG and adds the owner-approved evidence/tool surfaces. |
| P2 | 10. Measured retrieval/generation improvements | Add only after baseline gates show a specific weakness. |
| P3 | 11. Only-if-time enhancements | Useful polish with no dependency from core correctness. |

P0 is the next implementation milestone. Qdrant, images, and memory must not be
started by weakening or skipping the P0 release gates.

## 6. Phase 0 — Freeze baselines and expand the test set (P0)

### Work

- Record a baseline run from the existing NPZ/BM25 path before changing it.
- Add typo and ambiguity queries for every company/alias, including `frod`,
  transpositions, punctuation, casing, ticker/name collisions, multiple typos,
  global questions, and text that should resolve to no company.
- Add two-, three-, six-, and ten-company comparison cases whose gold evidence
  requires at least five relevant chunks per company.
- Add cases where the required evidence is ranked 11–30 globally but top 10
  within the correct company.
- Add citation cases: two valid citations among 15–22 final chunks, grouped
  citations, invented IDs, malformed IDs, no citations, and supported abstention.
- Create an image evaluation manifest from the existing 35 image nodes. Label
  decorative/logo/page artifacts separately from charts, diagrams, and other
  information-bearing figures. Do not download or modify raw HTML in this phase.
- Add multi-turn tests for pronouns, follow-ups, topic switches, old-turn recall,
  deletion, and cross-user isolation before implementing history.

### Outputs

- Versioned query/evidence labels under `data/evaluation/`.
- Machine-readable baseline summaries for detection, retrieval, final evidence,
  citations, and latency.
- A frozen representative parity fixture containing resolved companies, scope,
  subqueries, per-company candidates, selected IDs, and order.

### Gate

No retrieval policy change merges until its before/after result can be produced
with one command and failures identify detection, candidate retrieval, final
selection, generation, or citation as separate stages.

## 7. Phase 1 — Used-source and baseline correctness fixes (P0)

This is the first code fix.

### Required behavior

- Parse exact citation IDs from the completed answer and intersect them with
  final generation evidence.
- Preserve citation order and deduplicate IDs.
- Return only those resolved chunks in the `sources` SSE event.
- If no valid ID resolves, return `sources: []`, set a diagnostic reason such as
  `no_resolved_citations`, and let the UI render its existing no-reference state.
- Never show all final evidence as a citation fallback.
- Invented or candidate-only IDs remain rejected.

### Contract change

Replace the ambiguous `citation_fallback` behavior with backend-only diagnostics
and a clear public contract:

```json
{
  "sources": [],
  "source_status": "none_cited",
  "malformed_source_count": 0
}
```

Allowed `source_status` values should be `cited`, `none_cited`, and
`cited_with_unrenderable_items`. Do not expose candidate counts or scores.

### Reliability follow-up

Strengthen the generation prompt and tests so every factual claim uses an exact
source ID. If provider capabilities allow structured output without breaking
real streaming, add a terminal machine-readable `used_source_ids` field. Until
then, exact inline citations are authoritative. A second LLM verifier may audit
missing citations later, but it must never infer and display a source the answer
did not cite.

### Files expected to change

- `src/generation/rag.py`
- `src/backend/pipeline.py`
- `src/backend/sources.py`
- `src/frontend/src/api/chatStream.ts`
- `src/frontend/src/types.ts`
- `src/frontend/src/components/Sources.tsx`
- citation, backend, stream, and component tests

### Acceptance tests

- Answer cites chunks 555 and 456 from Tesla: exactly those two source cards are
  returned and displayed in citation order.
- Answer cites none: zero source cards; no candidate/final-context fallback.
- Answer cites one valid and one invented ID: only the valid source is shown.
- A malformed cited table increments the unrenderable count but does not cause
  unrelated evidence to appear.

### Other verified P0 repairs in this milestone

- Correct both prompt examples so CEO expands to Chief Executive Officer and COO
  expands to Chief Operating Officer. Add regression queries for both roles and
  remove any instruction that silently changes an acronym to the wrong office.
- Use the owner-approved supplied `ava-light.png` in light mode and
  `ava-dark.png` in dark mode at every AVA placement. Point the favicon to
  `src/frontend/avatar/favicon.png`, and keep one props declaration.
- Add a component assertion that each active theme selects its corresponding
  supplied avatar asset.

## 8. Phase 2 — Robust company and ticker resolution (P0)

The single existing LLM planner owns company scope, query decomposition,
retrieval-query reformatting, semantic comparison intent, and operation
classification. Exact/fuzzy resolution remains an advisory hint generator, not
a competing scope authority. Schema validation still rejects any ticker outside
the fixed corpus.

### Resolution pipeline

1. Normalize Unicode, case, whitespace, punctuation, apostrophes, and common
   corporate suffixes for matching only.
   Treat grammatical plurals of known domain/executive acronyms (for example,
   `CEOs` and `CFOs`) as ordinary terminology, never company-like mentions.
2. Run exact alias/name/ticker matching. Preserve the special handling for the
   single-letter Ford ticker `F`.
3. Run deterministic typo matching against the configured alias lexicon using a
   normalized Damerau-Levenshtein/token similarity score. Apply stricter
   thresholds to short strings and never auto-resolve a low-margin tie.
4. Supply exact/fuzzy results and unresolved candidates to the existing LLM
   planner as advisory hints. The planner selects final scope against the enum of
   eleven tickers plus `none`/`ambiguous`; do not add a second LLM call.
5. Validate every planner and subquery ticker against `ACTIVE_FILINGS`, but do
   not require equality with the advisory deterministic result.
6. For a low-confidence or ambiguous target, ask a concise clarification instead
   of silently searching the wrong filing. Global queries must remain global and
   must not be forced to a company.

### Shared result type

```text
CompanyResolution
  original_query
  mentions[]
    raw_text
    ticker
    canonical_name
    method: exact_alias | exact_ticker | fuzzy | llm
    confidence
  unresolved_mentions[]
  explicit_scope_tickers[]  # deterministic "each/all company" corpus quantifier
  planner_scope_tickers[]   # authoritative validated in-corpus planner scope
  scope
  comparison
  needs_clarification
```

An unqualified `each company`, `every company`, or `all companies` phrase is
supplied to the planner as a complete-corpus hint. The planner contract strongly
instructs all eleven targets, but runtime accepts any structurally valid
in-corpus scope it returns rather than failing on hint disagreement.

`detect_scope` must consume this result. There must be one resolver shared by the
API, evaluator, and notebooks/scripts; do not leave a duplicate detector in
`src/scripts/evaluate_scope_aware_hybrid_retrieval.py`.

### Planner contract

The validated plan should include `resolved_tickers`, atomic subqueries whose
company targets are explicit, comparison intent, operation, and ambiguity state.
The original query remains the generation question. Internal retrieval queries
may append the canonical name/ticker only when the planner subquery does not
already contain an exact alias/ticker, so `frod` retrieves Ford evidence without
degrading already-canonical company queries.

`comparison` means semantic comparison, not merely that two or more companies
were requested. Independent facts for multiple companies use `comparison=false`
while retaining every company target and the same balanced evidence allocation
policy. Planner ticker output is constrained by the resolver's exact/fuzzy
matches and unresolved shortlists are advisory. Deterministic code validates the
fixed ticker allowlist but does not override planner-owned scope or intent.

For `who` questions about an executive acronym, planner subqueries use the
company name plus the exact expanded role and `name` (for example,
`Ford Chief Executive Officer name`). This is a meaning-preserving retrieval
reformulation, not permission to add a person or fact absent from the query.

### Acceptance gates

- At least 99% accuracy on the fixed typo/alias set and 100% on exact aliases and
  explicit multi-company lists.
- Zero out-of-corpus company IDs and zero silent resolutions of labeled ambiguous
  cases.
- Exact-match path adds no LLM call beyond the existing planner.
- Resolver result is identical in API and evaluation entry points.
- Detection method, confidence band, and ambiguity are logged without exposing
  hidden prompts to the browser.

## 9. Phase 3 — Company-balanced, token-aware evidence selection (P0)

### Core change

For an explicit set of companies, do not run one combined filtered search. For
each atomic subquery and each target company, run an independently ticker-filtered
dense + lexical retrieval and retain 10 unique candidates per company. Merge by
stable chunk ID while retaining per-company, per-subquery, dense, lexical, and
fusion provenance.

This means a Tesla/Ford/Mobileye question starts with up to 30 company-balanced
candidates per atomic fact, rather than asking one 10-result list to represent
all three companies.

### Selection stages

```text
10 candidates per (company × relevant subquery)
  → stable-ID merge inside each company
  → optional measured rerank inside each company
  → redundancy/section/content-type diversification
  → compute an even company target under the 50-chunk request cap
  → reserve up to that target, never exceeding 10 for any company
  → token-aware pack without breaking balanced company allocation
  → final generation evidence
```

Reranking must happen before final quota allocation or within each company pool;
a single global rerank must not erase company balance. Start with deterministic
RRF plus diversity. Enable the existing cross-encoder only behind an experiment
flag after it improves the new multi-company evaluation set.

### Evidence-budget configuration

The owner's fixed requirements are `candidate_k_per_company = 10`,
`per_company_final_limit = 10`, and `corpus_final_limit = 50`.

| Explicit companies | Target allocation | Total target |
|---:|---:|---:|
| 1 | 10 each | 10 |
| 2 | 10 each | 20 |
| 3 | 10 each | 30 |
| 4 | 10 each | 40 |
| 5 | 10 each | 50 |
| 6–10 | even division of 50, remainder assigned deterministically | 50 |

Store this in one typed backend policy, not scattered constants or frontend
logic. Record the policy name/version in evaluation output and request logs.

### Token budget and large tables

- Compute actual generation tokens after context formatting, not just chunk
  counts. Reserve output tokens and prompt/history overhead first.
- Never cut a table cell or silently truncate a source. If a complete table makes
  the full policy impossible, retain the fair partial set of complete sources
  and record the unmet company/subquery quota in backend diagnostics.
- Prefer non-redundant sections and distinct evidence needed by the subqueries.
  Near-duplicate overlapping narrative chunks should not consume five company
  slots unless the gold evidence actually spans them.
- Adjacency expansion may nominate a neighboring chunk, but that neighbor becomes
  normal final evidence with its own source ID. No hidden uncitable context.
- Keep candidate acquisition, final chunk count, final token count, and reason for
  every selected/rejected item inspectable in backend diagnostics.

### Global and enumeration queries

Balanced company targets apply to explicitly requested companies, not every
global question. Enumeration/global questions retain their smaller target while
still enforcing the hard 10-per-company and 50-per-request caps. An all-company
planner scope uses the explicit company budget; if the full target cannot fit,
it remains an explicitly scoped partial result and must not silently become a
global top-10 query.

### Acceptance gates

- Two-company comparisons target 10 available relevant chunks from each company.
- Three-company comparisons target 10 available relevant chunks from each company.
- Five-company requests target 10 each; larger requests divide 50 slots evenly,
  including five each for ten companies.
- Partial allocation is round-robin, so a weak or later company is not erased by
  a strong company's scores before generation.
- Deduplication, per-subquery coverage, table integrity, and stable source IDs are
  preserved.
- Recall@k, MRR, per-company evidence coverage, quota satisfaction, context-token
  usage, unsupported-claim rate, and latency are reported separately.
- No accepted evaluation category regresses beyond a threshold recorded before
  implementation; any exception requires an explicit owner decision.

## 10. Phase 4 — Observability and evaluation completion (P0)

Implement alongside phases 1–3 and make it a release gate.

### Per-request structured record

- request/conversation/turn ID and corpus/index version;
- original query and retrieval subqueries;
- resolver mentions, methods, confidence, scope, and comparison decision;
- candidates per company/subquery and dense/lexical/fusion ranks;
- reranker identity/version/scores when enabled;
- quota allocation, selected IDs, selection reasons, and token count;
- image candidates, relevance/novelty decision, and selected asset IDs;
- short-/long-term memory IDs selected, never hidden full prompts in normal logs;
- final generation evidence IDs;
- generated citation IDs, resolved used IDs, rejected IDs, and source status;
- stage latency, time to first token, complete latency, cancellation, provider
  usage, and safe error class.

Logs must be access-controlled and redact secrets. Define retention separately
from user conversation retention.

### Metrics

- **Resolver:** exact/fuzzy/LLM accuracy, ambiguity precision, false company rate.
- **Retrieval:** Recall@k, MRR, candidate recall, per-company recall, table/image
  recall, and multi-company quota satisfaction.
- **Selection:** gold-evidence survival, redundancy, company balance, context
  tokens, and selected chunk count.
- **Generation:** claim support, completeness, numerical correctness, abstention,
  comparison coverage, and contradiction rate.
- **Citation:** citation precision/recall, invalid IDs, uncited factual claims,
  and source-display exactness.
- **Operations:** startup/readiness, p50/p95 latency by stage, time to first token,
  memory/CPU, Qdrant latency, error/cancel rate, and concurrency.

### Implemented Phase 4 contract — 2026-08-27

- Runtime emits one schema-v1 backend-only completion record per real request and
  exposes only an opaque `X-Request-ID` to the browser. The record reserves empty
  image and memory fields without claiming those later features exist.
- Query/answer content in operational logs is access-controlled and has a
  separately configurable external-sink retention period; it is not conversation
  persistence. Safe error classes never contain raw provider messages.
- `src.observability.summarize_request_records` provides backend-neutral p50/p95
  stage, first-token, and complete latency plus usage, error, cancellation, and
  observed-concurrency metrics. Qdrant latency was explicitly unavailable at
  Phase 4 completion; Phase 5 shadow reads now populate it.
- `src.evaluation.generation_quality` evaluates seven reviewed categories from
  fixed final evidence, separately from retrieval. Reference mode tests the
  deterministic metric/citation contract; provider mode records fresh answers,
  exact citations, model identity, corpus fingerprint, usage when available, and
  an optional non-authoritative grounding audit.

## 11. Phase 5 — Qdrant migration with parity first (P1)

### Architecture decision

Use Qdrant as the persistent filing retrieval index. Each chunk is one point with
a stable deterministic point ID, a named 768-dimensional BGE dense vector, and
payload containing the complete retrieval/source metadata needed for filters and
hydration. Keep complete chunk JSONL and embedding manifests as reproducible build
artifacts; Qdrant is a deployed index, not the only copy of source evidence.

Qdrant supports named dense/sparse vectors, payload filtering, hybrid multi-stage
queries, and atomic collection-alias switches. Use those capabilities only after
measuring parity with the current `bm25s` + custom RRF behavior. Official design
references:

- https://qdrant.tech/documentation/search/hybrid-queries/
- https://qdrant.tech/documentation/search/filtering/
- https://qdrant.tech/documentation/manage-data/collections/#collection-aliases
- https://qdrant.tech/documentation/operations/snapshots/

### Collection and payload contract

Use versioned physical collections and a stable read alias, for example:

```text
ava_filing_chunks_<artifact_version>  ← physical collection
ava_filing_chunks_current             ← runtime alias
```

Required payload includes `chunk_id`, ticker/company/CIK, filing and artifact
versions, accession number, section/path, content type, block/source anchors,
source URL, text or a stable text-store reference, table logical fields, token
count, content hash, and associated image asset IDs. Create payload indexes for
every field used in filters, especially ticker, filing version, content type, and
artifact version.

### Migration sequence

1. Pin compatible Qdrant server/client versions and add local Docker configuration,
   health/readiness, secrets, timeouts, and TLS expectations.
2. Add an index builder that validates manifests, computes deterministic point
   IDs, batches idempotent upserts, and emits an import manifest with counts and
   hashes.
3. Implement a retrieval interface with both `LocalArtifactRetriever` and
   `QdrantRetriever`; do not let FastAPI contain database query logic.
4. First store/use dense vectors in Qdrant while retaining current `bm25s` and
   custom RRF. Compare exact candidate IDs/order and accepted metric tolerances.
5. Shadow-read Qdrant in real requests without affecting answers and log parity.
6. Only then experiment with a named sparse vector and Qdrant-native hybrid RRF.
   The current custom RRF constant/ranking is the baseline; Qdrant defaults are
   not assumed equivalent.
7. Build a new physical collection, run strict count/hash/search audits, atomically
   switch the alias, and retain the previous collection for rollback.
8. Test snapshot/restore and alias rollback before deleting any old collection.
9. Remove NPZ/BM25 from runtime only after a defined soak period. Keep artifact
   generation and an offline parity path for reproducibility.

### Gates

- 4,526 expected chunk IDs and vectors at the current corpus version; no missing,
  duplicate, payload, dimension, normalization, hash, or table-provenance errors.
- Metadata filters cannot leak an unrequested ticker.
- Baseline evaluation is within the predeclared parity tolerance before any
  native sparse/ranking change.
- Startup no longer rebuilds the whole corpus index in every API worker.
- Unavailable Qdrant makes readiness false; real mode must not fall back silently
  to mock answers.
- Backup, restore, alias cutover, rollback, and partial-import recovery are tested.

### Implemented Phase 5 foundation — 2026-08-28

- Pinned Qdrant server `v1.18.2` and Python client `1.19.0`; added loopback-only
  Docker configuration plus a standalone-binary wrapper with ignored persistent
  storage and snapshot paths.
- Added a content-addressed collection builder for the current 4,526-point,
  eleven-company corpus. It validates manifest-v3 source/embedding hashes,
  ordered embedding-input hashes, dimensions, dtype, normalization, and chunk
  alignment before idempotent batched upserts.
- Each point uses a deterministic UUID and the named
  `dense_bge_base_v1_5` 768-dimensional dot-product vector. Payload contains the
  complete chunk and its filing/source/table metadata, artifact/model identity,
  content and embedding-input hashes, and image-asset placeholder IDs. Required
  filter fields are indexed.
- Strict post-import audit verifies all point counts, canonical chunk IDs,
  deterministic point IDs, complete payload hashes, every vector, and a
  ticker-filtered exact-search smoke query before alias activation. The emitted
  import manifest records source paths, hashes, counts, alias, previous target,
  and snapshot name.
- Added interchangeable local and Qdrant dense retrievers behind the shared
  scope-aware module. BM25 and custom RRF are unchanged. Backend modes are
  `disabled`, `shadow`, and `primary`; configured Qdrant failure makes real-mode
  readiness false without mock fallback.
- Shadow requests retain local dense answers and record Qdrant latency, complete
  candidate IDs/order, exact top-10 order, and overlap. The declared dense gate
  is exact top-10 order plus at least 98% overlap at candidate depth 50; final
  hybrid selected-evidence IDs/order remain an exact gate.
- The live migration imported and strictly audited all 4,526 points into
  `ava_filing_chunks_89d3a5be9e7d7a8e`, activated
  `ava_filing_chunks_current`, created a server snapshot, restored it into a
  separate collection, audited through the restored alias, and atomically
  rolled the alias back. The original and restore-tested collections remain
  available; nothing was deleted.
- The saved parity report accepted all 11 dense cases (nine full exact-order,
  every case exact through top 10, minimum overlap 98%) and all three shared
  final-selection cases with exact chunk IDs/order. Local `.env` is configured
  for shadow soak; promotion to `primary` remains intentionally gated on the
  soak rather than being silently enabled.

## 12. Phase 6 — Filing image retrieval and display (P1)

Image support is an ingestion, retrieval, generation, provenance, and UI feature.
It is not a rule that blindly displays every image near a selected chunk.

### Asset ingestion

- Scan the original HTML before `drop_non_text_nodes` removes visual nodes.
- Resolve relative SEC asset URLs against the filing URL with SEC-compliant
  acquisition behavior.
- Store original bytes unchanged under a new versioned tree such as
  `data/assets/TICKER/FILING/original/`; never add them to or rewrite `data/raw/`.
- Record stable `asset_id`, filing/accession, original URL/path, DOM XPath/order,
  nearest preceding/following block IDs and anchors, section, caption/nearby text,
  alt/title, MIME type, dimensions, byte size, SHA-256, acquisition time, and
  status. Reject unsafe MIME types and path traversal.
- Produce derivatives (thumbnail, OCR, vision description) in separate directories
  with their own hashes/model versions. Never overwrite the original.
- Classify logos, decorative rules, signatures, page furniture, and duplicate
  images so they are excluded by default.

### Retrieval and novelty gate

1. Text/table retrieval runs first.
2. Collect image candidates associated with selected chunks/blocks and image
   candidates retrieved by caption/OCR text when the planner marks the question
   as visually answerable.
3. Score relevance to the query and whether the image contains material evidence
   not already present in selected text/table chunks.
4. Supply only relevant, non-duplicate, information-bearing images to a
   vision-capable generation path. If no configured model can inspect them, do
   not claim they add information and do not display them as used evidence.
5. Require an exact figure citation/used asset ID. A nearby but uncited image is
   not sent to the frontend.

“Close to a used chunk” is a candidate-generation signal, never sufficient by
itself. The novelty decision and reason must be logged and evaluated.

### API/UI contract

Extend the `sources` payload with a discriminated image source, or add an `images`
field in that same terminal event. Do not add fake answer tokens or base64 image
bytes to SSE deltas.

```json
{
  "content_type": "image",
  "asset_id": "MBLY-2025-FIGURE-0007",
  "company": "Mobileye Global Inc.",
  "ticker": "MBLY",
  "filing_year": 2025,
  "section": "Item 1 — Business",
  "caption": "Trustworthy filing caption when available",
  "alt_text": "Accessible description verified from the asset",
  "image_url": "/api/assets/MBLY-2025-FIGURE-0007",
  "source_url": "https://www.sec.gov/Archives/..."
}
```

Serve assets through a stable ID endpoint that maps IDs to approved files; never
accept a filesystem path from the browser. Send correct MIME, cache, CSP, and
content-disposition headers. The React source card uses semantic `figure`, `img`,
and `figcaption`, lazy loading, explicit dimensions, zoom/open-original, useful
alt text, and responsive sizing. The user must be able to distinguish filing
evidence from AVA's avatar.

### Gates

- Every displayed image hash matches its asset manifest and filing provenance.
- Decorative/duplicate image precision and information-bearing image recall meet
  thresholds set from the labeled manifest.
- A selected neighboring chunk with an irrelevant image produces no image card.
- A relevant figure that materially answers the question is supplied to the
  model, cited, displayed once, and accessible by keyboard/screen reader.
- Missing/corrupt assets fail independently without causing unrelated sources to
  appear.

## 13. Phase 7 — Short- and long-term history and memory (P1)

Conversation history changes the current stateless contract. Implement transcript
persistence first, bounded short-term context second, and semantic long-term
memory last. Do not treat all stored messages as prompt context.

### Data ownership

- PostgreSQL is authoritative for users/tenants, conversations, ordered messages,
  assistant answers, source-use records, summaries, feedback, and deletion state.
- Qdrant may store embeddings for explicitly eligible long-term memory items in a
  collection separate from filing chunks. Every search must carry an indexed
  tenant/user filter and optionally a conversation filter.
- Authentication or a documented single-user deployment boundary is required
  before cross-session history. Browser-generated IDs alone are not sufficient
  isolation for a multi-user deployment.

### Short-term memory

- Add `conversation_id` and `client_turn_id` to requests; make retries idempotent.
- Load a token-bounded window of recent turns plus a versioned rolling summary.
- Resolve follow-ups and pronouns using recent context, then run the same company
  resolver. Never assume the previous company after an explicit topic switch.
- Keep filing evidence separate from conversational text in prompts and logs.
- Summaries must preserve named companies, periods, unresolved questions, user
  constraints, and cited evidence IDs, and must be replaceable/rebuildable from
  canonical messages.

### Long-term memory

- Define eligible memory narrowly: explicit user preferences, durable project
  context, and conversation summaries. Do not store every raw turn as semantic
  memory by default.
- Retrieve a small tenant-filtered memory candidate set, rerank it for the current
  query, and include only items above a threshold and within a separate token
  budget.
- Store provenance (`conversation_id`, message/summary IDs, created/updated time,
  memory type, model/version, tenant ID) and never cite memory as SEC evidence.
- Provide user controls to list conversations, rename, resume, delete one, delete
  all, and disable long-term memory. Deletion must remove relational rows, Qdrant
  memory points, cached summaries, and derived exports according to a tested job.

### API direction

```text
POST   /api/conversations
GET    /api/conversations
GET    /api/conversations/{id}/messages
DELETE /api/conversations/{id}
POST   /api/chat/stream
       { conversation_id, client_turn_id, query }
```

The exact route shape can change in its implementation phase, but server-side
ownership, idempotency, pagination, tenant checks, and deletion cannot be omitted.

### Gates

- Follow-up evaluation improves without regressing standalone queries.
- Prompt construction is deterministic and remains within its history/memory
  token budgets.
- Cross-user and cross-conversation isolation tests pass at API and Qdrant layers.
- Retry does not duplicate a user or assistant message.
- Deletion is complete and auditable; retention and backup behavior are documented.
- A user can start a new stateless conversation with no inherited memory.

### Implemented Phase 7 foundation — 2026-08-31

- Added PostgreSQL schema and repository boundaries for server-owned tenants,
  users, conversations, ordered messages, summaries, exact source-use records,
  feedback, and deletion audit records. The API supports create/list/resume,
  rename, per-conversation memory control, delete-one, delete-all, and paginated
  transcript reads.
- Chat requests accept server-owned `conversation_id` plus UUID
  `client_turn_id`. Beginning a turn is atomic; completed retries replay the
  stored answer and source event, in-progress duplicates return a conflict, and
  interrupted turns become retryable without duplicating the user message.
- Short-term context selects whole completed turns under a configurable token
  budget and rebuilds a versioned extractive summary from canonical older
  messages. Conversation text remains a distinct prompt section, and the exact
  formatted history is counted during the existing evidence-packing pass.
- The same planner receives bounded context for follow-up/pronoun resolution
  while the deterministic resolver and retrieval path still receive the current
  original query unchanged. Explicit topic switches remain planner-owned.
- Added a separate named Qdrant memory collection for eligible conversation
  summaries. Every query and deletion includes tenant and user filters;
  cross-conversation retrieval is thresholded and separately token bounded.
  Memory is off for every new conversation and disabling it removes that
  conversation's derived points.
- Cross-session history is available only in an explicitly acknowledged
  `single_user` deployment with server-configured tenant/user identity and
  PostgreSQL. `disabled` retains the stateless path. Multi-user deployment still
  requires a selected authentication provider and is not authorized by browser
  IDs.
- Added the minimal responsive history UI for resume, rename, delete, delete
  all, new-memory-off chat, and memory enable/disable controls. Transcript text
  is loaded from the API and is not copied into browser persistence.
- Unit/local-Qdrant coverage validates idempotency, prompt separation, bounded
  summaries, cross-user isolation, opt-in memory, filtered search, and complete
  derived-memory deletion. A live PostgreSQL contract test is available behind
  `AVA_TEST_POSTGRES_DSN`.
- Added an executable provider-backed gate for the frozen history manifest. It
  compares query-only and contextual planner scope through the same validated
  resolver as the API, while deletion and isolation cases execute separately
  against deterministic storage doubles. The accepted 31 August run improved
  history-dependent scope accuracy from 0% to 100%, retained 100% standalone
  accuracy with zero regressions, reached 100% contextual topic-switch accuracy,
  recorded no planner errors, and passed all four state cases.
- The live gate exposed a provider response that marked one valid subquery as a
  multi-retrieval plan. Runtime now normalizes only this redundant boolean from
  the already validated subquery count and records the normalization; company
  scope, query text, subqueries, and retrieval behavior remain unchanged.
- Added provider-neutral multi-user OIDC authentication through a backend-for-
  frontend authorization-code/PKCE flow. ID tokens are validated for fixed
  algorithm, issuer, audience, nonce, expiry, and tenant claim; the browser gets
  only an opaque HttpOnly session plus CSRF cookie, while hashed session state is
  stored in PostgreSQL and every conversation operation is owner-bound.
- Added dry-run-first retention with conditional expiry rechecks, derived-memory
  deletion, cascaded canonical deletion, content-free audit records, and expired
  auth-session cleanup. Added portable PostgreSQL/Qdrant backups with checksummed
  manifests plus guarded isolated restore drills and a documented daily/weekly/
  monthly retention policy.

Provider-backed follow-up and topic-switch evaluation is passing. Automated and
local-Qdrant isolation, idempotency, deletion, retention, authentication, and
backup safeguards pass. A production-like live PostgreSQL/Qdrant run and restore
drill remain Phase 8 release evidence because this workspace cannot access its
Docker engine. Phase 6 image work is explicitly skipped at the owner's direction;
no image implementation or acceptance claim is included in this memory milestone.

## 14. Phase 8 — Production hardening and finished-product gates (P1)

### Closure status — 1 September 2026

The owner-authorized non-image scope is implementation-complete. API and
frontend production images, private PostgreSQL/Qdrant topology, migrations,
health probes, Nginx SSE proxying, security controls, observability, retention,
backup/restore tooling, export/deletion, feedback, CI, live database/vector
contracts, and smoke/load probes are present. The local production-image path
has passed exact proxied SSE smoke/load checks and fixed-finding container scans.
CI run `33488909029` passed backend, frontend, live PostgreSQL/Qdrant, production
image build, proxied SSE smoke/load, and both fixed HIGH/CRITICAL container
security gates. This is the recorded automated release evidence for the Phase 8
closure.

Phase 6 remains explicitly skipped by owner decision. Therefore Phase 8 closure
does not claim image ingestion, image provenance, or image-isolation acceptance.
The configured production provider is truthfully reported as buffered; native
token streaming remains a deployment capability gate rather than a fabricated
frontend effect. Provider/site rollout and operator sign-off remain deployment
evidence, not unfinished application code.

### Backend and infrastructure

- Containerize API and frontend with pinned, reproducible dependencies.
- Select and document managed/self-hosted Qdrant, PostgreSQL, and asset storage;
  use secrets management, TLS, backups, restore drills, migrations, and separate
  liveness/readiness.
- Use an ASGI production process model compatible with model memory and Qdrant;
  measure worker count rather than duplicating large models blindly.
- Add request IDs, JSON logs, metrics/traces, rate limits, body/time limits,
  graceful shutdown, disconnect cancellation, provider timeouts/retries with
  idempotency, and circuit-breaking where measured.
- Validate real provider streaming through proxies and record p50/p95 time to
  first token. Buffered delivery may remain explicit but cannot be labeled as
  token streaming.
- Add CI for Python tests/audits, frontend typecheck/lint/tests/build, production
  bundle secret scan, migrations, Qdrant integration, and end-to-end SSE.

### Security and privacy

- Threat-model prompt injection from SEC excerpts and conversation memory.
- Enforce tenant authorization server-side on every history, asset, and memory
  access; add CSRF/session policy appropriate to the chosen authentication.
- Add secure headers/CSP, dependency and container scanning, secret rotation,
  audit events, abuse controls, data export/deletion, retention, and privacy terms.
- Never expose provider errors, prompts, scores, API keys, Qdrant credentials,
  database IDs, stack traces, or arbitrary filesystem/object keys.

### User experience

- Add history navigation only when persistence/identity is ready; retain mobile,
  keyboard, focus, reduced-motion, contrast, live-region, table, and image access.
- Make clarification, no-evidence, no-citation, partial-stream, reconnect, image
  failure, and deleted-conversation states explicit and tested.
- Represent pre-answer service and plan failures as distinct actionable error
  states, never as the generic assistant text `AVA could not complete this
  response. Please try again.`
- Add user feedback tied to answer/evidence/version metadata without exposing
  internal scores.

### Release definition

AVA is “finished” only when:

- all P0/P1 acceptance gates pass in CI and a production-like environment;
- the eleven-company index is reproducible and rollback/restore tested;
- real multi-company questions meet the balanced 10-per-company/50-total target or expose a
  measured balanced-partial diagnostic while still answering supported parts;
- visible sources exactly match validated used evidence;
- memory isolation/provenance tests pass, and image gates pass only if Phase 6 is
  resumed;
- generation/citation evaluation has signed-off thresholds;
- real streaming/buffered behavior is truthfully represented;
- backup recovery, deletion, security, accessibility, and load tests pass;
- deployment and operator runbooks match the deployed versions.

## 15. Phase 9 — Bounded routing, tools, uploads, and conversation workspace (P1)

This phase implements the owner's 1 September decision. “Agentic” here means a
bounded, typed route-and-tool orchestrator, not an autonomous agent. It chooses
the minimum evidence path needed for the current request, executes only
allow-listed read-only tools, and ends after a configured finite number of
steps. No model may execute arbitrary code or invent a tool result.

### 9A. Route before retrieval and harden planning/generation

- Add one shared typed route contract before SEC retrieval. Supported route
  families are `conversation_only`, `filing_rag`, `uploaded_document_rag`,
  `web_search`, `calculator`, bounded combinations of those evidence routes,
  and `clarify`. The original user query remains unchanged.
- Use deterministic high-confidence checks for greetings, application help, and
  explicit arithmetic, then a structured LLM decision only where needed. Record
  the route, reason code, evidence requirements, allowed tools, company scope,
  and `arithmetic_required`; never request or persist chain-of-thought.
- A greeting such as `Hello` must use `conversation_only`, return no filing
  sources, and never run dense/BM25 retrieval. A general or current question
  outside the fixed filing corpus must not receive arbitrary SEC chunks.
- Preserve exact/fuzzy/LLM-assisted company resolution, but treat aliases,
  product/technology descriptions, conversation context, and planner output as
  advisory signals. A question need not contain a ticker. When a description
  maps uniquely and confidently to one corpus company, add the canonical company
  only to the internal retrieval query. When it does not, search the appropriate
  corpus scope or ask a concise clarification instead of raising a planner
  contract error.
- Normalize benign planner mention/scope inconsistencies into a validated
  allowed-ticker subset with a diagnostic. Reject only out-of-corpus tickers,
  malformed output, or genuinely ambiguous targets. Add regression cases for the
  observed empty greeting plan and `Planner mention ticker is absent from
  resolved_tickers` failure.
- Split the router prompt, filing planner prompt, and route-aware answer prompts.
  Filing answers remain grounded only in validated filing evidence. Greetings do
  not require citations. Web and uploaded-document prompts label their content as
  untrusted evidence and require source-specific citations for factual claims.
- Keep internal chunk/source IDs through generation and exact citation
  resolution, then remove only validated internal citation markers from visible
  answer Markdown. Do not remove arbitrary bracketed user text. Source cards and
  backend diagnostics retain provenance; raw chunk IDs are not user-visible.

### 9B. Calculator and web-search tools

- Implement the calculator first as deterministic server code using decimal
  arithmetic and an allow-listed expression grammar. Never use `eval`, Python
  execution, or model arithmetic as the calculator. Preserve operands, operation,
  units, rounding rule, and result in a typed tool record.
- `arithmetic_required=true` makes calculator execution mandatory. If operands
  come from filings, uploads, or web results, retrieve them first, run the
  calculator, and cite those source operands in the final response. If required
  operands or compatible units are absent, ask for them or abstain; do not guess.
- Add a provider-neutral `WebSearchTool` interface. Prefer the primary provider's
  native search only when the configured endpoint demonstrably supports it;
  otherwise use a separately configured search adapter. The current buffered
  chat-completions gateway must not be silently migrated to a different API.
- Web results must carry title, canonical URL, publisher/domain, retrieval time,
  and bounded excerpt text. Present web sources distinctly from SEC filings and
  uploads. Apply timeouts, result/count/byte limits, URL safety, content-type
  validation, and no follow-on actions from instructions found in a page.
- Default orchestration limits are four total tool executions, at most two web
  searches, no recursive planning, and one final generation. Limits are typed
  configuration and must be covered by timeout, cancellation, and audit tests.

### 9C. Conversation-scoped document upload and Sources

- Accept PDF and UTF-8 plain-text files initially. Validate MIME type and file
  signature, reject encrypted/active/malformed content, cap each file at 20 MiB,
  200 pages, and 200,000 extracted tokens, and enforce per-chat/user quotas. Do
  not execute embedded code, links, macros, actions, or document instructions.
- Store immutable bytes in the private asset store and ownership/order/status in
  PostgreSQL. Store derived chunks in a separate Qdrant collection with mandatory
  tenant, user, and conversation filters. Never mix uploaded points into the
  filing collection or another chat, and cascade deletion through bytes,
  metadata, chunks, source-use rows, and derived summaries.
- Treat extracted content as quoted untrusted evidence. Server-owned delimiters
  and source IDs identify it; text resembling system/developer/tool instructions
  has no authority. Add prompt-injection, exfiltration, cross-user, cross-chat,
  oversized-file, malformed-PDF, duplicate-upload, and deletion tests.
- Make successfully processed uploads available to the exact chat's short-term
  evidence path. They are not long-term user memory and are not silently used in
  other chats. Summaries must retain document provenance and be rebuildable.
- Add a `+` upload control opposite Send, with keyboard-accessible status and
  progress. A per-chat Sources control lists uploaded files and their processing
  state. Uploaded text differs from pasted chat text by being immutable,
  attributable, queryable source material shown in that Sources view.

### 9D. Left sidebar and conversation controls

- Replace the current history placement with a responsive left sidebar/drawer.
  Use an accessible temporary sidebar icon until the owner supplies another
  asset. It contains New chat, the explicit long-term Memory toggle, pinned chats,
  and all remaining chat history.
- Each chat row exposes a three-dot button only on hover, keyboard focus, or when
  its menu is open. Three-dot activation and right-click open the same accessible
  menu with Pin/Unpin, Rename, and Delete. Escape closes it, focus returns to the
  invoking row, and touch/mobile has an always-reachable equivalent.
- Persist pin state and ordering in PostgreSQL behind owner-scoped API methods.
  Rename/delete retain current isolation, CSRF, idempotency, audit, and cascade
  rules. The sidebar does not store transcript or identity data in browser
  persistence.

### 9E. Evaluation and acceptance

- Freeze route labels before implementation: greetings/small talk, AVA help,
  in-corpus SEC questions with name/ticker/no explicit company, unique and
  ambiguous technology descriptions, follow-ups, current/out-of-corpus web
  questions, pure arithmetic, evidence-derived arithmetic, upload-only questions,
  mixed-source questions, prompt injection, and unavailable-tool cases.
- Measure route accuracy, unnecessary retrieval/tool-call rate, company scope,
  retrieval recall, answer grounding, citation/source exactness, calculator
  exactness, web freshness/provenance, injection resistance, tenant/chat
  isolation, latency, token usage, and cost separately.
- Acceptance requires zero filing retrieval for the greeting set, 100% calculator
  use on labeled calculation cases, no raw internal IDs in rendered answers,
  exact source ownership, no prompt-injection policy violations, and no regression
  in the saved SEC retrieval/generation gates. Each tool and route remains behind
  a kill switch with a tested filing-only rollback.

## 16. Phase 10 — Lower-priority measured improvements (P2)

Implement only after a saved failure demonstrates the need:

- Cross-encoder reranking, first behind an experiment flag and evaluated per
  company/content type. Keep the non-reranked RRF baseline.
- A small LLM evidence selector after deterministic quota allocation. It may
  order or reject candidates but cannot violate company targets or hard caps,
  invent IDs, or become the only auditable ranking signal.
- Query expansion or HyDE for terminology gaps, with original-query retrieval
  preserved as one fusion input.
- Section-aware or content-type-aware retrieval weights.
- Table row/column focused generation views while retaining the full validated
  table as the source object.
- Multimodal embeddings for figures if caption/OCR/adjacency retrieval misses the
  labeled image set.
- Semantic caching keyed by corpus/index/prompt/model version, only after history
  and tenant privacy rules are enforced.
- Answer regeneration, citation highlighting, source-to-claim navigation, and
  export after the core source contract is stable.

Each improvement needs its own flag, versioned evaluation result, latency/cost
measurement, rollback, and a removal decision if it does not help.

## 17. Phase 11 — Only if time (P3)

- Admin dashboards beyond essential operational monitoring.
- Advanced conversation organization such as folders, tags, search, and sharing.
- Fine-grained user preference memory beyond explicit opt-in facts.
- OCR language expansion or image-region annotations beyond the corpus need.
- Offline/background answer jobs for unusually large all-company reports.
- Additional provider adapters after the primary provider is reliable.

Explicitly out of scope unless the owner creates a new decision: expanding the
filing corpus, Graph RAG, open-ended/autonomous agent loops, arbitrary code or
shell tools, actions that mutate external systems, portfolio/trading
functionality, and a human-like AVA persona.

## 18. Expected module boundaries

The exact filenames may evolve, but responsibilities must stay independently
testable and outside FastAPI/React adapters.

```text
src/
  orchestration/
    routing.py            typed route decision, validation, and finite execution
    models.py             evidence/tool contracts and limits
  tools/
    calculator.py         deterministic decimal expression evaluation
    web_search.py         provider-neutral bounded search interface/adapters
  resolution/
    companies.py          exact/fuzzy/LLM validation and shared result types
  retrieval/
    interface.py          backend-independent retriever protocol
    local.py              NPZ/BM25 parity implementation
    qdrant.py             Qdrant query/filter/hydration implementation
    allocation.py         per-company quota and token-aware context packing
    reranking.py          optional measured rerankers
  assets/
    acquisition.py        SEC image fetch and immutable manifest
    extraction.py         DOM/block association and derivatives
    selection.py          relevance and novelty gate
  conversations/
    models.py             DB entities and migrations
    service.py            idempotent turns and transcript operations
    context.py            short-term window and rolling summary
    memory.py             tenant-filtered long-term memory
  documents/
    service.py            upload ownership, validation, lifecycle, and deletion
    extraction.py         bounded PDF/text extraction as untrusted evidence
    retrieval.py          tenant/user/chat-filtered document evidence
  generation/
    rag.py                prompt/generator and citation parsing
    citations.py          exact used-evidence validation
  backend/
    pipeline.py           orchestration only
    sources.py            frontend-safe text/table/image adaptation
    app.py                validation, auth boundary, SSE and asset routes
```

The evaluator, notebook, CLI, and API import these shared modules. A notebook is
never a production dependency and a script must not retain a second resolver or
selector.

## 19. Configuration decisions still owned by the project owner

These must remain configuration with documented defaults until explicitly set:

1. Supplemental final evidence for four or more explicit companies.
2. Model input/output token reserve and maximum all-company request size.
3. Whether the measured final selector remains RRF/diversity, uses the existing
   cross-encoder, or adds a bounded LLM selector.
4. Qdrant deployment mode and retention period for the pre-cutover collection.
5. Vision model/provider and whether OCR/vision derivatives may be generated
   during ingestion.
6. Authentication provider, single-user versus multi-user deployment, retention,
   and whether long-term memory is opt-in.
7. Production SLOs and accepted evaluation regression thresholds.
8. The production web-search provider/credentials after interface and local
   contract tests; lack of a provider must produce an explicit unavailable state.
9. Private production asset-store implementation and retention quota for uploads;
   local development defaults to an ignored, permission-restricted data path.

An implementation agent may recommend values with measurements, but must not
hard-code a product decision merely to complete a phase.

## 20. Failure diagnosis tree

```text
Was the intended company resolved?
├─ no  → normalization, alias, fuzzy threshold, LLM resolver, or ambiguity issue
└─ yes
   Is the evidence in the frozen filing/image assets?
   ├─ no  → corpus boundary or correct abstention
   └─ yes
      Is it in structured blocks/table/image manifests?
      ├─ no  → preprocessing, table, or image-ingestion issue
      └─ yes
         Is it represented in chunks/vectors/payloads?
         ├─ no  → chunking, embedding, Qdrant import, or version issue
         └─ yes
            Is it in the correct per-company candidate pool?
            ├─ no  → dense, lexical, filter, fusion, or query-planning issue
            └─ yes
               Did it survive rerank, diversity, quota, and token packing?
               ├─ no  → final evidence allocation issue
               └─ yes
                  Was it supplied to generation/vision?
                  ├─ no  → context or image novelty-selection issue
                  └─ yes
                     Did the answer use and cite it correctly?
                     ├─ no  → generation, grounding, or citation issue
                     └─ yes
                        Was exactly that source displayed?
                        ├─ no  → source resolution/API/frontend issue
                        └─ yes → successful evidence chain
```

## 21. Documentation maintenance rule

- `README.md`: concise current state, setup, commands, and link here.
- `IMPLEMENTATION_PLAN.md`: all future architecture, priorities, numbers,
  acceptance gates, and open decisions.
- `ROADMAP.md`: compact phase index linking here; no duplicated thresholds.
- `DEPLOYMENT.md`: current verified deployment behavior and operator setup only.
- `frontend_plan.md`: historical stateless frontend design plus a link here for
  future image/history/source-contract work.
- `src/frontend/PROGRESS_REPORT.md`: chronological implementation log, not a plan.

If documents disagree about future work, this file wins. If code differs from
the “Audited baseline” section, update the baseline in the same change that
changes the code.
