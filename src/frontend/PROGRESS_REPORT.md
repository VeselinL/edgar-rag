# AVA Frontend and API Progress Report

This report records the work performed for the AVA local vertical slice. Work
began on `deploy_front`, then moved to the dedicated `edgar-rag-frontend` branch
and linked worktree at the user's request. Existing unrelated work was preserved
and was not included in AVA commits.

## 2026-08-20

### Repository verification and discovery

- Confirmed the initial active branch was `deploy_front`; the later worktree-isolation section records the requested branch move before implementation continued.
- Inspected the working tree before editing. Pre-existing modified and untracked files were found, including retrieval evaluation, notebooks, dependency changes, documentation, `src/generation/`, and the supplied avatar assets. These are treated as user work and will not be overwritten or bundled into AVA commits unless a task-required edit is made deliberately.
- Located the only applicable root instruction file at `AGENTS.md`; an unrelated `AGENTS.md` exists in a sibling repository and does not govern this workspace.
- Confirmed the canonical supplied assets exist at `src/frontend/avatar/ava.png` and `src/frontend/avatar/favicon.png`.
- Surveyed the repository layout, current documentation, dependency files, source packages, test suite, generated chunk/embedding artifacts, and empty `src/backend/` package directory.
- Began tracing the scope-aware hybrid retrieval implementation and its uncommitted enumeration-query additions. No retrieval behaviour has been changed.

### Phase 1 — documentation

- Inspected the README, roadmap, architecture document, all repository documentation, dependencies, Python entry points, tests, environment-variable names, current git history, chunk-schema-v3 narrative records, table-schema-v2 logical table records, embedding layout, avatar files, and LLM call sites.
- Extracted and reviewed every code cell in `notebooks/hybrid_rag_generation.ipynb` and the relevant reranking notebook functions, then traced helpers imported by `src/scripts/evaluate_scope_aware_hybrid_retrieval.py`.
- Recorded the verified source-of-truth discrepancy: the generation notebook contains planning/generation/citation logic and a 12-chunk budget; the evaluator contains the authoritative regex scope/Comparison Cue policy; the cross-encoder exists in a separate experiment. The API cannot truthfully import one existing complete pipeline because one did not yet exist.
- Added `DEPLOYMENT.md` with local/production architecture, backend-only retrieval responsibilities, real streaming POST/SSE transport, health/readiness, source schemas, environment handling, security rules, deployment measurements, and future milestones.
- Updated `ROADMAP.md` to reflect the aligned ten-company corpus, completed hybrid/scope evaluation, measurable generation/frontend/API acceptance gates, and explicitly deferred capabilities.
- Updated root `AGENTS.md` without removing SEC acquisition, processing, evaluation, or raw-data safety rules. Added AVA identity, supplied-asset protection, shared retrieval requirements, stateless API constraints, structured-table rules, streaming/accessibility/theme requirements, exclusions, testing, progress-report, branch, and commit rules.

### Phase 2 — frontend plan

- Added `frontend_plan.md` before creating implementation code.
- Specified the complete identity, visual tokens, theme initialization, header, empty state, transcript, composer, waiting state, real SSE parsing, source presentation, state machine, errors, responsiveness, accessibility, component structure, automated tests, and manual visual matrix.
- Kept company detection, Comparison Cues, evidence allocation, merging, reranking, and citation validation entirely on the backend; the plan sends each original current query unchanged.
- Cross-checked `DEPLOYMENT.md`, `ROADMAP.md`, `AGENTS.md`, and `frontend_plan.md` for stale Tesla/chunk state, Streamlit/Gradio direction, conversation persistence, scope ownership, source schemas, streaming transport, avatar paths, themes, and deferrals. Corrected the old implementation-order reference to conversation history so it no longer conflicts with the stateless first version.

### Worktree isolation

- At the user's request, moved ongoing implementation to the existing linked worktree at `/home/veselin/Documents/Programiranje/edgar-rag-frontend`.
- Renamed that worktree's checked-out branch from `deploy_front` to `edgar-rag-frontend` and verified it remains based on documentation commit `33fc001`.
- Transferred the in-progress backend, shared retrieval/generation modules, tests, frontend scaffold, and supplied assets into the dedicated worktree.
- Removed only the transferred uncommitted implementation files/hunks from the original workspace. Preserved its pre-existing modified retrieval evaluation, dependency, notebook, report, avatar, and empty generation-package work.

### Phase 3 — shared backend foundation

- Extracted regex company/ticker/alias detection, Comparison Cues, scope classification, dense/BM25 retrieval, RRF, comparison-balanced merging, stable-ID deduplication, cross-encoder pair scoring, and the 12-chunk final selector into `src/retrieval/scope_aware.py`.
- Added regression tests for one company, ticker, alias, two-company queries, Comparison Cues, global scope, multi-scope deduplication, target-company coverage, final context budget, and citation resolution.
- Extracted the notebook's grounded prompt, context formatting, OpenAI-compatible streaming/non-streaming generation boundaries, and exact final-evidence citation resolution into `src/generation/rag.py`.
- Added FastAPI health and POST/SSE streaming adapters, explicit real/mock runtime assembly, client-disconnect checks, safe error events, and backend-only narrative/table source normalization.
- Added deterministic mock cases for normal fragments, pre-token failure, and mid-stream failure.
- Verified 871 of 877 real table chunks normalize from validated logical headers/rows. Six table chunks lack trustworthy logical headers and are rejected instead of reconstructed from Markdown or assigned fabricated headers.
- Ran 21 focused backend tests: 20 passed initially and one test-double signature failed; updated scoped calls to use explicit `allowed_tickers`, then reran all 12 scope/citation tests successfully. Python compilation of the new backend, retrieval, and generation packages also passed.

### Phase 3 — frontend scaffold

- Added the React 19 + TypeScript + Vite project configuration, strict TypeScript settings, ESLint/Vitest setup, pre-paint theme initializer, AVA page metadata/favicon, API source/message types, streaming POST/SSE parser, theme hook, and initial focused components.
- Added header branding/theme toggle, canonical avatar component, empty state, keyboard composer, retrieval waiting bubble, and narrative/structured-table source components as the initial frontend checkpoint.
- Completed the conversation renderer, safe Markdown output, near-bottom scroll control, local-only transcript state, request lifecycle, source expansion, structured HTML table rendering, responsive CSS, and accessible statuses/focus/reduced-motion treatment.
- Installed frontend dependencies and generated `package-lock.json`. The initial audit found vulnerable older Vite/Vitest pins; updated to patched Vite 7.3.6 and Vitest 3.2.7. A subsequent high-severity audit reports zero vulnerabilities.
- Ran frontend checks after implementation: ESLint passed, 7 Vitest tests passed, and the TypeScript/Vite production build completed successfully.

### Real-pipeline validation

- Copied the ignored local NPZ artifacts and `.env` into the dedicated worktree so real-mode checks can run without exposing or committing either.
- Loaded all 4,115 chunks/vectors, built BM25, and loaded the cached BGE-base embedder plus BGE reranker.
- Compared direct production retrieval with `evaluate_scope_aware_retrieval` for six real queries: company name, ticker, alias, explicit Tesla/Ouster comparison, existing Comparison Cue, and global/no-company scope. All six matched exactly on detected companies, comparison status, scopes, evidence companies, final count, and ordered pre-normalization chunk IDs.
- Confirmed the explicit Tesla/Ouster comparison final context contains both target companies and exactly 12 chunks.
- Exercised the configured LLM gateway with `stream=True`. The gateway returned HTTP 201 with `Content-Type: application/json`, no transfer encoding, and zero parsed streaming chunks. Adding `Accept: text/event-stream` did not change the response. This is a real integration blocker outside the adapter: the current gateway ignores native chat-completions streaming.
- Updated generation to fail explicitly when the provider returns a non-SSE response or a stream ends with no text. It never converts the completed JSON answer into fake fragments.
- Re-ran the narrowed real provider call after adding the guard. It raised the expected safe `The configured LLM gateway did not provide a streaming response.` error, confirming the adapter now rejects the gateway's completed JSON response at the streaming boundary.

### Manual visual verification

- Started the FastAPI adapter in explicit mock mode and the Vite development server, then drove Firefox headlessly through the real browser UI.
- Inspected light desktop, dark desktop, light mobile breakpoint, dark mobile breakpoint, empty state, waiting bubble, completed streamed response, expanded narrative source, structured table source, and partial-response error.
- Verified the waiting bubble is visually anchored above-right of the AVA response avatar and is absent once streamed text appears.
- Detected Firefox's white native scrollbar track against dark mode and added theme-aware scrollbar colours; the dark page no longer shows a white edge.
- Expanded the deterministic mock table to eight columns for a real narrow-layout overflow check. Measured `scrollWidth` 496 px versus `clientWidth` 384 px and successfully moved `scrollLeft` to 112 px, confirming the table scrolls inside its source container.
- Confirmed the supplied AVA image is contained without stretching or destructive cropping in header, empty, and response placements in both themes.

### Shared notebook handoff

- Added the previously untracked `notebooks/hybrid_rag_generation.ipynb` to the dedicated branch because it is a mandatory behavioural source of truth.
- Preserved its historical experiment cells/outputs and appended a clearly labelled production-path cell that constructs `ScopeAwareRetriever` and `GenerationService` from the same modules used by evaluation and FastAPI, including cross-encoder reranking, the existing configuration, real provider streaming, and citation resolution.

### Final verification

- Ran the complete Python suite: 90 of 92 tests passed. The two failures are pre-existing repository consistency failures outside the AVA changes: the table embedding-text test expects currency symbols to be removed although the current formatter retains them, and the baseline-review test contains an outdated expected baseline hash. No AVA file changes the formatter, saved baseline, review record, or either failing test.
- The external application environment used for final real-provider validation did not contain `pytest`; installed only the test runner into that environment (not the repository dependency set) so the requested final backend check could execute.
- Re-ran the focused AVA backend suite separately: all 24 scope-aware retrieval, generation, source-normalization, health, SSE-ordering, and failure-path tests passed.
- Re-ran frontend lint, all 7 component/unit tests, the production build, dependency audit, and bundle secret/branding scan; all passed and the audit reported zero vulnerabilities.
- Validated the notebook as JSON and ran whitespace/error checks over the final diff.
- Stopped the temporary Vite and mock FastAPI processes used for browser verification.

### Main-notebook pipeline synchronization

- Recreated the dedicated linked worktree after it had been removed, checked out `edgar-rag-frontend`, and left the dirty `main` worktree untouched.
- Re-read the current untracked main-branch `notebooks/hybrid_rag_generation.ipynb` and traced its imported `retrieve_generation_context` helper through the modified main-branch scope-aware evaluator.
- Confirmed the new authoritative path uses an LLM atomic-subquery planner, scope-aware BGE/BM25 RRF retrieval of 10 candidates per subquery, stable-ID merge/deduplication, two coverage rounds per subquery, a `0.01` multi-subquery bonus, and a fixed 10-chunk answer context. The active notebook path does not use the cross-encoder.
- Ported that selector into the shared production retrieval module and made the evaluator expose a compatibility wrapper around the same function.
- Updated FastAPI's real pipeline to run the notebook planner, retrieve using the returned subqueries, and stream the answer using the original query and selected evidence. Removed cross-encoder loading from real API startup.
- Updated the shared generation prompt and planner instruction/JSON contract to match the current main notebook.
- Added a configuration guard that rejects more subqueries than the 10-chunk budget can cover at two chunks each, preventing silent subquery starvation.
- Updated the branch notebook's shared production cell and project/deployment/frontend documentation from the superseded 12-chunk reranker path to the planned 10-chunk path.
- Added regression tests for planner behavior, two-per-subquery selection, 10-chunk total budget, stable-ID deduplication, multi-subquery scoring, impossible coverage plans, and planner-to-retriever API orchestration.
- Compared the shared selector directly with the current main evaluator using identical deterministic candidates. Scope, companies, subqueries, coverage, selected IDs, and selection reasons matched; the representative result selected 10 chunks with `[5, 5]` subquery coverage.
- The first test invocation used the recreated worktree's absent `python` command and did not start; reran compilation and tests with the existing application virtual environment successfully.
- Ran the real LLM planner and production selector against all 4,115 corpus chunks for the notebook's three-fact Ouster question. The planner produced three subqueries; the selector returned 10 unique Ouster chunks with coverage `[9, 8, 10]`, satisfying the two-per-subquery invariant.
- Re-ran the focused backend suite after synchronization: all 30 tests passed, with only the existing Starlette `TestClient` deprecation warning. Python compilation, notebook JSON validation, and diff checks also passed.
- Ran the complete Python suite after the pipeline change: 96 tests passed with 157 subtests, and the same two pre-existing repository consistency tests failed (currency-symbol normalization expectation and stale baseline-review hash). No new pipeline test failed.
- The recreated worktree also lacked ignored frontend dependencies, so the first frontend commands did not start. Restored exactly the lockfile dependencies with `npm ci` (zero vulnerabilities), then reran ESLint, all 7 frontend tests, and the production build successfully.

### Explicit buffered real-answer delivery

- Reproduced the local Unique gateway response with the configured Azure-backed deployment: `stream=True` returned HTTP 201 JSON with zero stream fragments even with `Accept: text/event-stream` and `x-model`.
- Verified Unique's documented `/public/openai-proxy/` route and the Responses API route are not deployed on this tenant; both returned 404.
- After explicit approval to disable token streaming, added strict `AVA_LLM_STREAMING=true|false` configuration. The existing native-stream path remains available; buffered real mode uses the same planner, retrieval, prompt, citations, sources, and SSE endpoint but emits the completed provider answer as one `delta` without simulated typing.
- Enabled `AVA_LLM_STREAMING=false` only in the ignored local `.env` and exposed the active answer-delivery mode through the health response.
- Ran the real FastAPI pipeline on an isolated port against the configured Unique tenant. Health reported `mode=real`, `pipeline_ready=true`, and `answer_delivery=buffered`; a Tesla query produced one grounded answer delta, resolved SEC filing sources, and terminated with `done`.
- The live response grouped several chunk IDs inside one citation bracket, so citation parsing now accepts semicolon- or comma-separated valid IDs while continuing to reject bracketed prose.
- Focused generation, real-pipeline, and API tests passed (16 tests). The complete Python suite ran 103 tests; only the same two pre-existing repository consistency failures remained (currency-symbol embedding-text expectation and stale baseline-review hash). Frontend ESLint, all 7 Vitest tests, and the TypeScript/Vite production build passed. Python compilation and diff whitespace validation also passed.

### Rivian corpus integration

- Added Rivian as the eleventh active AVA filing in the shared runtime/evaluation corpus registry and added `Rivian`, `Rivian Automotive`, and `RIVN` scope detection coverage.
- Included Rivian's validated 411 chunks and aligned 768-dimensional BGE-base vectors in real API startup, corpus-wide evaluators, and retrieval notebooks.
- Updated the browser corpus count from ten to eleven while keeping all company detection and filtering on the backend.
- Verified the real loader produces a 4,526-by-768 matrix, production and evaluator retrieval match on a Rivian query, and the expected Rivian revenue table ranks first for its new evaluation question.
- Passed strict eleven-company table/chunk and embedding audits with zero failures, 35 focused Python tests, frontend lint, all 8 frontend tests, and the production build.

## 2026-08-27

### Canonical completion planning

- Audited the current company detector, scope policy, per-subquery retrieval,
  final evidence selection, generation/citation fallback, source normalization,
  SSE API, React source rendering, corpus artifacts, documentation, and tests on
  `main` without changing runtime behavior.
- Verified the current explicit-subset search uses one combined company filter,
  final context is fixed at 10, and a no-citation answer exposes all final-context
  chunks as retrieved evidence.
- Counted 35 image nodes in the frozen HTML across 10 of the 11 filings and
  confirmed preprocessing removes image nodes before structured extraction.
- Found two additional baseline defects for the next P0 milestone: CEO is
  incorrectly expanded as Chief Operating Officer in both prompts, and the
  frontend uses theme-specific avatar/favicon files instead of the canonical
  `ava.png` and `favicon.png` assets.
- Added root `IMPLEMENTATION_PLAN.md` as the Git-visible source of truth because
  `.gitignore` excludes the local `docs/` directory. It defines priorities,
  company-balanced dynamic evidence, cited-only sources, Qdrant migration,
  image provenance/retrieval, short- and long-term memory, production hardening,
  module boundaries, evaluation gates, and owner decisions that remain open.
- Reconciled `README.md`, `ROADMAP.md`, `DEPLOYMENT.md`, and `frontend_plan.md`
  around that authority and corrected stale eleven-company/table-audit values in
  the supporting current-state documentation.
- Added root `CODEX_IMPLEMENTATION_PROMPT.md` as a concise agent handoff. It
  requires the agent to read the full canonical plan and this report, preserve
  unrelated work, implement one phase at a time, satisfy measured release gates,
  and request only decisions that materially block the next phase.

### Completion Phase 0 — frozen P0 baseline and evaluation labels

- Created the dedicated `ava-p0-completion` branch from `main` before editing;
  preserved the pre-existing `CHUNKING_REPORT.md` deletion and did not stage it.
- Added versioned P0 labels for 46 company-resolution cases, five retrieval and
  final-selection cases, seven citation/source-display cases, all 35 raw-HTML
  image nodes, and eight future history/deletion/isolation cases. Exact labels
  cover every configured alias plus tickers, punctuation, casing, the special
  single-letter Ford ticker, typos, transpositions, collisions, multiple typos,
  global questions, and out-of-corpus mentions.
- Added one shared-path baseline command,
  `.venv/bin/python -m src.evaluation.ava_p0 --overwrite`. It loads the real
  4,526-vector NPZ corpus, builds the existing BM25 index, calls the production
  `ScopeAwareRetriever` and citation resolver, and writes a detailed summary plus
  a timing-independent parity fixture. Fixed reviewed subqueries avoid making a
  provider/planner response part of the ranking baseline.
- Added a one-command before/after mode using `--compare-to`. It emits quality,
  latency, token-proxy, selected-ID, and company-balance deltas and refuses to
  treat results from a different corpus fingerprint as parity evidence.
- Recorded pre-change resolution accuracy of 60.87% overall, 96.15% for exact
  cases, and 0% for typo cases. The one exact failure is ticker `F`; ambiguity
  and out-of-corpus clarification remain unimplemented and are scored as such.
- Recorded 100% candidate gold recall across the five retrieval cases, but only
  58.87% final gold recall. The two-/three-/four-/five-company final contexts
  contain company counts of `6/4`, `5/3/2`, `3/3/2/2`, and `2/2/2/2/2`,
  respectively, proving the fixed 10-chunk selector is the first failing stage.
- Verified the depth fixture's four Aptiv chunks rank 15, 21, 23, and 27 globally
  but 5, 7, 9, and 6 inside the Aptiv pool. This freezes evidence for independent
  company retrieval without changing ranking behavior in Phase 0.
- Recorded a 10-chunk budget with 3,842 mean and 4,744 maximum BGE-token proxy
  after complete context formatting. The proxy is diagnostic because the current
  generation gateway does not publish its tokenizer; Phase 3 must configure the
  real generation-token counter before enforcing packing.
- Recorded 42.86% source-display exactness: the three cases containing at least
  one valid final-evidence citation pass, while candidate-only, malformed,
  no-citation, and supported-abstention cases expose fallback evidence and fail
  at the citation stage as expected.
- Validated the image manifest against every immutable raw HTML node: 35 nodes
  across 10 filings, labeled as 29 information-bearing figures, five logo/page
  artifacts, and one low-confidence decorative/unverified figure. No asset was
  downloaded and no raw filing was changed.
- Added nine focused evaluator tests; all passed. Python compilation and diff
  whitespace validation passed. The complete Python suite ran 116 tests: 114
  passed and the same two previously documented repository consistency tests
  failed (currency-symbol embedding-text expectation and stale Mobileye baseline
  review hash); Phase 0 does not touch either implementation or artifact.
- Frontend ESLint, all eight Vitest tests, strict TypeScript checking, and the
  Vite production build passed. The build still contains the non-canonical
  theme-specific avatar assets, as expected before the Phase 1 correctness fix.

### Completion Phase 1 — cited-only sources and verified baseline repairs

- Replaced citation fallback with an exact `CitationResolution` result containing
  answer-order parsed, resolved, and rejected IDs plus a backend-only diagnostic.
  No valid final-evidence citation now resolves to an empty evidence set.
- Replaced the public `citation_fallback` field with `source_status` values
  `cited`, `none_cited`, and `cited_with_unrenderable_items`. The API returns no
  candidate or final-context fallback, while malformed cited tables increment the
  public unrenderable count without causing unrelated sources to appear.
- Updated the TypeScript stream contract and message/source components for the
  new status. A completed answer with no resolved citation renders the existing
  no-reference state and never offers a source-card expander.
- Corrected both generation and planner instructions: CEO is Chief Executive
  Officer and COO is Chief Operating Officer. Added a regression assertion over
  both prompts.
- Replaced the duplicate theme-specific AVA images with one import of canonical
  `avatar/ava.png`, removed the duplicate component props declaration and obsolete
  CSS swapping, and changed the favicon to canonical `avatar/favicon.png`.
- The production build contains hashed outputs derived from `ava.png` and
  `favicon.png` and contains no `ava-light` or `ava-dark` references.
- Added cited-only, no-citation, invented-ID, malformed-table, stream-contract,
  no-reference UI, canonical-avatar, and CEO/COO coverage. All 50 focused Python
  tests and all 10 frontend tests passed; ESLint, TypeScript, and production build
  also passed.
- Compared Phase 1 with the frozen P0 baseline on the same corpus fingerprint.
  Source-display exactness improved from 42.86% to 100% (+57.14 percentage
  points). Resolution, candidate recall, final recall, context token proxy, all
  retrieval candidate/selected IDs, and company balance were unchanged.

### Completion Phase 2 — deterministic-first company and ticker resolution

- Added one shared `CompanyResolver` and `CompanyResolution` contract for the
  fixed eleven-company corpus. It normalizes Unicode, punctuation, apostrophes,
  whitespace, and corporate suffixes; resolves exact aliases/names/tickers;
  preserves explicit-only handling for Ford ticker `F`; then applies
  thresholded Damerau-Levenshtein typo matching with a minimum winner margin.
- Added canonical company names to the active corpus registry and made the API,
  scope detector, P0 evaluator, scope-aware evaluation script, and production
  notebook path consume the shared resolver. Removed the evaluator script's
  duplicate company/scope detector and replaced historical notebook detection
  helpers with shared-resolver calls.
- Extended the existing planner prompt and validated JSON contract with the
  allowed ticker enum, deterministic matches, unresolved shortlists,
  `resolved_tickers`, per-subquery ticker targets, company-mention decisions,
  comparison intent, operation, and ambiguity. The boundary accepts two observed
  harmless provider representations (`"null"` and comparison labels) but still
  rejects malformed, out-of-corpus, conflicting, invented, or out-of-shortlist
  ticker assignments.
- Exact and fuzzy deterministic results cannot be overridden. A validated LLM
  result can resolve only a mention and ticker supplied by the deterministic
  shortlist; an empty shortlist cannot be mapped to any corpus company.
  Low-confidence, collision, and out-of-corpus mentions return a concise
  clarification with no retrieval, answer generation, or source cards.
- Kept the user's original query unchanged for generation. Retrieval-only
  subqueries append canonical company names and tickers, and planner subquery
  targets must cover every validated company without introducing another one.
- Added structured server diagnostics for resolved tickers, method, confidence
  band, unresolved mentions, scope, comparison, and ambiguity. None of these
  diagnostics or the resolver/planner prompt is added to browser events.
- The frozen 46-case resolver evaluation now passes 46/46: 100% overall, 100%
  exact aliases/tickers and explicit multi-company lists, and 100% typo cases,
  with zero silent labeled ambiguity/out-of-corpus resolutions. Mean local
  resolver latency was 16.66 ms (p50 15.74 ms, p95 30.06 ms); exact resolution
  adds no provider call beyond the already-required planner.
- Exercised the configured real planner on deterministic `frod`, shortlisted
  `Telsaaa`, out-of-corpus Toyota, and a Tesla/Ford comparison. All four passed
  final validation: Ford remained deterministic, `Telsaaa` resolved only to
  TSLA, Toyota required clarification, and Tesla/Ford produced independently
  targeted atomic subqueries. These checks called planning only, not answer
  generation.
- Re-ran the corpus-backed P0 comparison against the same 4,526-chunk fingerprint.
  Candidate gold recall remained 100%, final gold recall remained 58.87%, source
  display exactness remained 100%, mean context proxy remained 3,842.4 tokens,
  and no candidate ID, selected ID, or selected-company balance changed. The
  observed retrieval p50 was 592.67 ms versus the frozen 529.64 ms; this phase
  did not change ranking or storage and the five-case timing is diagnostic.
- Added focused resolver, planner-schema, provider-normalization, ambiguity
  short-circuit, canonical-query, shared-entry-point, and fail-closed validation
  tests; all 48 focused tests passed. Python compilation, notebook JSON parsing,
  and diff whitespace validation passed. The complete Python suite ran 136
  tests: 134 passed and the same two pre-existing unrelated consistency tests
  failed (currency-symbol embedding-text expectation and stale Mobileye review
  hash). Frontend ESLint, all 10 Vitest tests, strict TypeScript checking, and the
  production build passed.

### Completion Phase 3 — company-balanced, token-aware evidence selection

- Added one typed `EvidenceBudgetPolicy` shared by the API, evaluator, script,
  and active notebook path. It fixes candidate depth at 10 per relevant
  company/subquery, reserves five final chunks per explicit company, packs 15
  chunks for two companies and 22 for three, and leaves the four-plus
  supplemental count explicitly configurable. An unconfigured four-plus request
  now fails clearly instead of being silently treated as global top-10 retrieval.
- Replaced combined explicit-subset acquisition with independent ticker-filtered
  dense/BM25/RRF pools. Stable-ID merge retains company/subquery, dense, lexical,
  and fusion ranks; deterministic section/content-type/source-span diversity
  precedes two-per-subquery and five-per-company reservation. Every candidate is
  tagged with its final selection or rejection reason in backend diagnostics.
- Added a shared generation-message builder and pinned `o200k_base` token counter.
  Packing counts the complete system/user messages and formatted evidence, reserves
  4,096 output tokens from the configurable 32,768-token context window, passes
  that same output limit to generation, and never truncates a table, cell,
  narrative chunk, or source ID. Impossible explicit and global packs return a
  safe no-source response and retain a diagnosable server-side failure.
- Re-ran the frozen corpus-backed evaluation on the unchanged 4,526-chunk
  fingerprint. Each evaluated company/subquery pool contained 10 candidates;
  candidate gold recall remained 100%. On the three comparable configured cases,
  mean final gold recall increased from 71.11% to 84.44%; candidate MRR remained
  0.7333 and final MRR increased from 0.7333 to 0.7778. Two-company balance moved
  from Tesla 6/Ford 4 to Tesla 8/Ford 7, and three-company balance moved from
  Tesla 5/Mobileye 3/Ford 2 to Tesla 6/Mobileye 10/Ford 6. Every configured case
  met quota; per-company final recall was 0.8/0.8 for Tesla/Ford and 0.6/1.0/0.6
  for Tesla/Mobileye/Ford. The single-company Aptiv case retained all four gold
  chunks.
- The configured cases used a mean 6,109 exact generation-input tokens and a
  maximum 8,088 of 28,672 available. Mean historical BGE context proxy increased
  from 3,865.7 to 5,784.3 as expected from the larger evidence budget. Comparable
  retrieval p50 increased from 382.05 ms to 1,043.05 ms; this measured cost comes
  from the independent company pools and is retained for Phase 4 latency work.
  The four- and five-company cases produced the intended explicit policy error
  because their supplemental budget remains an open owner decision.
- Exercised real-provider answer generation separately from retrieval scoring.
  The three-company answer used 22 final chunks (Tesla 6/Ford 6/Mobileye 10) and
  8,088 input tokens; all 11 emitted citations resolved to final evidence, none
  were rejected, and a separate provider grounding judgment marked 0 of 60
  factual claims unsupported. A preceding two-company run likewise recorded 0
  of 14 claims unsupported, with five resolved and zero rejected citations.
- Added policy validation, independent-pool, two-/three-company quota,
  anchored-global, stable complete-chunk, token-failure, runtime configuration,
  provider output-limit, evaluator, and safe API failure coverage. The focused
  Phase 3 set passed 59 tests. The complete Python suite ran 148 tests: 146 passed
  and the same two pre-existing unrelated consistency tests failed. Python
  compilation, notebook JSON parsing, and diff whitespace validation passed.
  Frontend ESLint, strict TypeScript checking, all 10 Vitest tests, and the
  production build passed.

### Completion Phase 4 — structured observability and generation/citation evaluation

- Added a schema-v1 `RequestTrace` emitted exactly once for every real pipeline
  request. It correlates a server-generated request/turn ID with corpus and local
  index versions, original query, validated resolver decision, retrieval
  subqueries, every candidate's dense/lexical/fusion provenance, quota and token
  allocation, final generation evidence, answer, parsed/resolved/rejected
  citations, displayed-source status, provider usage when available, redacted
  error class, cancellation, stage latency, time to first token, and complete
  latency. Empty image and memory fields reserve later boundaries without
  claiming those features exist; hidden prompts and raw provider errors are not
  included.
- FastAPI remains a thin adapter. It generates one opaque UUID, returns it only
  as the CORS-exposed `X-Request-ID`, and passes it into the shared pipeline. A
  telemetry-sink failure is isolated from the user response. Structured startup
  diagnostics now include corpus/BM25/model load time, RSS, CPU count, chunk
  count, and exact corpus/index identity.
- Added a backend-neutral operations aggregator for p50/p95 stage, first-token,
  and complete latency, provider tokens, safe error/cancellation rates, and
  observed concurrency. Qdrant latency is explicitly `null` until Phase 5 shadow
  reads exist. Operational query/answer logs are documented as access-controlled
  and separate from conversation storage; their external-sink retention is
  configurable with a provisional 30-day default.
- Added seven reviewed generation cases spanning direct fact, table, numerical,
  calculation, synthesis, comparison, and supported abstention. The evaluator
  loads fixed complete filing evidence and reports claim support, completeness,
  numerical correctness, abstention, comparison coverage, contradictions,
  citation precision/recall, invalid IDs, uncited facts, source exactness,
  latency, model, and corpus fingerprint separately from retrieval. Reference
  mode passed every metric at 100%, with zero contradictions, invalid IDs, or
  uncited reviewed facts.
- The fresh seven-case real-provider run reached 100% reviewed completeness,
  labeled support, numerical correctness, abstention accuracy, comparison
  coverage, citation precision/recall, and source-display exactness, with zero
  contradictions, invalid citations, unsupported claims, or uncited reviewed
  facts. The independent atomic-claim judge nevertheless found an uncited final
  comparison synthesis: 2 of 22 non-abstention factual claims (9.09%). A repeat
  of that comparison produced 3 of 8 uncited claims, showing provider variance.
  Prompt instructions now explicitly prohibit `$`-prefixed source IDs and
  uncited conclusions, fixing the observed malformed numerical citation, but
  prompting alone did not make conclusion citation completeness deterministic.
  This is recorded as a generation/citation release-signoff risk, not a retrieval
  or source-display regression.
- Re-ran the corpus evaluation on the unchanged 4,526-chunk fingerprint.
  Resolution remained 100% with ambiguity precision 100% and false-company rate
  0%; candidate recall remained 100%, final recall 84.44%, candidate/final MRR
  0.7333/0.7778, quota satisfaction 100%, and table candidate/final recall 100%.
  Gold evidence survival was 79.31% and selected source-group redundancy 14.55%.
  Candidate and selected IDs were identical to Phase 3; only the strengthened
  prompt increased mean generation input from 6,109 to 6,147 tokens, still far
  below the 28,672 input limit.
- A real end-to-end buffered request emitted `delta`, `sources`, and `done`, with
  two parsed and resolved citations and no rejected IDs. Startup took 4,844 ms
  (1,050 ms corpus load, 585 ms BM25, 3,174 ms model load) at 690 MB observed RSS.
  The request took 3,384 ms: 1,523 ms planning, 629 ms retrieval/selection, 1,204
  ms generation, and sub-millisecond citation/source adaptation. The configured
  gateway again omitted token-usage metadata, so usage correctly remained empty
  instead of being estimated or fabricated.
- Phase 4 focused tests passed 49/49. The complete Python suite ran 159 tests:
  157 passed and the same two pre-existing unrelated consistency tests failed.
  Frontend ESLint, strict TypeScript, all 10 Vitest tests, and production build
  passed. Python compilation, JSON validation, and whitespace validation passed.

### Planner empty-single-query normalization

- Reproduced a provider contract violation that returned
  `needs_multiple_retrievals: false` with an empty `subqueries` list. The strict
  boundary rejected it before retrieval, so FastAPI correctly emitted the safe
  stream error while the backend logged `Planner returned an invalid retrieval
  plan`.
- Added one deterministic recovery allowed by the planner contract: a structurally
  complete single-retrieval plan with an empty list is normalized to exactly one
  subquery containing the user's original text unchanged and the provider's
  already validated ticker list. The normalization is recorded in backend-only
  request telemetry.
- The recovery is fail-closed when `needs_multiple_retrievals` is true, a ticker
  is invalid, or the provider omitted any deterministic company ticker. It does
  not invent companies, rewrite queries, suppress ambiguity, or relax the final
  schema validation.
- Added regression coverage for the observed global-plan response and for a
  malicious/incorrect omission of deterministic Tesla scope. All 34 focused
  generation, pipeline, and observability tests passed; compilation and diff
  validation passed. The full backend suite reached 159 passing tests with only
  the same two unrelated pre-existing consistency failures (the table-embedding
  `$20` assertion and stale Mobileye baseline-review hash). Frontend ESLint,
  strict TypeScript, all 10 Vitest tests, and the production build passed.

### Explicit full-corpus quantifier resolution

- Reproduced `Who is the CEO of each company?`: deterministic resolution treated
  it as an unscoped global question, while the planner correctly returned all
  eleven configured tickers. The validated-mention boundary therefore rejected
  the planner tickers before retrieval.
- Added deterministic full-corpus recognition for unqualified `each company`,
  `every company`, and `all companies` phrases. The shared resolution result now
  carries `explicit_scope_tickers`, keeping corpus-wide intent distinct from
  invented or LLM-resolved company mentions. Exclusion phrases are not expanded
  and require clarification.
- Full-corpus planner output must still contain exactly configured tickers. A
  provider's non-comparison classification is narrowly normalized to the shared
  deterministic result and recorded in backend-only telemetry. The request then
  reaches the existing four-plus evidence policy: it either honors the configured
  five-per-company/token budget or returns a safe narrowing response.
- Added resolver and end-to-end pipeline regressions for the reported query and
  exclusion safety. All 79 retrieval/resolution/planning/policy focused tests
  passed. The complete backend suite reached 162 passing tests with only the same
  two unrelated pre-existing consistency failures. Frontend ESLint, strict
  TypeScript, all 10 Vitest tests, production build, Python compilation, and
  whitespace validation passed.

### Planner-owned semantic intent and actionable error states

- Diagnosed the Tesla/Mobileye CEO failure as a conflation of company count with
  semantic comparison: both company resolvers agreed on TSLA/MBLY, but the shared
  resolver forced `comparison=true` while the planner correctly returned false
  for two independent factual questions.
- Made the single LLM planner authoritative for atomic search-query reformatting,
  comparison intent, and operation. Replaced the planner instruction with an
  explicit numbered contract and concrete independent-fact/comparison/single-fact
  examples. Deterministic exact/fuzzy resolution remains only as the required
  fixed-corpus safety boundary; it no longer overrides planner semantic intent.
- Strengthened plan validation so the multiple-retrieval flag exactly matches
  subquery count and the union of subquery ticker targets exactly matches the
  resolved ticker set. Independent ticker-filtered candidate pools and the
  configurable five-per-company plus supplemental allocation are unchanged.
- Removed the generic `AVA could not complete this response. Please try again.`
  runtime copy. Pre-answer plan failures and service failures are now distinct
  safe SSE error states rendered separately from assistant answer text, without
  raw provider/internal details.
- The configured real planner returned two atomic CEO subqueries targeted to
  TSLA and MBLY, `comparison=false`, and no ambiguity. A real buffered end-to-end
  request emitted `delta`, `sources`, `done`; selected 7 Tesla and 8 Mobileye
  chunks (15 total), satisfied both five-chunk minima, resolved two citations,
  and recorded no safe error.
- Added focused planner contract, independent multi-company, resolver semantics,
  safe API copy, and frontend malformed-error regressions. The complete backend
  suite reached 166 passing tests with only the same two unrelated pre-existing
  consistency failures. Frontend ESLint, strict TypeScript, all 11 Vitest tests,
  and the production build passed.
- Conversation behavior remains intentionally stateless. Phase 7 still requires
  server-owned conversation IDs, ordered/idempotent turns, a token-bounded recent
  window and rolling summary, topic-switch handling, isolation, persistence, and
  deletion gates before short-term or long-term memory is enabled.

### Plural executive-acronym resolution repair

- Reproduced `Who are CEOs of tesla and mobileye.` against the configured
  provider. Exact ticker detection and the LLM plan were correct, but the
  deterministic safety pass treated plural `CEOs` as an unknown company-like
  acronym because only singular `CEO` was exempted. That false unresolved mention
  disagreed with the planner's correct non-ambiguous result.
- Treat grammatical plurals of every known non-company acronym as ordinary query
  terminology. This covers `CEOs`, `CFOs`, `CTOs`, and equivalent configured
  domain acronyms without weakening company/ticker matching.
- Internal plan/validation failures no longer tell the user to restate valid
  wording. They use a safe temporary-service error state that does not expose raw
  details or blame the query.
- The exact real buffered request emitted `delta`, `sources`, `done`, resolved
  TSLA/MBLY with no unresolved mentions, selected 7/8 chunks with both quotas
  satisfied, resolved three citations, and recorded no error. All 79 focused
  planner/resolution/retrieval/API tests passed. The full backend suite reached
  166 passing tests with only the same two unrelated pre-existing consistency
  failures. Frontend ESLint, strict TypeScript, all 11 Vitest tests, production
  build, Python compilation, and whitespace validation passed.

### Multi-company executive evidence retrieval repair

- Diagnosed a supported Ford CEO answer that incorrectly abstained. Ford's
  authoritative executive-officer table is `F-2025-CHUNK-000123` and identifies
  James D. Farley Jr.; it was available in the corpus but absent from final
  evidence.
- The legacy canonical `Company scope` suffix was redundantly appended to
  planner queries that already contained exact company names and degraded the
  Ford candidate ranking. Canonical augmentation now applies only when the
  subquery lacks an exact alias/ticker, preserving its intended typo/LLM-resolved
  safety role without rewriting already-canonical planner output.
- Measured the executive query variants against the unchanged NPZ/BM25 index.
  `Ford Chief Executive Officer name` ranked the authoritative table first,
  versus rank 8 and final rejection for the interrogative CEO query in the
  reproduced three-company plan. Strengthened the planner contract to use the
  exact expanded executive title, company name, and `name` for `who` questions.
- The configured real planner emitted the three expected canonical subqueries.
  A real buffered end-to-end request selected 7 Tesla, 9 Mobileye, and 6 Ford
  chunks; included Ford's authoritative CEO table; named James D. Farley Jr.;
  resolved eight citations; and emitted `delta`, `sources`, `done` without error.
- All 73 focused planner/resolution/retrieval/pipeline tests passed. The full
  backend suite reached 166 passing tests with only the same two unrelated
  pre-existing consistency failures. Frontend ESLint, strict TypeScript, all 11
  Vitest tests, production build, Python compilation, and whitespace validation
  passed.

### Theme-aware AVA avatar

- At the owner's explicit direction, AVA now uses the supplied `ava-light.png`
  in light mode and `ava-dark.png` in dark mode at the header, empty-state, and
  assistant-message placements. The active theme is passed from the app shell,
  so the image updates immediately with the existing theme toggle.
- Updated the product-asset and Phase 1 plan contract to record this accepted
  exception to the previous canonical-single-avatar rule, while retaining
  `favicon.png` as the favicon source.
- Added a frontend regression assertion for both selected avatar assets.

### Planner-authoritative scope and partial evidence delivery

- At the owner's direction, removed the exact-set agreement requirement between
  deterministic company hints and `resolved_tickers`. The single LLM planner now
  owns final scope, query decomposition, and intent; runtime validation still
  rejects tickers outside the fixed eleven-company corpus and inconsistent
  subquery targets.
- Exact/fuzzy/full-corpus resolution is passed to the planner as advisory hints.
  A structurally valid planner subset now proceeds to retrieval instead of
  raising `Planner resolved_tickers disagree with validated mentions.`
- Replaced all-or-nothing company/token packing with fair round-robin partial
  selection. When five complete chunks per company or the configured final total
  cannot fit, AVA retains the supported evidence, records
  `quota_satisfied=false`, and lets generation answer supported company parts.
- Normalized the observed harmless provider representation that echoed
  `all companies` as a company mention with an empty ticker while returning the
  correct complete ticker/subquery scope. Direct pipeline and CLI settings now
  load the project `.env` before constructing the evidence policy, matching
  FastAPI startup behavior.
- The final real buffered all-company run used the current 65,536-token window
  and five supplemental chunks. The planner produced 11 company-specific
  subqueries; selection retained 5/5/5/5/7/5/5/5/7/6/5 chunks across the corpus
  (60 total, 25,243 generation-input tokens), satisfied every quota, and emitted
  `delta`, `sources`, and `done` with no safe error.
- The focused planner/resolver/evidence/pipeline suite passed 58 tests plus two
  subtests. The repository suite passed after excluding only the same two
  pre-existing consistency failures (currency-symbol table embedding text and
  stale Mobileye baseline review). Frontend ESLint, all 11 Vitest tests, strict
  TypeScript checking, production build, Python compilation, and whitespace
  validation passed.

## 2026-08-28

### Fixed corpus-wide and per-company evidence limits

- Replaced the former five-per-company plus supplemental-slot policy with
  policy v2: a hard 50-chunk final-evidence limit per request and a hard
  10-chunk limit per company.
- One through five explicitly scoped companies now target 10 chunks each.
  Larger scopes divide 50 slots as evenly as possible; ten companies target
  five chunks each. Allocation remains round-robin and returns balanced partial
  evidence with `quota_satisfied=false` when candidates or complete-chunk token
  packing cannot meet the target.
- Applied the caps in both the explicit company-balanced path and the generic
  multi-subquery path. Added target counts to evaluator and backend diagnostics.
- Removed the obsolete `AVA_EVIDENCE_FOUR_PLUS_SUPPLEMENTAL` configuration and
  updated the runtime, notebook handoff, deployment guidance, roadmap, and
  canonical implementation plan to use the shared fixed policy.
- The 45-test focused evidence/scope/pipeline suite passed. Frontend ESLint, all
  11 Vitest tests, strict TypeScript checking, and the production build passed.
  The 173-test repository backend suite retained only the same two documented,
  unrelated failures: currency-symbol table embedding text and the stale
  Mobileye baseline-review hash.

### Updated default generation model

- Changed the backend pipeline, generation fallback, local real-mode
  configuration, and README example from the legacy GPT-4o deployment name to
  `AZURE_GPT_51_2025_1113`, the Azure GPT-5.1 snapshot deployment.
- The focused backend pipeline and generation suite passed (37 tests).

### Added persistent Qdrant deployment with parity-first cutover

- Pinned Qdrant server `v1.18.2` and client `1.19.0`; added loopback Docker and
  standalone local-server configuration with persistent ignored storage.
- Added the versioned index builder, deterministic UUID points, named BGE dense
  vector, complete metadata payloads, indexed filter fields, idempotent upserts,
  strict point/payload/vector audits, import manifests, snapshots, restores,
  alias cutover, and explicit rollback commands.
- Migrated and audited all 4,526 current vectors into
  `ava_filing_chunks_89d3a5be9e7d7a8e`, activated the stable
  `ava_filing_chunks_current` alias, created a snapshot, restored and audited a
  second collection, tested alias cutover, and rolled back to the original.
- Added local/Qdrant dense-retriever implementations behind the shared
  scope-aware retrieval module. Local BM25 and custom RRF remain unchanged.
  Backend `disabled`, `shadow`, and `primary` modes include readiness and health;
  configured Qdrant failure leaves real mode unready without mock fallback.
- Saved representative parity evidence: all 11 dense cases met exact top-10 plus
  98% candidate-overlap gates, and all three final hybrid-selection cases had
  exact selected chunk IDs/order. Local configuration now uses shadow mode for
  the soak before an explicit primary promotion.
- The complete backend suite passed 183 tests and 159 subtests; only the two
  pre-existing unrelated failures remained (currency-symbol table embedding
  text and the stale Mobileye baseline-review hash). Frontend ESLint, all 11
  Vitest tests, strict TypeScript checking, and the production build passed.

## 2026-08-31

### Added bounded conversation history and opt-in semantic memory

- Added PostgreSQL-backed, server-owned conversation and message persistence,
  atomic UUID turn idempotency, stored source events/source-use IDs, versioned
  summaries, feedback-ready schema, and deletion audit records.
- Added create/list/resume/rename/delete-one/delete-all API routes with bounded
  pagination and a safe explicit stateless mode. Persistent mode requires an
  acknowledged single-user deployment boundary and never trusts a browser ID as
  tenant identity.
- Added whole-turn short-term selection and rebuildable extractive summaries.
  The planner can resolve follow-ups from that context, while retrieval still
  uses the unchanged current query and evidence packing counts the exact history
  prompt before selecting complete chunks.
- Added a separate Qdrant long-term-memory collection with mandatory tenant and
  user filters, score/count/token bounds, summary-only eligibility, per-chat
  opt-in, and derived-point removal on disable/delete.
- Added responsive History, New chat, rename, delete, delete-all, resume, and
  Memory on/off controls. New chats start memory-off and the browser does not
  persist transcript content independently.
- Pinned `psycopg` and added a loopback PostgreSQL 18 Docker configuration plus
  an opt-in live database contract test. Docker-engine access was unavailable in
  this workspace, so the live test remains gated by `AVA_TEST_POSTGRES_DSN`.
- Focused backend coverage passed 81 tests plus two subtests; frontend ESLint,
  strict TypeScript, all 14 Vitest tests, and production build passed. The
  repository backend suite reached 198 passing tests and one expected skipped
  live-PostgreSQL test; only
  the two unchanged pre-existing embedding/baseline consistency failures remain.

### Executed the frozen conversation-history acceptance gate

- Added a provider-backed evaluator for all conversational turns in
  `conversation_history_v1.json`. It compares query-only and history-aware
  planner results and passes each through the exact company-resolution boundary
  used by FastAPI before scoring scope.
- Kept deletion and tenant/conversation-isolation execution separate from the
  provider audit using deterministic repository and memory doubles. The audit
  continues after an individual planner failure and records only safe failure
  type/stage information.
- The configured provider run passed: history-dependent accuracy improved from
  0% to 100%, standalone accuracy remained 100% with zero regressions,
  contextual topic-switch accuracy was 100%, no planner errors remained, and
  all four deletion/isolation cases passed.
- Normalized one observed harmless gateway inconsistency where a single valid
  subquery retained a multi-retrieval boolean. The repair derives only that
  redundant flag from validated subquery count and records the normalization;
  it does not change queries, company scope, or retrieval.
- The repository suite passes 204 tests with one expected live-PostgreSQL skip
  when the two unchanged pre-existing consistency failures are excluded.
  Frontend ESLint, all 14 Vitest tests, strict TypeScript checking, and the
  production build also pass.

### Added multi-user identity, retention, and recovery controls

- Added provider-neutral OIDC authorization-code/PKCE sign-in with strict token
  validation, PostgreSQL-backed opaque sessions, secure HttpOnly cookies, CSRF
  protection, and server-side tenant/user ownership on every conversation route.
- Added signed-in/signed-out frontend states without persisting access, ID, or
  session tokens in browser storage. Signing out clears the in-memory transcript.
- Added a dry-run-first retention command that deletes derived Qdrant memory and
  eligible canonical records with a content-free audit trail, then purges expired
  OIDC state and sessions.
- Added checksummed PostgreSQL and Qdrant state backups plus guarded PostgreSQL
  and Qdrant restore-drill commands. Documented daily/weekly/monthly retention and
  quarterly isolated restore verification.
- Focused backend retention, auth, API, and backup tests pass. Frontend ESLint,
  strict TypeScript, all 19 Vitest tests, and the production build pass. Live
  container validation is assigned to the production-like Phase 8 gate because
  this workspace cannot access the installed Docker engine.

### Added production hardening and release gates

- Added pinned non-root API and frontend images, a private PostgreSQL/Qdrant
  production topology, separate liveness/readiness probes, graceful shutdown,
  and an SSE-safe Nginx edge configuration.
- Added request IDs, structured JSON logs, security headers, body/rate/time
  limits, provider retry/timeout controls, a measured circuit breaker, and safe
  pre-stream and partial-stream failure states.
- Added CI gates for the backend, frontend, live PostgreSQL/Qdrant contracts,
  migrations, production builds, browser-bundle secret scanning, container
  vulnerability scanning, and proxied SSE smoke/load probes.
- Added answer feedback bound to owned completed messages and saved
  answer/evidence/version metadata. Added an authenticated owner-scoped JSON
  conversation export alongside complete delete-one/delete-all and retention
  controls.
- Added privacy and security documentation, operator incident/rotation guidance,
  and production verification commands. Phase 6 image work remains explicitly
  skipped at the owner's direction.

### Added one-command local AVA startup

- Added `start_app.sh` to start and health-check PostgreSQL, Qdrant, the audited
  filing index, the real FastAPI pipeline, and the Vite frontend.
- The launcher enables server-owned single-user history and optional Qdrant
  conversation memory, accepts both local browser origins, preserves existing
  services and persistent volumes on shutdown, and fails clearly when a required
  dependency, port, provider configuration, or readiness gate is unavailable.
- Routed local browser API calls through the Vite same-origin proxy, matching the
  production Nginx contract and eliminating hostname-dependent CORS startup
  failures between `localhost` and `127.0.0.1`.

## 2026-09-01

### Closed Phase 8 production hardening

- Fixed the local same-origin API route and added the one-command launcher for
  PostgreSQL, Qdrant, the audited filing index, the real API, and the frontend.
- Exempted only liveness/readiness probes from the Nginx request bucket so startup
  polling cannot consume chat capacity; chat and stateful routes remain limited.
- Reworked the SSE load probe to use only the Python standard library so the
  production-container CI job does not depend on a separately provisioned Python
  environment or project import path.
- Constrained SARIF output to the configured HIGH/CRITICAL gate and replaced the
  API runtime image with a pinned multi-stage Debian runtime. Build-only pip and
  its vulnerable vendored packages are absent from the deployed image; runtime
  dependencies and Python 3.12 are copied from the pinned builder.
- Verified the final image runs as UID/GID `10001`, imports the production API,
  model, retrieval, PostgreSQL, Qdrant, and tokenizer dependencies, and passes a
  local production-proxy SSE smoke plus 10/10 concurrent requests with no errors.
  The current local Trivy database reports zero fixed HIGH/CRITICAL findings.
- [CI run 33488909029](https://github.com/VeselinL/edgar-rag/actions/runs/33488909029)
  passed backend, frontend, live PostgreSQL/Qdrant, migrations, production image
  builds, proxied SSE smoke/load, and API/frontend container security gates.
  Phase 6 remains explicitly skipped and is not included in this acceptance.

### Reframed the next implementation phase

- Replaced the former lower-priority Phase 9 with the owner-approved bounded
  route-and-tool phase: route/prompt hardening, mandatory deterministic
  calculation, bounded cited web search, conversation-scoped PDF/text sources,
  hidden raw citation IDs, and a left history/memory conversation workspace.
- Defined explicit evidence authority, prompt-injection boundaries, finite tool
  limits, upload ownership/storage/deletion rules, sidebar behavior, route/tool
  evaluation labels, kill switches, and filing-only rollback requirements.
- Moved measured retrieval/generation experiments to Phase 10 and deferred polish
  to Phase 11. Updated the repository instructions, roadmap index, and Codex
  handoff so they no longer reapply obsolete stateless/no-tool restrictions.

### Froze the Phase 9 routing contract

- Added a shared finite route model for conversation-only, filing, upload, web,
  calculator, bounded evidence-plus-calculator, and clarification paths. The
  contract records only a reason code and execution requirements, never private
  reasoning.
- Added deterministic high-confidence routing for greetings, AVA help, explicit
  filing requests, and self-contained arithmetic. Other requests are formatted
  for a strict model route decision with corpus company/product aliases and
  separately delimited untrusted conversation context.
- Froze 24 route cases before pipeline integration, covering the observed
  greeting failure, ticker/name/product filing questions, no-ticker follow-ups,
  unrelated/current web questions, mandatory arithmetic, chat uploads, and
  genuine ambiguity. Seven focused tests plus 28 subtests pass.

### Routed requests before filing retrieval

- Integrated the shared route contract ahead of planning and dense/BM25 retrieval.
  Greetings and AVA help now return without sources or filing retrieval; current
  and unrelated questions no longer receive arbitrary SEC chunks. Calculator,
  web, and upload routes fail closed until their bounded executors are enabled.
- Hardened the filing planner so a ticker is optional when a configured company,
  product, or technology identifies one company. Benign empty or inconsistent
  single-company planner scope is normalized to validated deterministic scope,
  while out-of-scope planner mentions are removed with diagnostics.
- Added the reusable Phase 9 route evaluator and saved a live-provider run:
  24/24 routes passed, all labeled calculations required a calculator, no
  non-filing case selected filing retrieval, and the evaluator reported no errors.
  The focused routing/generation/pipeline suite passes 78 tests and 31 subtests.

### Added the deterministic calculator boundary

- Added an allow-listed arithmetic grammar over decimal values with bounded
  expression length, operation count, and nesting. It supports direct arithmetic
  plus common percentage, ratio, difference, sum, and rounding requests without
  `eval`, code execution, or model arithmetic.
- Pure calculator routes now execute without filing retrieval or answer generation,
  return an exact auditable result, and record operands, operators, units, rounding,
  and status in request telemetry. Unsafe, malformed, unsupported, and divide-by-zero
  inputs fail closed, and `AVA_CALCULATOR_ENABLED=false` is a tested rollback.
- Added filing-derived arithmetic as a bounded evidence-first path. AVA retrieves
  filing chunks, asks the model only to extract ordered operands, validates each
  verbatim numeric value against every cited chunk, enforces compatible units,
  then executes the requested difference, ratio, percentage, growth rate, or sum
  locally. Missing or ambiguous operands abstain without a model-generated result.
- Verified the structured extraction contract against the configured live provider
  using controlled filing-style table evidence; both values, their shared unit,
  source ID, operation, and rounding were accepted before local execution. The
  focused calculator/generation/pipeline suite passes 69 tests and 8 subtests.

### Added the bounded web-search provider boundary

- Added a provider-neutral web-search interface plus a Brave Search adapter that
  follows the provider's documented HTTPS endpoint, subscription-token header,
  400-character/50-word query limit, and moderate safe-search mode.
- Limited AVA more tightly to 10 results, 1 MiB response bodies, 1,000-character
  excerpts, safe public HTTPS result URLs, no redirects, no page follow-on fetches,
  and no execution of returned markup or instructions. Each accepted result keeps
  title, canonical URL, publisher domain, UTC retrieval time, and a bounded excerpt.
- Added an explicit unavailable adapter so absent credentials cannot silently fall
  back to model knowledge or filing retrieval. Nine calculator/web tool tests plus
  eight subtests pass before runtime orchestration is connected.

### Connected cited web answers and web calculations

- Routed current/external requests through exactly one configured bounded search
  call, then into a web-specific grounding prompt that treats snippets as
  untrusted evidence and requires `web-*` citations. Disabled, failed, empty, and
  uncited searches do not fall back to SEC chunks or model knowledge.
- Added search-plus-calculator execution for current numeric comparisons: search
  first, validate quoted operands against cited web snippets, then run the same
  deterministic calculator. Request telemetry records the ordered web and
  calculator executions without provider response bodies or secrets.
- Added a frontend-safe web source schema and distinct source cards with title,
  publisher, retrieval time, bounded excerpt, and canonical external link; raw
  internal IDs remain backend-only source correlation data. Backend tests pass
  81 tests and 11 subtests; frontend lint, 26 component tests, and production
  build pass.

### Hid internal citation IDs without losing provenance

- Added an incremental citation-visibility filter that suppresses only exact
  citation groups resolved against the evidence supplied to generation. It
  preserves arbitrary bracketed user/model text and unrecognized IDs, and handles
  citation groups split across real provider fragments without buffering or fake
  streaming.
- Filing, web, and evidence-derived calculator answers now send and persist clean
  visible text while retaining the raw generated answer, parsed IDs, resolved IDs,
  rejected IDs, and source cards in backend diagnostics. A split-fragment regression
  proves the browser sees no `CHUNK` marker while the backend resolves the source.
- The focused backend suite passes 101 tests and 11 subtests; frontend lint, all
  26 tests, and the production build remain green.
