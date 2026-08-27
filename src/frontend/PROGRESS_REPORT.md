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
