# Phase 6 evidence ledger

Status: **not passed**. This document records the completed measurements for the
v7 candidate and the remaining required gate failure. It does not authorize
Phase 7.

## Frozen candidate

- Manifest: `data/evaluation/finalization/v7/freeze_manifest.json`
- Frozen source commit: `21b3d1919e7611d82fb16c5417f4bbc8eb89fb00`
- Validation after all saved v7 result artifacts: passed.

## Passed checks

- Backend regression suite: `369 passed, 3 skipped, 2 warnings, 255 subtests
  passed`.
- Frontend component suite: `32 passed`; ESLint and production type/build checks
  passed.
- Real `./start_app.sh` startup: API readiness, preferences, and memory endpoints
  responded successfully. PostgreSQL and Qdrant remained persistent.
- Agent route/tool manifest:
  `runs/agent-routes-v1/summary.json` — 60/60 passed, route accuracy 1.0,
  web-required recall 1.0, unnecessary web-call rate 0, and calculator false
  positives 0.
- Conversation/memory evaluation:
  `runs/conversation-history-v1/summary.json` — passed; all 9 contextual planner
  turns and all 4 memory/state cases passed.
- Oracle-context generation runs:
  `runs/generator-oracle-context-run-{1,2,3}/` — 75 raw answers per run, each
  with raw output, failure subset, and reproducibility metadata.

## Required failing gate

`runs/language-parity-v1/summary.json` executed all ten reviewed English/Serbian
pairs without an execution error. It measured company-resolution parity 1.0,
gold-chunk-recall parity 1.0, route parity 0.9, reviewed-number parity 0.9, and
citation-ID-set parity 0.7.

The Serbian revenue-change request now reaches `filing_calculate`, retrieves the
same Tesla source chunk, and cites it. The remaining route mismatch is the English
counterpart selecting `filing` instead of the reviewed `filing_calculate` route.
The other citation differences are in generated citation sets for three
non-numeric prompts. The raw per-case evidence is retained in the run directory.
No additional correction was promoted from these paired release cases.

## Next condition

Do not start Phase 7 until the Phase 6 English/Serbian parity gate passes under a
new validated freeze, or the plan owner changes that gate explicitly.
