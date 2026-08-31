# AVA — Roadmap Index

**Status date:** 31 August 2026

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
10. **P2/P3:** add reranking and other enhancements only in response to measured
    failures.

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

P0 Phases 0–4 and the Phase 5 Qdrant foundation are implemented on
`ava-p0-completion`. The current release gate
includes structured request diagnostics and separate reviewed/reference plus
real-provider generation/citation evaluation. Release sign-off still requires an
accepted threshold or fix for uncited concluding synthesis observed in the live
comparison case. Qdrant is in the parity soak with the local NPZ/BM25 path as
its oracle; native sparse retrieval and primary-mode promotion remain gated.
Phase 7's implementation foundation is present, while live PostgreSQL/Qdrant
deployment, provider-backed follow-up evaluation, multi-user authentication,
and retention/backup sign-off remain release gates.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the audited file-level
evidence and all release gates.
