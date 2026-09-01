# Codex Prompt — Continue AVA to Completion

Work on AVA in this repository and implement the remaining roadmap in controlled,
testable phases.

Before changing anything:

1. Read `AGENTS.md` completely and follow it.
2. Read `IMPLEMENTATION_PLAN.md` completely. It is the authority for priorities,
   architecture, invariants, acceptance gates, and open decisions.
3. Read `src/frontend/PROGRESS_REPORT.md`, especially the latest entries, so you
   do not repeat completed work.
4. Inspect the branch, worktree, existing changes, relevant code, live schemas,
   and tests. Preserve all unrelated user work. The existing deletion of
   `CHUNKING_REPORT.md` is unrelated and must not be restored or included.

Start with the next incomplete phase in `IMPLEMENTATION_PLAN.md`. Work in the
documented order and complete one bounded subphase at a time. Phase 6 is skipped
by owner decision; do not resume image work implicitly. Do not combine storage,
ranking, tool, or interface changes in a way that prevents measured attribution.

For each phase:

- verify current behavior with evidence before editing;
- use shared production modules for the API, evaluators, scripts, and notebooks;
- keep FastAPI and React as thin adapters;
- preserve the fixed eleven-company corpus, immutable raw HTML, complete tables,
  stable provenance, and the current NPZ/BM25 path as the Qdrant parity oracle;
- keep provisional evidence budgets and undecided product choices configurable;
- add focused regression and integration tests, then run all repository-relevant
  backend and frontend checks required by `AGENTS.md`;
- compare retrieval and generation/citation quality separately and record IDs,
  company balance, token budgets, latency, and regressions;
- update `IMPLEMENTATION_PLAN.md` only when an accepted decision or contract
  changes, and append the completed work and verification results chronologically
  to `src/frontend/PROGRESS_REPORT.md`;
- review the final diff and include only task-related paths in any commit.

The immediate objective is Phase 9: bounded routing before retrieval, robust
no-ticker company/product resolution, route-aware prompts, deterministic
mandatory calculation, cited bounded web search, conversation-scoped PDF/text
sources with injection/isolation controls, hidden raw citation IDs, and the left
history/memory conversation workspace. Implement and evaluate the subphases in
the canonical order; do not start the only-if-time phase early.

Never show retrieved candidates merely because the answer has no citations.
Never let an LLM invent an out-of-corpus company, silently resolve an ambiguous
mention, starve a requested company, fabricate table/image information, or expose
internal prompts, scores, credentials, stack traces, or raw provider errors.

If an open owner decision in `IMPLEMENTATION_PLAN.md` materially blocks the next
phase, complete all safe unblocked work, present measured options, and ask for the
smallest necessary decision. Otherwise continue autonomously until the current
phase and its release gates are genuinely complete.

At handoff, report the outcome first, then changed files, tests/evaluations run,
measured before/after results, remaining risks, and the exact next phase. Do not
claim completion when a real-provider, migration, recovery, security, or
evaluation gate has only been mocked.
