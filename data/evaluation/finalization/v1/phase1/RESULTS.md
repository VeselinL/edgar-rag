# Phase 1 — stabilization and cleanup results

**Run date:** 4 September 2026
**Branch:** `ava-completion`
**Starting commit:** `f70bb3aa15cfccf972bda082a73fcfa6f12b8d6d`

## Changes

- Preserved the clean starting state and saved the pre-cleanup environment,
  dependency, corpus, Qdrant, migration, backend, and frontend evidence.
- Resolved all six deterministic failures named by `FINALIZATION.md`: Super
  Cruise resolution coverage, hermetic settings, the 11-company scope contract,
  web-unavailable routing, searchable table values, and the Mobileye review hash.
- Consolidated application configuration in immutable typed settings and kept
  calculator enablement fail-closed in every Phase 1 deployment path.
- Made model selection request-scoped so concurrent requests cannot mutate the
  shared generator.
- Split generation prompts, provider behavior, calculation-plan parsing,
  generation service, and citation handling while retaining compatibility
  imports.
- Split dependency construction, finite request execution, evidence handlers,
  and FastAPI health/auth/conversation/document/chat routes. `pipeline.py`,
  `rag.py`, and `app.py` are compatibility/factory facades.
- Added a regression for real web/upload provider streams and corrected the
  previously undefined streaming-response validator.
- Removed the rejected strict-abstention production flag and runtime prompt
  branch. Historical results remain unchanged in their saved artifacts.
- Archived superseded tracked plans and preserved previously ignored legacy
  documents under `docs/archive/2026-09-pre-finalization/`, each marked as
  historical. Updated active documentation to point to `FINALIZATION.md` only.

## Commits

- `b84d786` `docs: record pre-cleanup verification`
- `14152ac` `test: cover Super Cruise alias`
- `8884df2` `test: enforce all-company retrieval scope`
- `62c617b` `test: preserve searchable table values`
- `11372bb` `docs: refresh Mobileye review hash`
- `59a1196` `fix: prevent stale filing web fallback`
- `6073264` `refactor: centralize runtime settings`
- `1c62d8b` `fix: isolate request model selection`
- `3899940` `docs: archive superseded finalization plans`
- `4dc016e` `refactor: isolate generation boundaries`
- `7f23dde` `refactor: isolate pipeline execution`
- `8d1c063` `refactor: isolate evidence handlers`
- `4f62c3d` `refactor: split backend routes`
- `9654d73` `refactor: remove rejected prompt branch`
- `0f22325` `docs: preserve ignored legacy records`

## Focused verification

- Generation/pipeline/scope/generation-quality: 102 passed and 3 subtests before
  the route split; the prompt-branch cleanup subset passed 105 and 3 subtests.
- Backend API/pipeline/auth/conversation/document: 92 passed, 1 skipped, and 13
  subtests after route extraction.
- Orchestration/API/generation/scope: 121 passed and 3 subtests after handler
  extraction.

## Full exit gate

- Backend: `317 passed, 3 skipped, 243 subtests passed` in 31.66 seconds.
- Frontend lint: passed.
- Frontend component tests: 31/31 passed.
- TypeScript and Vite production build: passed; 298 modules transformed.
- Authority scan: zero matches outside `FINALIZATION.md`, archived documents,
  and historical evaluation data.
- Shared-generator mutation scan: zero assignments to a shared generator model.

The three skips are explicit external-service integration tests. The warnings
are a Starlette/httpx deprecation notice and Qdrant local-mode payload-index
notice; neither is a deterministic failure.

## Live-process verification

`start_app.sh` started the actual real pipeline, PostgreSQL, Qdrant-backed dense
retrieval, owner-scoped history, uploads, and the Vite frontend. `/api/ready`
returned 200; `/api/health` reported a green 4,526-point Qdrant alias and enabled
single-user history; `/api/conversations?limit=1` returned an empty owner-scoped
list. The test used process-local non-secret identity/feature overrides because
the developer's ignored `.env` predates the typed history keys. `.env`, stored
records, and data volumes were not changed. Shutdown stopped only application
processes and preserved both pre-existing containers and volumes.

## Remaining failures

None for the Phase 1 gate. Calculator and web search remain deliberately disabled
until their Phase 2 gates pass.
