# SEC Filing RAG Assistant

![project banner](banner/banner.png)

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
- table classification as `data`, `text`, `navigation`, `list`, or `unknown`
- table header, title, unit, and per-column unit extraction
- deterministic JSONL block output with filing metadata and source anchors
- configurable narrative and table chunking with source-block provenance
- one complete table per table chunk
- local MiniLM embedding pipeline with normalized vectors and reproducibility manifests

Not implemented yet:

- vector database and retrieval
- answer generation and citations
- conversation history and chat interface
- retrieval and generation evaluation

The current company configuration contains ten candidates from the automotive and
autonomous-driving ecosystem. The final corpus should be explicitly approved and
documented before further expansion.

## Data flow

```text
SEC EDGAR API
  -> frozen filing HTML and metadata
  -> structured preprocessing
  -> chunks and embeddings
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

Install dependencies with `pip install -r requirements.txt`.

On Linux, the requirements select CPU-only PyTorch from the official PyTorch
package index. Replace that requirement intentionally if GPU inference is needed.

Set an SEC-compliant user agent in `.env`:

```dotenv
SEC_USER_AGENT="Application Name contact@example.com"
```

## Usage

Download the latest configured 10-K filings:

```bash
python -m src.filings.fetch_data
```

Existing filing snapshots are not overwritten by default.

Process an existing filing, for example Mobileye:

```bash
python -m src.filings.preprocess_filing mobileye
```

Rebuild an existing processed output intentionally:

```bash
python -m src.filings.preprocess_filing mobileye --overwrite
```

Create retrieval chunks:

```bash
python -m src.chunking.chunk_documents --overwrite
```

Embed the chunks with `sentence-transformers/all-MiniLM-L6-v2`:

```bash
python -m src.embeddings.embed_chunks --device cpu
```

The embedding command writes normalized vectors to a compressed NumPy file and
a JSON manifest containing the source hash, exact model revision, dimensions,
normalization policy, and any inputs truncated by the model.

Run tests:

```bash
python -m unittest discover -s tests -v
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
  chunks/
    MBLY/
      2025-10-K.chunks.jsonl
      2025-10-K.chunks.stats.json
  embeddings/
    MBLY/
      2025-10-K.embeddings.npz
      2025-10-K.embeddings.manifest.json
```

Generated `.npz` vectors are ignored by Git; manifests remain available for
reproducibility. Future stages will add `data/indexes/` and `data/evaluation/`.

## Evaluation direction

Retrieval and answer generation will be evaluated separately. The test set will
cover factual, comparative, numerical, table-based, absent-evidence, ambiguous,
and follow-up questions built from the downloaded filings.

## Reference

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
