# Phase 6 evidence ledger — v19

Status: **passed with documented limitations**.

## Frozen candidate

- Manifest: `data/evaluation/finalization/v19/freeze_manifest.json`
- Frozen source commit: `56d3ae603b7240d82f294e9471befd20fe3da306`
- Corpus/index: 11 filings, 4,526 Qdrant points, artifact version recorded in
  the manifest.
- Provider: Azure-compatible Chat Completions; Tavily trusted-source web path.

## Gates

- Focused backend routing, memory, API, and orchestration tests: **151 passed,
  2 warnings, 6 subtests**.
- Frontend lint: **passed**.
- Frontend Vitest/API/component suite: **43 passed**.
- Frontend TypeScript check and production build: **passed**.
- Existing full-suite result recorded by the completed router-refactor handoff:
  **446 passed, 3 skipped, 267 subtests**.
- Agent route/tool manifest v18: **60/60 passed**, route accuracy 1.0,
  web-required recall 1.0, unnecessary web calls 0, calculator false positives 0.
- Conversation-history manifest v18: gate passed; contextual planner accuracy
  0.8889 overall and 1.0 on history-dependent turns.
- English/Serbian parity: accepted under the explicit 6 September 2026 plan
  exception; citation-ID parity remains 0.8 while company resolution, route,
  gold recall, and numerical parity are 1.0.

## Known limitations carried into release

- The bounded planner can still lose context or reject an ambiguous reference;
  this is reported as a planner limitation, not silently repaired with facts.
- Web quotes remain dependent on qualifying trusted-provider results and their
  timestamps/delay disclosures.
- Serbian wording and citation-ID parity retain the documented exception.
- Optional source-editor UI and other post-release polish are not part of this
  release candidate.

The runtime, corpus, prompts, retrieval parameters, trusted-source registry,
and effective configuration are frozen in the manifest. No holdout tuning was
performed.
