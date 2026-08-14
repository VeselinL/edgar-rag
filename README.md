# SEC Filing RAG Assistant

![project banner](banner/banner_text.png)

A Retrieval-Augmented Generation assistant for annual SEC filings. The project
downloads the latest normal 10-K filings, preserves the source HTML, extracts
structured document blocks, and will use those blocks for retrieval and grounded
answers with citations.

## Current status

Implemented:

- SEC submissions lookup and latest normal `10-K` selection
- local HTML and acquisition metadata storage
- HTML cleanup with visible Inline XBRL text preserved
- extraction of Item sections, headings, paragraphs, lists, and tables
- table-schema-v2 extraction with immutable physical DOM evidence and validated
  logical rows, columns, headers, units, titles, regions, and continuations
- deterministic JSONL block output with filing metadata and source anchors
- token-based recursive narrative chunking with source-block provenance
- one complete included logical table per chunk; navigation tables remain in
  processed evidence and produce no chunks
- processed block output for the approved ten-company corpus
- aligned chunk-schema-v3 500-token/32-token output for all ten companies
- dynamically selected local embedding models with normalized vectors and reproducibility manifests
- aligned 768-dimensional BGE-base v1.5 manifest-v3 embeddings for all ten
  promoted chunk files
- versioned Mobileye gold-v2 labels and a post-migration semantic baseline

Not implemented yet:

- persistent vector database and reusable retrieval service
- cross-encoder reranking
- answer generation and citations
- conversation history and chat interface
- generation evaluation

The `table-v2-chunk-v3.20260813-r2` table repair release was promoted on 13
August 2026. Its live audit contains 11,440 blocks, 889 logical tables, and
4,115 chunks (3,238 narrative and 877 table); all ten BGE-base artifacts contain
matching vectors. There are no
normalization collisions, unmapped non-empty cells, standalone marker columns,
unknown tables, invalid table Markdown, or provenance gaps. Persistent indexing,
reranking, and generation remain separate pending work.

See [ARCHITECTURE.md](ARCHITECTURE.md) for module and data contracts and
[ROADMAP.md](ROADMAP.md) for verified progress and current gates.

## Data flow

```text
SEC EDGAR API
  -> frozen filing HTML and metadata
  -> physical table evidence + logical table-schema-v2 blocks
  -> chunk-schema-v3 logical-table chunks
  -> manifest-v3 BGE-base embeddings
  -> vector retrieval
  -> grounded conversational answers
  -> separate retrieval and generation evaluation
```

Raw filing files under `data/raw/` are treated as immutable. Processed outputs are
written separately under `data/processed/`.

## Requirements

- Python 3.12
- `lxml`
- `python-dotenv`
- `langchain-text-splitters`
- `sentence-transformers`

Install dependencies with `.venv/bin/pip install -r requirements.txt`.

On Linux, the requirements select CPU-only PyTorch from the official PyTorch
package index. Replace that requirement intentionally if GPU inference is needed.

Set an SEC-compliant user agent in `.env`:

```dotenv
SEC_USER_AGENT="Application Name contact@example.com"
```

## Usage

Download the latest configured 10-K filings:

```bash
.venv/bin/python -m src.filings.fetch_data
```

Existing filing snapshots are not overwritten by default.

Process an existing filing, for example Mobileye:

```bash
.venv/bin/python -m src.filings.preprocess_filing mobileye
```

Rebuild an existing processed output intentionally:

```bash
.venv/bin/python -m src.filings.preprocess_filing mobileye --overwrite
```

Create retrieval chunks from processed blocks:

```bash
.venv/bin/python -m src.chunking.chunk_documents mobileye --overwrite
```

The active configuration is recursive 500-token narrative chunks, 32-token
configured overlap, and one complete table per table chunk.

Embed the chunks with BGE-base v1.5, the default model:

```bash
.venv/bin/python -m src.embeddings.embed_chunks mobileye --device cpu
```

Select another supported model with `--model-name`:

```bash
.venv/bin/python -m src.embeddings.embed_chunks mobileye --model-name mpnet --device cpu
.venv/bin/python -m src.embeddings.embed_chunks mobileye --model-name bgebase --device cpu
.venv/bin/python -m src.embeddings.embed_chunks mobileye --model-name nomic --device cpu
```

The available names are `minilm`, `mpnet`, `bgebase`, and `nomic`; `bgebase` is
the default. Only the selected model is loaded.

Benchmark one model and update `EMBEDDING_REPORT.md`:

```bash
.venv/bin/python -m src.embeddings.benchmark_embeddings --model-name bgebase --device cpu
```

Each call replaces the existing result for that model and chunk-file version,
or adds a new row. The benchmark records model loading and encoding time,
throughput, query latency, dimensions, memory size, input lengths, and
truncation. Run the command separately for each model so only one is loaded at
a time.

The embedding command writes normalized vectors to a compressed NumPy file and
a JSON manifest containing the source hash, exact model revision, dimensions,
normalization policy, and any inputs truncated by the model.

Run tests:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Run the strict live table and embedding audits:

```bash
.venv/bin/python -m src.filings.audit_tables \
  --processed-directory data/processed --chunks-directory data/chunks \
  --input-manifest data/manifests/table-v2-inputs.json \
  --review-decisions data/manifests/table-v2-manual-review.json \
  --measure-embedding-tokens --strict

.venv/bin/python -m src.embeddings.audit_embeddings \
  --input-manifest data/manifests/table-v2-inputs.json --strict
```

## Data layout

```text
data/
  raw/
    MBLY/
      2025-10-K.html
      2025-10-K.metadata.json
  processed/
    MBLY/
      2025-10-K.blocks.jsonl
      2025-10-K.blocks.qa.json
  chunks/
    MBLY/
      2025-10-K.chunks.jsonl
      2025-10-K.chunks.stats.json
  embeddings/
    MBLY/
      2025-10-K.bgebase.embeddings.npz
      2025-10-K.bgebase.embeddings.manifest.json
  manifests/
    table-v2-release.json
    table-v2-live-validation.json
    table-v2-embedding-release.json
  evaluation/
    mobileye_retrieval_gold_v2.json
    mobileye_bgebase_table_v2_baseline.json
```

Generated `.npz` vectors are ignored by Git; manifests remain available for
reproducibility. A future indexing stage will add `data/indexes/`.

## Evaluation direction

Retrieval and answer generation are evaluated separately. The authoritative
Mobileye gold-v2 file preserves all 60 historical questions and maps 102 evidence
items through source blocks and logical-table identities. The post-migration
BGE-base run records Recall@10 and MRR overall and by evaluation set, category,
and evidence type. The historical 34-question comparison subset is unchanged at
mean Recall@10 0.721 (exactly 0.720588); its single-narrative, single-table, and
multi-chunk values remain 0.833, 0.625, and 0.5625. All incomplete retrievals are
reviewed in `data/evaluation/mobileye_bgebase_table_v2_review.json`.

## Reference

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
