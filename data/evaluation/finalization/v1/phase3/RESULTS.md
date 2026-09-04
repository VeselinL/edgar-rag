# Phase 3 — functional release candidate freeze

**Run date:** 4 September 2026  
**Branch:** `ava-completion`

## Changes

- Added `python -m src.evaluation.freeze create|validate`.
- Froze the pre-manifest source commit `6c3c9d3` and its tree, all raw metadata,
  processed blocks, chunk and embedding artifacts, prompt hashes, trusted-source
  registry, migrations, locks, retrieval parameters, and sanitized runtime
  configuration.
- Recorded BGE-base revision, Qdrant server `1.18.2`, client `1.19.0`, active
  alias `ava_filing_chunks_current`, physical collection
  `ava_filing_chunks_89d3a5be9e7d7a8e`, and point count `4,526`.
- Validation rejects dirty worktrees, post-freeze code changes, altered corpus or
  prompt inputs, changed trusted sources, changed effective configuration, and
  mismatched live Qdrant state.

## Commits

- `4b3a9c6` `feat: add release freeze validation`
- `8065ed2` `fix: hash typed trusted sources in freeze`
- `610f291` `fix: record freeze runtime versions`
- `434b9fa` `fix: verify frozen runtime state`
- `6c3c9d3` `fix: normalize frozen configuration`
- `652dd36` `test: refresh release freeze`

## Exit gate

- Focused freeze tests: 6 passed.
- `python -m src.evaluation.freeze validate`: passed twice from a clean
  worktree, including live Qdrant alias/collection/point-count verification.
- The live stack launched by `start_app.sh` is ready. Its health response reports
  the frozen real primary Qdrant alias with 4,526 points, routing enabled,
  Tavily enabled, calculator disabled, and the frozen tool limits.

## Remaining failures

None for the Phase 3 freeze gate. Phase 4 ground-truth construction remains
incomplete beyond the reviewed agent route/tool manifest.
