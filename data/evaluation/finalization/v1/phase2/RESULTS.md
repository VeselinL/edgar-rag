# Phase 2 — bounded routing and tools results

**Run date:** 4 September 2026  
**Branch:** `ava-completion`

## Changes

- Validated the finite `EvidencePlan` path for filing, upload, web, calculator,
  conversation, and clarification routes.
- Replaced the paid Brave adapter with Tavily's direct Search API while retaining
  the reviewed trusted-domain registry and provider-boundary quarantine.
- Recorded the configured OpenAI-compatible gateway's capability probe. Ordinary
  chat and streaming work; strict JSON and native function calls do not. AVA uses
  the validated Python tool executor rather than native provider functions.
- Added the reviewed 60-case route/tool manifest and corrected only observed
  routing defects: resolved product questions, possessive ticker handling, and
  CEO letter-count classification. A CEO-name comparison remains filing-backed
  and never invokes the calculator.

## Commits

- `7f64e67` `feat: add trusted web routing`
- `97df606` `refactor: validate typed evidence plans`
- `5ba266d` `test: record provider capabilities`
- `9059544` `feat: replace Brave with Tavily search`
- `fd30ab1` `docs: document Tavily web search`
- `32072af` `test: verify Tavily web search`
- `6d18fb1` `test: record calculator regression results`
- `6cef175` `fix: reject word-count repetition routes`
- `775398b` `fix: remove stale web activity sources`
- `d7accb1` `fix: cancel web search before execution`
- `af926d3` `fix: quarantine web instructions`
- `ab726ae` `test: add phase 4 route manifest`
- `0f0826b` `fix: isolate conversation scope metrics`
- `19eab34` `fix: route resolved products to filings`
- `a401492` `fix: resolve possessive tickers`
- `c208754` `fix: reject CEO letter count routing`
- `2f7ed4a` `test: correct CEO comparison route`
- `4daf911` `test: record route tool results`

## Exit gate

- Route/tool evaluator: 60/60 passed; route accuracy `1.0`; web-required recall
  `1.0`; unnecessary web-call rate `0`; calculator false positives `0`.
  Raw per-case output: `agent_routes.json`.
- Calculator regression: 10/10 passed; exactness `1.0`.
- Live Tavily smoke: three allowlisted Tesla investor-relations results returned.
- Provider capability probe: ordinary chat and streaming supported; strict JSON
  and required native functions rejected safely.
- Focused route, resolver, and evaluator tests: 41 passed.
- Full backend suite: 334 discovered tests completed without failures. Explicit
  live PostgreSQL/Qdrant integration cases remain skipped when their test
  endpoints are not configured.

## Remaining failures

None for the Phase 2 gate. Phase 3 must freeze the current candidate from a
clean worktree before any baseline evaluation proceeds.

## Follow-up capability correction — 5 September 2026

The original probe bypassed AVA's production provider adapter. GPT-5.1 rejects
the legacy Chat Completions `max_tokens` argument; AVA retries with
`max_completion_tokens`. The corrected probe therefore records GPT-5.1 ordinary
chat, streaming, and required native-function support in
`provider_capabilities_gpt51.json`. Strict JSON remains unsupported. GPT-5
remains unavailable through this gateway. AVA's bounded Python tool executor is
unchanged; native function calling is not enabled as a result of this probe.
