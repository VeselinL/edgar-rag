# AVA — Roadmap Index

**Status date:** 27 August 2026

The authoritative roadmap, target architecture, priorities, implementation
contracts, acceptance gates, and unresolved decisions are maintained in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Do not duplicate configurable
budgets or future architecture in this file.

## Current phase order

1. **P0:** freeze baselines and add typo, multi-company, citation, image, and
   multi-turn evaluation cases;
2. **P0:** fix source correctness so only exact cited/used chunks are displayed,
   correct CEO/COO prompt expansion, and restore canonical AVA assets;
3. **P0:** add exact, fuzzy, and validated LLM-assisted company resolution;
4. **P0:** retrieve 10 candidates independently per requested company and reserve
   at least five final chunks per company with configurable supplemental slots;
5. **P0:** complete structured observability and generation/citation evaluation;
6. **P1:** migrate retrieval to Qdrant through a measured parity and rollback
   process;
7. **P1:** ingest, retrieve, validate, and display useful filing images;
8. **P1:** add persistent transcripts, bounded short-term context, and isolated
   opt-in long-term memory;
9. **P1:** complete production, security, accessibility, recovery, CI, and load
   gates;
10. **P2/P3:** add reranking and other enhancements only in response to measured
    failures.

The owner-confirmed evidence rules are 10 retrieval candidates per explicitly
requested company and at least five final evidence chunks per company when
sufficient relevant candidates exist. Supplemental totals remain configurable
and are defined only in the canonical plan until the owner makes a final product
decision.

## Current baseline

The repository currently uses regex company detection, a combined explicit-subset
filter, 10 candidates per planner subquery, a fixed 10-chunk generation context,
NPZ dense vectors plus in-memory BM25, cited chunk IDs, a stateless API, and
text/table sources. A no-citation fallback currently displays all final-context
chunks; that is a known correctness bug and the first implementation fix.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the audited file-level
evidence and all release gates.
