> **Archived 4 September 2026.** This previously ignored local document is
> preserved as a read-only historical record, not a current plan or authority.
> See [`FINALIZATION.md`](../../../../FINALIZATION.md) for the sole remaining-work plan.
>
# Chunking Report

**Current corpus date:** 21 August 2026

## Current active result

The strategy benchmark below predates logical table schema 2 and is retained as
the evidence for choosing recursive 500/32 narrative splitting. The current
promoted corpus keeps that narrative configuration but composes HTML fragments
into logical tables and excludes true navigation tables.

| Filing | Blocks | Chunks | Narrative | Logical-table chunks | Median tokens | P95 | Max | Boundary accuracy | Provenance coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MBLY | 1,258 | 462 | 412 | 50 | 248 | 492 | 988 | 91.7% | 100% |
| RIVN | 1,162 | 411 | 323 | 88 | 250 | 495 | 913 | 90.1% | 100% |
| TSLA | 945 | 341 | 275 | 66 | 220 | 489 | 980 | 88.7% | 100% |

Across all eleven issuers the active corpus contains 4,526 chunks: 3,561 narrative and
965 table. Every included logical table produces exactly one chunk; 13
navigation logical tables produce none. Source-block and source-anchor coverage
is 100% under the explicit non-heading/non-navigation scope.

## Historical strategy benchmark

## Method

- Splitter package: `langchain-text-splitters==1.1.2`
- Tokenizer: `sentence-transformers/all-MiniLM-L6-v2`
- Every retained physical table stayed complete in one table chunk in these
  historical benchmark runs. The current contract is one included logical table
  per chunk and may compose multiple physical fragments.
- Size and overlap are measured in tokenizer tokens.
- The configured size limit applies to narrative chunks, not complete table chunks.
- Navigation is excluded and chunks never cross `section_path` boundaries.
- `Boundary accuracy` is the share of narrative chunks ending at sentence punctuation.
- Boundary accuracy is not retrieval accuracy; retrieval requires embeddings and labels.
- `Actual overlap` is median source-token overlap between narrative chunks.
- `Coverage` is the share of evidence blocks represented in at least one chunk.

## Mobileye Global Inc.

- Input: `data/processed/MBLY/2025-10-K.blocks.jsonl`
- Filing: 2025 10-K
- Structured blocks: 1258
- Longest source block: 947 tokens

### Results

| Strategy | Size (tokens) | Config overlap (tokens) | Actual overlap (tokens) | Chunks | Narrative | Tables | Min tokens | Median tokens | P95 tokens | Max tokens | Boundary accuracy | Coverage | Section accuracy | Table context | Time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| recursive | 128 | 16 | 0 | 1403 | 1351 | 52 | 10 | 94 | 128 | 978 | 53.7% | 100.0% | 100.0% | 100.0% | 3453.9 ms |
| recursive | 192 | 24 | 0 | 949 | 897 | 52 | 10 | 141 | 191 | 978 | 69.6% | 100.0% | 100.0% | 100.0% | 1825.0 ms |
| recursive | 250 | 32 | 0 | 778 | 726 | 52 | 10 | 164.5 | 247 | 978 | 76.4% | 100.0% | 100.0% | 100.0% | 1735.4 ms |
| recursive | 500 | 32 | 0 | 464 | 412 | 52 | 10 | 253.5 | 494 | 978 | 91.7% | 100.0% | 100.0% | 100.0% | 1477.4 ms |
| fixed | 128 | 16 | 16 | 1169 | 1117 | 52 | 10 | 128 | 128 | 978 | 22.3% | 100.0% | 100.0% | 100.0% | 1401.4 ms |
| fixed | 192 | 24 | 24 | 809 | 757 | 52 | 10 | 192 | 192 | 978 | 32.2% | 100.0% | 100.0% | 100.0% | 1354.5 ms |
| fixed | 250 | 32 | 32 | 662 | 610 | 52 | 10 | 250 | 250 | 978 | 39.2% | 100.0% | 100.0% | 100.0% | 1320.2 ms |
| fixed | 500 | 32 | 32 | 426 | 374 | 52 | 10 | 258 | 500 | 978 | 62.6% | 100.0% | 100.0% | 100.0% | 1238.4 ms |

## Tesla, Inc.

- Input: `data/processed/TSLA/2025-10-K.blocks.jsonl`
- Filing: 2025 10-K
- Structured blocks: 945
- Longest source block: 897 tokens

### Results

| Strategy | Size (tokens) | Config overlap (tokens) | Actual overlap (tokens) | Chunks | Narrative | Tables | Min tokens | Median tokens | P95 tokens | Max tokens | Boundary accuracy | Coverage | Section accuracy | Table context | Time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| recursive | 128 | 16 | 0 | 968 | 900 | 68 | 10 | 95 | 187 | 1491 | 56.0% | 100.0% | 100.0% | 100.0% | 1299.9 ms |
| recursive | 192 | 24 | 0 | 661 | 593 | 68 | 10 | 140 | 253 | 1491 | 72.3% | 100.0% | 100.0% | 100.0% | 1129.7 ms |
| recursive | 250 | 32 | 0 | 531 | 463 | 68 | 10 | 176 | 309 | 1491 | 77.8% | 100.0% | 100.0% | 100.0% | 1052.1 ms |
| recursive | 500 | 32 | 0 | 343 | 275 | 68 | 10 | 239 | 503 | 1491 | 88.7% | 100.0% | 100.0% | 100.0% | 959.7 ms |
| fixed | 128 | 16 | 16 | 791 | 723 | 68 | 10 | 128 | 213 | 1491 | 26.3% | 100.0% | 100.0% | 100.0% | 925.6 ms |
| fixed | 192 | 24 | 24 | 565 | 497 | 68 | 10 | 192 | 295 | 1491 | 37.2% | 100.0% | 100.0% | 100.0% | 891.0 ms |
| fixed | 250 | 32 | 32 | 474 | 406 | 68 | 10 | 250 | 363 | 1491 | 45.3% | 100.0% | 100.0% | 100.0% | 875.9 ms |
| fixed | 500 | 32 | 32 | 330 | 262 | 68 | 10 | 230 | 503 | 1491 | 66.4% | 100.0% | 100.0% | 100.0% | 820.1 ms |

## Selected Recursive 500 / 32 Comparison

| Filing | Source blocks | Chunks | Median | Min | Max | Boundary accuracy | Actual overlap |
|---|---:|---:|---:|---:|---:|---:|---:|
| MBLY | 1258 | 464 | 253.5 | 10 | 978 | 91.7% | 0 |
| TSLA | 945 | 343 | 239 | 10 | 1491 | 88.7% | 0 |

## Interpretation

- Recursive splitting consistently preserves sentence boundaries better than fixed slicing.
- Recursive overlap is not guaranteed; separator-aware runs can have actual overlap 0.
- Fixed token splitting produces the configured overlap exactly but often cuts sentences.
- Complete table chunks may exceed the configured narrative chunk size by design.
- The selected baseline is recursive splitting with a 500-token narrative limit and 32-token configured overlap.

## Reproduce

```bash
.venv/bin/python -m src.chunking.benchmark_chunking
.venv/bin/python -m src.chunking.chunk_documents mobileye --overwrite
.venv/bin/python -m src.chunking.chunk_documents tesla --overwrite
```
> **Archived 4 September 2026.** This previously ignored local document is\n> preserved as a read-only historical record, not a current plan or authority.\n> See [\`FINALIZATION.md\`](../../../../FINALIZATION.md) for the sole remaining-work plan.\n>\n# Chunking Report
