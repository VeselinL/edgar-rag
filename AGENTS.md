# RAG Assistant Project Instructions

## Project Goal

Build a Retrieval-Augmented Generation (RAG) assistant over annual SEC filings from public companies.

The system must:

- use the SEC EDGAR API to obtain filing HTML;
- obtain the latest `10-K` filing for 10 public companies;
- save the downloaded filings locally;
- preprocess the HTML while preserving useful document structure;
- chunk and embed the processed content;
- store embeddings in a vector database such as ChromaDB or FAISS;
- provide the AVA browser interface through React + TypeScript and FastAPI;
- retrieve relevant filing content and use it to answer questions;
- provide references or citations to retrieved filing content whenever possible;
- show a current-session transcript without persisting or sending prior turns as context in the current frontend phase;
- evaluate retrieval quality separately from generation quality;
- include a small test set built from the extracted documents.

The baseline corpus is fixed to these ten companies: Aptiv, Aurora, Ford,
General Motors, Alphabet, Mobileye, NVIDIA, Ouster, Qualcomm, and Tesla. Do not
expand or replace this corpus without an explicit decision.

## Current Verified State — 2026-08-20

- Raw SEC HTML and metadata exist for all ten companies.
- Structured block JSONL exists for all ten filings.
- All ten filings use the current recursive `500`-token, `32`-token-overlap
  configuration with one complete retained logical table per table chunk.
- The promoted corpus contains 4,115 chunk-schema-v3 chunks, including 462
  Mobileye chunks; Tesla has been regenerated with the current configuration.
- BGE-base v1.5 is the selected baseline embedder. Valid aligned normalized
  768-dimensional vectors and manifest-v3 records exist for all ten filings.
- Corpus-wide dense, BM25, hybrid RRF, and scope-aware retrieval evaluation
  implementations exist. Generation, citation, and reranking experiments exist
  in notebooks but were not yet shared production modules at the start of the
  AVA frontend/API work.
- A persistent vector database, production web API, and UI were not implemented
  at the start of this phase. The current in-memory NPZ/BM25 path is retained for
  the local vertical slice; do not introduce a new vector database here.

## Filing Rules

- Use the latest normal `10-K`.
- Do not use `10-K/A` as the primary filing.
- Treat `10-K/A` as an amendment that may be inspected separately later.
- Use SEC-hosted filing HTML.
- Keep the original downloaded HTML unchanged.
- Do not automatically overwrite previously downloaded filings when a newer filing appears.
- Store filing metadata so results remain reproducible.

Suggested metadata:

```json
{
  "company": "",
  "ticker": "",
  "cik": "",
  "form": "10-K",
  "filing_date": "",
  "reporting_period": "",
  "accession_number": "",
  "source_url": ""
}
```

Separate acquisition from processing. The project should support the equivalent of:

```text
download-latest
process-existing
```

## Recommended Implementation Order

1. Implement SEC EDGAR fetching.
2. Test acquisition and preprocessing on one or two filings.
3. Inspect the extracted document blocks manually.
4. Fix preprocessing before processing all 10 filings.
5. Process the full corpus.
6. Chunk the cleaned content.
7. Generate embeddings.
8. Build the vector index.
9. Implement retrieval.
10. Connect retrieval to the model.
11. Add citations.
12. Add the stateless AVA chat interface.
13. Build the evaluation set.
14. Evaluate retrieval and generation separately.
15. Consider persistent conversation history only in a later explicitly approved phase.
16. Add optional tools only after the baseline RAG system works.

## Raw Data Layout

Use a reproducible layout similar to:

```text
data/
  raw/
    COMPANY/
      YEAR-10-K.html
      metadata.json
  processed/
  chunks/
  indexes/
  evaluation/
```

Do not modify files under `data/raw/`.

## HTML Preprocessing Requirements

Do not flatten the complete filing with a single `get_text()` call.

Preserve document structure before chunking.

### Remove

- `script`;
- `style`;
- `noscript`;
- SEC viewer or navigation content when present;
- hidden Inline XBRL content such as `ix:hidden`;
- repeated page headers, footers, or navigation elements when they can be identified safely.

### Preserve

- visible text inside Inline XBRL tags;
- section headings;
- paragraphs;
- lists;
- tables;
- document order;
- company and filing metadata.

Visible Inline XBRL wrappers may be removed while keeping their visible text.

Normalize whitespace, including non-breaking spaces such as `\xa0`.

Do not rely only on `<h1>` and `<h2>` tags. SEC filings may represent headings through styled paragraphs or other HTML elements.

## Document Structure

Detect and preserve major filing sections where possible, including:

- Item 1 — Business;
- Item 1A — Risk Factors;
- Item 7 — Management’s Discussion and Analysis;
- Item 8 — Financial Statements.

Represent extracted content as structured blocks before chunking.

Example:

```json
{
  "company": "Example Company",
  "ticker": "EXAMPLE",
  "filing_year": 2025,
  "section": "Item 1A — Risk Factors",
  "content_type": "paragraph",
  "text": "..."
}
```

Keep narrative text and tables distinguishable. Do not discard tables.

## Chunking

Chunk only after structured extraction has been inspected.

The selected baseline uses `RecursiveCharacterTextSplitter` with token-based
length measurement. Narrative chunks use 500 tokens and 32 tokens of configured
overlap. Tables are exempt from the narrative size limit and each retained table
must remain complete in one chunk.

Its separator priority is conceptually:

```python
["\n\n", "\n", " ", ""]
```

It attempts paragraph boundaries first, then lines, then words, then characters.

Measure both configured and actual overlap; separator-aware splitting does not
guarantee that the configured overlap is realized.

Preserve useful metadata on every chunk, including:

- company;
- ticker;
- CIK;
- filing year;
- filing date;
- reporting period;
- accession number;
- section;
- content type;
- source URL;
- a source location or block identifier when available.

Do not choose chunk size and overlap without recording them as configuration.

## Retrieval

The baseline retrieval pipeline is:

```text
user query
→ query embedding
→ vector index
→ top-k candidate chunks
→ prompt augmentation
→ model answer
```

A vector index is used to search stored embeddings efficiently. ChromaDB or FAISS may be used.

The initial version may use semantic vector search only.

Later improvements may include:

- lexical search such as BM25;
- hybrid lexical and semantic search;
- metadata filtering;
- reranking;
- query rewriting;
- separate handling for tables.

Do not add these before the baseline works unless an observed failure requires them.
The Mobileye semantic-retrieval baseline and labeled evaluation set now provide
enough evidence for a bounded reranking experiment. Keep that experiment scoped
to Mobileye until it improves the saved baseline without unacceptable regressions.

## Reranking

If reranking is added:

1. preserve the BGE-base-only result as the comparison baseline;
2. retrieve a larger candidate set quickly;
3. score each query–chunk pair with a reranker;
4. reorder candidates;
5. keep only the best few chunks;
6. compare Recall@k and MRR before and after reranking, including separate table,
   narrative, and multi-chunk results.

A cross-encoder reranker processes the query and candidate chunk together and outputs a relevance score.

## RAG and Tools

Use RAG for questions answered from unstructured filing content.

Use function calling or deterministic code for exact operations such as:

- calculations;
- exact filtering;
- structured lookups;
- other external tools added later.

The optional extension may include web search, a calculator, or document lookup, but only after the baseline RAG assistant is complete.

## Prompting and Citations

The answer-generation prompt should provide retrieved filing context and instruct the model to answer from that context.

The assistant should cite or reference the retrieved filing content whenever possible.

Do not claim that information comes from a filing when no supporting chunk was retrieved.

For questions that cannot be answered from the indexed filings, the assistant should state that the available documents do not provide enough evidence.

## Conversation History

The current AVA phase is stateless. The browser may show several messages during
one tab session, but it must not persist them and each backend request receives
only the current query. Persistent history and conversational memory are deferred.

## Observability

Record enough information to diagnose failures:

- user question;
- retrieval query;
- retrieved chunks;
- retrieval scores;
- chunk metadata;
- final context sent to the model;
- generated answer;
- citations;
- latency by stage;
- token usage when available;
- evaluation results.

## Evaluation

Evaluate retrieval and generation separately.

For every failed answer, first determine:

```text
Was the required evidence retrieved?
├─ no  → retrieval, indexing, preprocessing, or chunking problem
└─ yes → generation, prompting, or grounding problem
```

Build a small test set from the extracted filings.

The test set should include different question types discussed for this project:

- direct factual questions;
- cross-section synthesis;
- cross-company comparison;
- numerical questions;
- table questions;
- questions whose answers are absent;
- ambiguous questions;
- follow-up questions.

Choose and document metrics for both:

- retrieval quality;
- generation quality.

Do not report only an overall chatbot score.

## Development Principles

- Build the simplest complete baseline first.
- Inspect intermediate outputs instead of treating LangChain or any other framework as a black box.
- Keep retrieved chunks visible during development.
- Preserve reproducibility through frozen filing snapshots and saved configuration.
- Make one improvement at a time in response to an observed failure.
- Do not discard tables or document hierarchy for implementation convenience.
- Do not add Graph RAG or agentic RAG. Hybrid BGE/BM25 retrieval with RRF is
  now the evaluated baseline. AVA's active generation path follows the current
  main notebook's planned multi-subquery RRF selector without cross-encoder
  reranking. Preserve the separate reranking experiment and saved non-reranked
  baseline for later measured comparison.

## AVA Web Application Rules

### Product identity and assets

- The user-facing product name is **AVA**, which expands to **Autonomous Vehicle Analyst**.
- The internal historical repository name may remain in code and engineering documentation, but it must never appear in the browser interface, page metadata, user-facing errors, or frontend accessibility text.
- `src/frontend/avatar/ava.png` is the canonical supplied AVA avatar.
- `src/frontend/avatar/favicon.png` is the supplied favicon source.
- Do not regenerate, redraw, recolour, crop destructively, or move either supplied image without an explicit request. CSS backing and sizing may be used without altering the source files.
- AVA is a restrained product identity, not a human-like mascot. Do not invent a biography, face, personality, onboarding story, or decorative animation.

### Shared retrieval source of truth

- The deployed API must use the same scope-aware retrieval and generation path demonstrated by `notebooks/hybrid_rag_generation.ipynb` and validated by `src/scripts/evaluate_scope_aware_hybrid_retrieval.py` (`evaluate_scope_aware_retrieval`).
- A notebook file must never be a runtime dependency. Extract the smallest coherent production functions and make the notebook, evaluator, and API import the shared module where practical.
- Preserve the current main-notebook pipeline: LLM atomic-subquery planning; regex company-name, ticker, and alias matching; Comparison Cues; company/global scope decisions; dense/BM25 retrieval; reciprocal-rank fusion; stable-ID merge/deduplication; minimum two available chunks per subquery; the `0.01` multi-subquery bonus; final 10-chunk context selection; grounded prompt; and citation resolution. The cross-encoder experiment is not part of this active generation path.
- The frontend sends the original query unchanged. Company detection, Comparison Cue detection, subquery planning, retrieval scope, evidence allocation, merging, context selection, and citation validation are backend responsibilities.
- Do not change core retrieval behaviour merely to make FastAPI integration easier, and do not create a second scope detector in the API.
- Before connecting or changing the endpoint, compare the shared API/evaluation path on representative queries. Detected companies, comparison status, retrieval scopes, selected-evidence companies, final count, and internal chunk IDs must match before frontend normalization.
- For a multi-company comparison, the planner's company-specific subqueries must each retain at least two available evidence chunks within the 10-chunk final context. Reject a plan whose subquery count makes that invariant impossible rather than silently starving a subquery.

### API and source adaptation

- FastAPI is a thin adapter. Core retrieval and generation modules remain independently usable outside the web application.
- The first API is stateless: accept only the current query and do not accept or infer conversation history.
- Use a streaming `POST` with actual provider streaming. Fake typing, splitting a completed answer, and artificial per-token delays are prohibited.
- Emit structured SSE events named `delta`, `sources`, `done`, and `error` over streamed `fetch`.
- Map pipeline chunks into explicit frontend-safe narrative and structured-table schemas. Keep internal IDs for backend correlation and citation resolution, but never use a raw chunk ID as the primary user-visible source label.
- Prefer explicitly cited final-evidence chunks. If no citation can be resolved, return only the final evidence given to generation and describe it as retrieved evidence.
- Tables remain structured end to end. Use existing logical headers and rows; never make the frontend reconstruct a Markdown table, and never fabricate missing headers, units, values, or labels.
- Never expose API keys, gateway headers, system prompts, retrieval scores, stack traces, or raw provider errors to the browser.

### Frontend requirements

- Light and dark themes are required. The light page background is white; the dark page background is very dark blue rather than black.
- The layout must be responsive and accessible at desktop and mobile widths, with semantic controls, keyboard navigation, visible focus, sufficient contrast, reduced-motion support, and appropriate non-token-spamming live regions.
- Render model Markdown safely without unsanitized HTML.
- Display the AVA avatar beside assistant messages, not user messages. Remove the retrieval/waiting bubble on the first non-empty streamed fragment.
- Current-session messages may remain visible in memory only. Do not add persistence, a history sidebar, authentication, accounts, profiles, uploads, corpus management, admin controls, analytics, model/tool selectors, or agentic behaviour in this phase.

### Implementation discipline

- Verify implementation decisions against repository files and live schemas rather than guessing filenames, APIs, fields, environment variables, or table structure.
- Preserve existing user changes and unrelated work. Inspect the worktree before edits and stage only task-related paths.
- Keep mock mode explicitly separate from real pipeline mode; do not hide a broken real integration behind mock output.
- Run repository-relevant backend tests plus frontend type checking, linting, component tests, and production build before declaring completion.
- Record AVA work chronologically in `src/frontend/PROGRESS_REPORT.md` and make incremental commits on `deploy_front` with messages formatted `feat: implemented/added/ [feature]`.
