> **Archived 4 September 2026.** This previously ignored local document is
> preserved as a read-only historical record, not a current plan or authority.
> See [`FINALIZATION.md`](../../../../FINALIZATION.md) for the sole remaining-work plan.
>
# EDGAR Insight RAG — Architecture

**Status date:** 21 August 2026

## System boundary

The project builds a RAG assistant over a fixed corpus of eleven SEC 10-K filings. Acquisition, cleaning, chunking, embeddings, retrieval, reranking, and generation are separate stages. Every generated artifact must identify the exact artifact from the previous stage so failures can be traced without rerunning the whole system.

## Current data flow

```text
SEC submissions API + SEC filing HTML
                  ↓
data/raw/TICKER/YEAR-10-K.html
data/raw/TICKER/YEAR-10-K.metadata.json
                  ↓
DOM cleanup, physical table capture, and logical normalization
                  ↓
data/processed/TICKER/YEAR-10-K.blocks.jsonl
                  ↓
recursive 500-token narrative chunking
+ one complete included logical table per table chunk
                  ↓
data/chunks/TICKER/YEAR-10-K.chunks.jsonl
data/chunks/TICKER/YEAR-10-K.chunks.stats.json
                  ↓
BGE-base v1.5 document encoding
                  ↓
data/embeddings/TICKER/YEAR-10-K.bgebase.embeddings.npz
data/embeddings/TICKER/YEAR-10-K.bgebase.embeddings.manifest.json
                  ↓
planned: vector index → top-k retrieval → optional reranking
                  ↓
planned: grounded generation → citations → conversation UI
```

Raw files are immutable. Rebuilding a later stage never changes an earlier-stage artifact.

## Source modules

### Filing acquisition and preprocessing

| Module | Responsibility |
|---|---|
| `src/filings/fetch_data.py` | Company configuration, SEC submissions lookup, normal 10-K selection, HTML and metadata acquisition. |
| `src/filings/filing_io.py` | Local filing discovery, byte-level HTML loading, parsing, and metadata reconstruction/validation. |
| `src/filings/dom_processing.py` | Text normalization, hidden/non-text removal, Inline XBRL unwrapping, page-furniture rules, and heading signals. |
| `src/filings/table_processing.py` | Table grid reconstruction, classification, titles, headers, units, and table rendering. |
| `src/filings/block_extraction.py` | Ordered emit-once DOM traversal, section state, and semantic block creation. |
| `src/filings/preprocess_filing.py` | Pipeline orchestration, block validation, and atomic processed JSONL writes. |
| `src/filings/audit_tables.py` | Read-only corpus/schema/provenance/Markdown/comparison audit. |
| `src/filings/promote_table_release.py` | Audited aligned release promotion, rollback inventory, hashes, and migration marker. |
| `src/filings/release_state.py` | Fail-closed guard against live reads during a file-level promotion. |

`preprocess_filing.py` contains orchestration rather than the complete implementation. This keeps table heuristics, DOM rules, source I/O, and block emission independently testable without introducing a framework or service layer.

### Chunking

| Module | Responsibility |
|---|---|
| `src/chunking/chunk_documents.py` | Configuration validation, tokenizer loading, recursive/fixed splitting, table chunk construction, provenance, validation, statistics, and JSONL writes. |
| `src/chunking/benchmark_chunking.py` | Reproducible comparison of chunk sizes and recursive versus fixed boundaries. |

The active configuration is stored in `data/chunks/chunking-config.json`:

- recursive separator-aware splitting;
- 500-token narrative limit;
- 32-token configured overlap;
- pinned tokenizer and revision;
- no crossing of `section_path` boundaries;
- navigation excluded;
- one complete included non-navigation logical table per chunk.

The section path is part of narrative text and counts toward the 500-token budget. Complete tables may exceed that limit. Each narrative chunk records source character and token spans; every chunk records its contributing `block_ids` and filing metadata.

### Embeddings

| Module | Responsibility |
|---|---|
| `src/embeddings/embed_chunks.py` | Dynamic model selection, model-specific prefixes, embedding-text construction, normalized encoding, integrity validation, NPZ persistence, and manifests. |
| `src/embeddings/benchmark_embeddings.py` | Single-model runtime, throughput, query-latency, capacity, and truncation benchmarking. |
| `src/embeddings/audit_embeddings.py` | Manifest-driven corpus vector integrity audit and release summary. |
| `src/evaluation/migrate_mobileye_gold.py` | Stable-evidence migration from historical Mobileye labels to chunk schema 3. |
| `src/evaluation/evaluate_retrieval.py` | Strict BGE semantic evaluation with Recall@k and MRR. |

BGE-base v1.5 is the selected corpus baseline. Its query prefix is applied only to queries, document vectors are normalized, and cosine similarity can therefore be computed as a dot product. The NPZ file contains:

- `embeddings`: ordered `float32` vectors;
- `chunk_ids`: the exact chunk order represented by those vectors;
- `text_hashes`: one hash for each prepared document input.

The adjacent manifest records source and output hashes, ordered input-text-hash digest, exact model revision, dimensions, prefixes, model limit, truncation, and runtime configuration. Loading code rejects a vector file whose schema, manifest/source hashes, chunk order, input hashes, count, 768-dimensional shape, `float32` dtype, finite values, or normalized vectors do not match.

## Data contracts

### Processed block

A block is the smallest extracted semantic unit. Required common fields include:

```text
block_id, block_index, company, ticker, cik, form,
filing_year, filing_date, reporting_period, accession_number,
section, section_path, content_type, text,
source_tag, source_anchor, page_start, page_end, source_url
```

Table blocks use `table_schema_version: 2`. They preserve immutable raw cells,
spans, source-coordinate and physical-display grids, cleaned-DOM XPath and HTML
fingerprint. Separately, they expose validated logical lanes, row roles, header
paths, per-column units, title/region/classification provenance, continuation
links, raw-cell mappings, ignored-layout reasons, and Markdown rendered only from
logical data. The old `rows` fields remain physical-evidence aliases, never a
silent logical fallback.

### Retrieval chunk

Every chunk contains stable filing metadata, contributing `block_ids`, source anchors, a `source_group`, text, content type, sequential `chunk_id`, and `chunk_index`.

Chunks use `chunk_schema_version: 3`. Narrative chunks retain the unchanged
source character/token span algorithm. Each included non-navigation
`logical_table_id` maps to exactly one complete table chunk and can contain one
or more source HTML fragments composed vertically, horizontally, or as explicit
compound subtables. All contributing block IDs, HTML table IDs, anchors, row and
cell source maps, rowspans, and citation metadata remain attached. A true
`index_navigation` table stays in processed output and maps to zero chunks.

### Embedding input

Narrative chunks use their complete chunk text. Table vectors use a searchable representation made from section path, title, units, headers, and descriptive row labels. The complete table remains in the chunk JSONL and is returned when that vector is retrieved.

## Current artifact state

The promoted `table-v2-chunk-v3.20260813-r2` release covers the original ten
unchanged raw filings. Rivian is an aligned active-corpus extension with the
same table-schema-v2/chunk-schema-v3 contracts. The eleven-company runtime
contains 12,602 processed blocks, 1,005 physical table fragments, 978 logical
tables, and 4,526 chunks: 3,561 narrative plus 965 included logical tables; 13
navigation tables produce no chunk. The strict artifact checks report
zero hard failures, collisions, unmapped values, marker columns, unknown kinds,
invalid Markdown, or source-provenance gaps. All 10,526 narrative blocks and all
3,238 narrative chunks are byte-identical to the pre-repair baseline.

The active BGE-base artifacts contain 4,526 normalized
768-dimensional `float32` vectors and passes all source-hash, order, input-hash,
shape, finite-value, and norm checks. Twenty-eight complete table inputs exceed
512 tokens and are recorded in their manifests; no narrative input is truncated. Mobileye
now has 462 aligned chunks/vectors and a versioned 60-question gold-v2 dataset.

## Future retrieval architecture

Any later bounded reranking experiment remains Mobileye-only:

```text
query
  ↓ BGE-base query encoding
top 10 semantic candidates
  ↓ cross-encoder query–chunk scoring
reranked top 3 or top 4
  ↓ later generation stage
answer context
```

The reranker must not replace first-stage retrieval or mutate stored embeddings. It receives only the query and candidate chunk text, returns a score, and preserves the original semantic score and metadata for diagnostics. The saved table-v2/chunk-v3 BGE-base-only ordering remains the baseline. Reranking is not part of this parser repair and remains paused.

Evaluate reranking with the same gold labels using Recall@k and MRR, separated into narrative, table, and multi-chunk categories. Record candidate count, final count, reranker identity/revision, latency, and scores. A reranker is retained only when it improves final ranking without unacceptable regressions or latency.

## Not implemented

- persistent vector database;
- reusable retrieval service;
- cross-encoder reranker;
- answer generation and citation validation;
- conversation state;
- Streamlit or Gradio interface;
- production request logging and deployment.

These boundaries are intentional. The current local NPZ retrieval path is sufficient for evaluating first-stage retrieval and a Mobileye reranker before database integration.
> **Archived 4 September 2026.** This previously ignored local document is\n> preserved as a read-only historical record, not a current plan or authority.\n> See [\`FINALIZATION.md\`](../../../../FINALIZATION.md) for the sole remaining-work plan.\n>\n# EDGAR Insight RAG — Architecture
