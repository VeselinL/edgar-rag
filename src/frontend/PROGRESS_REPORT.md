# AVA Frontend and API Progress Report

This report records the work performed on the `deploy_front` branch for the AVA local vertical slice. Existing unrelated work is preserved and is not included in AVA commits.

## 2026-08-20

### Repository verification and discovery

- Confirmed the active branch is `deploy_front`.
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
