# Chunking Report

## Method

- Splitter package: `langchain-text-splitters==1.1.2`
- Tables use identical row-group chunking in every run.
- Navigation is excluded and chunks never cross `section_path` boundaries.
- `Boundary accuracy` is the share of narrative chunks ending at sentence punctuation.
- Boundary accuracy is not retrieval accuracy; retrieval requires embeddings and labels.
- `Actual overlap` is median source-character overlap between narrative chunks.
- `Coverage` is the share of evidence blocks represented in at least one chunk.

## Mobileye Global Inc.

- Input: `data/processed/MBLY/2025-10-K.blocks.jsonl`
- Filing: 2025 10-K
- Structured blocks: 1258
- Longest source block: 3,949 characters

### Results

| Strategy | Size | Config overlap | Actual overlap | Chunks | Narrative | Tables | Min | Median | P95 | Max | Boundary accuracy | Coverage | Section accuracy | Table context | Time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| recursive | 500 | 50 | 0 | 2526 | 1892 | 634 | 42 | 414 | 499 | 500 | 41.4% | 100.0% | 100.0% | 100.0% | 45.9 ms |
| recursive | 800 | 100 | 0 | 1230 | 1133 | 97 | 42 | 594.5 | 787 | 800 | 61.3% | 100.0% | 100.0% | 100.0% | 29.1 ms |
| recursive | 1200 | 150 | 0 | 842 | 775 | 67 | 42 | 815 | 1176 | 1199 | 75.4% | 100.0% | 100.0% | 100.0% | 21.9 ms |
| recursive | 1600 | 200 | 0 | 667 | 604 | 63 | 42 | 977 | 1570 | 1600 | 82.6% | 100.0% | 100.0% | 100.0% | 20.7 ms |
| fixed | 500 | 50 | 50 | 3408 | 1504 | 1904 | 42 | 499 | 500 | 500 | 15.8% | 100.0% | 100.0% | 100.0% | 37.3 ms |
| fixed | 800 | 100 | 100 | 1045 | 948 | 97 | 42 | 799 | 800 | 800 | 24.8% | 100.0% | 100.0% | 100.0% | 16.7 ms |
| fixed | 1200 | 150 | 150 | 730 | 663 | 67 | 42 | 1199 | 1200 | 1200 | 35.7% | 100.0% | 100.0% | 100.0% | 11.7 ms |
| fixed | 1600 | 200 | 200 | 591 | 528 | 63 | 42 | 1598 | 1600 | 1600 | 43.8% | 100.0% | 100.0% | 100.0% | 10.5 ms |

### Infeasible Configurations

| Strategy | Size | Overlap | Reason |
|---|---:|---:|---|
| recursive | 250 | 25 | Table context exceeds chunk size: MBLY-2025-000633 |
| fixed | 250 | 25 | Table context exceeds chunk size: MBLY-2025-000633 |

## Tesla, Inc.

- Input: `data/processed/TSLA/2025-10-K.blocks.jsonl`
- Filing: 2025 10-K
- Structured blocks: 945
- Longest source block: 3,131 characters

### Results

| Strategy | Size | Config overlap | Actual overlap | Chunks | Narrative | Tables | Min | Median | P95 | Max | Boundary accuracy | Coverage | Section accuracy | Table context | Time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| recursive | 500 | 50 | 0 | 2814 | 1308 | 1506 | 34 | 482 | 499 | 500 | 41.8% | 100.0% | 100.0% | 100.0% | 48.0 ms |
| recursive | 800 | 100 | 0 | 939 | 777 | 162 | 34 | 607 | 788 | 800 | 61.1% | 100.0% | 100.0% | 100.0% | 22.6 ms |
| recursive | 1200 | 150 | 0 | 633 | 524 | 109 | 34 | 807 | 1175 | 1199 | 75.8% | 100.0% | 100.0% | 100.0% | 18.5 ms |
| recursive | 1600 | 200 | 0 | 492 | 402 | 90 | 34 | 988.5 | 1569 | 1600 | 81.6% | 100.0% | 100.0% | 100.0% | 18.1 ms |
| fixed | 500 | 50 | 50 | 4617 | 1016 | 3601 | 34 | 499 | 500 | 500 | 18.0% | 100.0% | 100.0% | 100.0% | 61.7 ms |
| fixed | 800 | 100 | 100 | 800 | 638 | 162 | 34 | 799 | 800 | 800 | 27.7% | 100.0% | 100.0% | 100.0% | 14.6 ms |
| fixed | 1200 | 150 | 150 | 549 | 440 | 109 | 34 | 1185 | 1200 | 1200 | 39.5% | 100.0% | 100.0% | 100.0% | 12.8 ms |
| fixed | 1600 | 200 | 200 | 446 | 356 | 90 | 34 | 1390 | 1600 | 1600 | 49.7% | 100.0% | 100.0% | 100.0% | 13.1 ms |

### Infeasible Configurations

| Strategy | Size | Overlap | Reason |
|---|---:|---:|---|
| recursive | 250 | 25 | Table context exceeds chunk size: TSLA-2025-000359 |
| fixed | 250 | 25 | Table context exceeds chunk size: TSLA-2025-000359 |

## Recursive 1,200 / 150 Comparison

| Filing | Source blocks | Chunks | Median | Min | Max | Boundary accuracy | Actual overlap |
|---|---:|---:|---:|---:|---:|---:|---:|
| MBLY | 1258 | 842 | 815 | 42 | 1199 | 75.4% | 0 |
| TSLA | 945 | 633 | 807 | 34 | 1199 | 75.8% | 0 |

## Interpretation

- Recursive splitting consistently preserves sentence boundaries better than fixed slicing.
- Recursive overlap is not guaranteed; separator-aware runs can have actual overlap 0.
- Fixed splitting produces the configured overlap exactly but often cuts sentences.
- A 250-character maximum is infeasible when repeated table context exceeds the limit.
- The 500-character runs create many small table fragments.
- The current baseline remains recursive splitting with size 1,200 and overlap 150.

## Reproduce

```bash
python -m src.chunking.benchmark_chunking
python -m src.chunking.chunk_documents --overwrite
python -m src.chunking.chunk_documents data/processed/TSLA/2025-10-K.blocks.jsonl --overwrite
```
