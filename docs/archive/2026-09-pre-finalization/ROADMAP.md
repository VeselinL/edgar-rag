> **Archived 4 September 2026.** This is a read-only historical record, not a
> current plan or authority. See [`FINALIZATION.md`](../../../FINALIZATION.md) for the
> sole remaining-work plan and release gates.

# AVA — Roadmap Index

**Status date:** 1 September 2026

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
4. **P0:** retrieve 10 candidates independently per requested company and pack
   an even allocation under hard limits of 10 per company and 50 per request;
5. **P0:** complete structured observability and generation/citation evaluation;
6. **P1:** migrate retrieval to Qdrant through a measured parity and rollback
   process;
7. **P1:** ingest, retrieve, validate, and display useful filing images;
8. **P1:** add persistent transcripts, bounded short-term context, and isolated
   opt-in long-term memory;
9. **P1:** complete production, security, accessibility, recovery, CI, and load
   gates;
10. **P1:** add bounded intent/evidence routing, calculator and web-search tools,
    conversation-scoped document sources, and the left conversation workspace;
11. **P2:** add reranking and other retrieval/generation changes only in response
    to measured failures;
12. **P3:** consider explicitly deferred polish only after the core product is
    evaluated and monitored.

The owner-confirmed evidence rules are 10 retrieval candidates per explicitly
requested company, a hard final limit of 10 chunks per company, and a hard
50-chunk request limit. One through five requested companies target 10 each;
larger scopes divide 50 slots as evenly as possible.

## Current baseline

The repository uses deterministic exact/fuzzy plus validated planner company
resolution, independently filtered 10-candidate company/subquery pools, typed
10-per-company and 50-per-request evidence caps, generation-token-aware packing,
Qdrant-shadowed NPZ dense vectors plus in-memory BM25/custom RRF, cited-only
source display, text/table sources, and an optional PostgreSQL-backed
single-user conversation path with bounded short-term context and isolated
opt-in Qdrant summary memory. The explicit stateless path remains available.

Phases 0–5, 7, and the owner-authorized non-image scope of Phase 8 are
implemented on `ava-p0-completion`. Phase 6 is explicitly skipped. Phase 9 is
now active: route greetings and unrelated requests before filing retrieval,
accept company/product descriptions without requiring a ticker, add mandatory
deterministic calculation for arithmetic, integrate bounded cited web search,
add chat-owned PDF/text sources, hide raw citation IDs in rendered answers, and
move history/memory/chat actions into the left conversation workspace.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the audited file-level
evidence and all release gates.
