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
- deterministic JSONL block output with filing metadata and source anchors

Not implemented yet:

- chunking and embeddings
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

A dependency declaration still needs to be added.

Set an SEC-compliant user agent in `.env`:

```dotenv
SEC_USER_AGENT="Application Name contact@example.com"
```

## Usage

Download the latest configured 10-K filings:

```bash
python src/fetch_data.py
```

Existing filing snapshots are not overwritten by default.

Process an existing filing, for example Mobileye:

```bash
python src/preprocess_filing.py mobileye
```

Rebuild an existing processed output intentionally:

```bash
python src/preprocess_filing.py mobileye --overwrite
```

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
```

Future stages will add `data/chunks/`, `data/indexes/`, and `data/evaluation/`.

## Evaluation direction

Retrieval and answer generation will be evaluated separately. The test set will
cover factual, comparative, numerical, table-based, absent-evidence, ambiguous,
and follow-up questions built from the downloaded filings.

## Reference

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- See `AGENTS.md` for project constraints and `ROADMAP.md` for the detailed plan.
