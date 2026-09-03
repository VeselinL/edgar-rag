# AVA — Semi-Final Internship Project Report

- **Project:** AVA (Autonomous Vehicle Analyst)
- **Project type:** Retrieval-Augmented Generation over SEC annual filings
- **Role perspective:** Junior Machine Learning Engineer
- **Report status:** Semi-final engineering report
- **Repository state reviewed:** `ava-p0-completion`, 2 September 2026

## Executive summary

The objective of this internship project was to build a browser-based assistant
that answers questions from annual SEC filings and makes the supporting filing
evidence inspectable. The work began as a document-ingestion and retrieval
exercise and grew into a complete RAG application with reproducible data
artifacts, structured table handling, hybrid retrieval, grounded generation,
citations, persistent conversations, bounded memory, deterministic tools,
document uploads, observability, and a containerized deployment path.

The current application processes the latest frozen normal 10-K filings for 11
companies: Aptiv, Aurora, Ford, General Motors, Alphabet, Mobileye, NVIDIA,
Ouster, Qualcomm, Rivian, and Tesla. The original internship scope contained 10
companies; Rivian was added to the runtime corpus on 21 August 2026. The active
corpus contains:

- 12,602 structured document blocks;
- 1,005 physical HTML table fragments;
- 978 logical tables, of which 965 are retrieval chunks and 13 navigation
  tables are retained as processed evidence but intentionally not indexed;
- 4,526 retrieval chunks: 3,561 narrative chunks and 965 complete table chunks;
- 4,526 aligned, normalized, 768-dimensional BGE-base v1.5 embeddings.

The most important result was not a single model score. It was the construction
of a traceable evidence chain:

```text
SEC submissions API
  -> immutable filing HTML and metadata
  -> ordered structured blocks and logical tables
  -> provenance-preserving chunks
  -> versioned embeddings
  -> dense + BM25 retrieval and reciprocal-rank fusion
  -> company-balanced, token-aware evidence selection
  -> grounded model answer
  -> exact citation validation
  -> user-visible source cards
```

On the shared 300-question retrieval benchmark, the latest recorded Recall@10
increased from 0.5472 for MiniLM dense retrieval to 0.7072 for BGE-base dense
retrieval, 0.8117 for BGE/BM25 reciprocal-rank fusion, and 0.8411 for the
scope-aware hybrid path. MRR@10 increased from 0.3171 to 0.6157 over the same
sequence. On the later five-case P0 evidence-chain gate, the initial fixed
10-chunk selector had perfect candidate recall but only 0.5887 final recall. The
current company-balanced policy reaches 1.0 candidate recall, 1.0 final recall,
1.0 quota satisfaction, and 1.0 source-display exactness on that frozen set.
This improvement costs additional retrieval and context work: median retrieval
latency in the small P0 gate increased from about 530 ms to about 1,345 ms, and
the mean final generation input grew to 11,817 tokens.

The current system is deliberately bounded. “Agentic RAG” means a typed router
that selects a filing, upload, web, calculator, conversation-only, or
clarification path. It is not an autonomous agent and cannot execute arbitrary
code or external actions. This boundary was added after the assistant answered
an irrelevant sliding-window programming question and invented a six-CEO scope.
The resulting scope gate passes 20/20 cases and executes no retrieval, tool, or
generation call for blocked tasks.

Phase 6, filing-image ingestion and retrieval, was explicitly skipped by the
project owner. Web search is implemented behind a provider-neutral Brave Search
adapter, but live search is disabled until a backend API key is configured and
validated. The current LLM gateway returns buffered JSON instead of genuine
token streaming, so AVA truthfully sends one completed SSE `delta` rather than
simulating token-by-token output.

## 1. Project definition and engineering constraints

The original goal was to answer questions from SEC 10-K filings for a fixed set
of public companies. A useful answer needed more than plausible language. It had
to satisfy four conditions:

1. The filing had to be acquired reproducibly from the SEC.
2. The relevant text or table had to survive preprocessing and chunking.
3. Retrieval had to place the required evidence in the model context.
4. The generated claim had to be grounded in, and traceable to, that evidence.

This changed how I approached the work. A retrieval failure could not be fixed
only by editing a prompt, and a fluent answer could not be considered correct if
its source card was unrelated. The project therefore evaluates acquisition,
parsing, retrieval, generation, and citation display as separate stages.

The main constraints were:

- use SEC-hosted filing HTML and select the latest normal `10-K`, not `10-K/A`;
- never modify or silently overwrite downloaded raw filings;
- preserve sections, paragraphs, lists, tables, document order, and filing
  metadata instead of flattening the HTML;
- preserve complete logical tables even when they exceed the narrative chunk
  limit;
- keep internal chunk identifiers for validation but avoid presenting them as
  the primary user-facing citation;
- keep company scope, retrieval, memory, ownership, and tool decisions on the
  backend;
- evaluate retrieval independently from answer generation;
- avoid Graph RAG, unrestricted agents, arbitrary code tools, trading actions,
  and unmeasured “only-if-time” additions.

The root [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) became the canonical
plan when the application work expanded beyond the original notebooks. This
report uses current manifests, saved evaluation runs, code, and the chronological
[progress report](src/frontend/PROGRESS_REPORT.md) as evidence. Older documents
are treated as historical snapshots when they conflict with current artifacts.

### 1.1 Project phase map

The implementation plan's numbered phases describe the later completion work,
while acquisition, parsing, chunking, embedding, and initial retrieval predate
that numbering. Their current status is:

| Stage/phase | Result at this report cut |
|---|---|
| Data foundation | SEC acquisition, structured parsing, table-schema-v2, chunk-schema-v3, BGE artifacts, and retrieval benchmarks complete |
| Phase 0 | P0 resolver, multi-company evidence, citation, image-inventory, and future-history baselines frozen |
| Phase 1 | cited-only sources, CEO/COO correction, and AVA asset repairs complete |
| Phase 2 | exact/fuzzy/planner-assisted company resolution complete |
| Phase 3 | independent company pools and token-aware balanced evidence complete; policy later fixed at 10/company and 50/request |
| Phase 4 | request tracing and separate generation/citation evaluation complete |
| Phase 5 | Qdrant import, audit, parity, snapshot, restore, alias cutover, and rollback complete |
| Phase 6 | explicitly skipped by owner; no image feature claimed |
| Phase 7 | PostgreSQL history, bounded short-term context, opt-in Qdrant memory, identity, deletion, and recovery foundation complete |
| Phase 8 | non-image production hardening and CI release gates complete |
| Phase 9 | bounded router, calculator/web boundaries, uploads, Sources view, and left conversation sidebar complete |
| Phase 10 | measured improvements in progress; one prompt rejected, routing/calculator and task-scope fixes promoted |
| Phase 11 | deferred polish; not started |

## 2. Scope evolution and current corpus

The initial approved corpus contained 10 companies. The current runtime registry
in `src/filings/corpus.py` contains 11 because Rivian was later integrated with
the same block, chunk, embedding, and retrieval contracts. This is a scope
evolution worth recording rather than hiding: the original ten-company release
remains a reproducible milestone, while current runtime metrics and deployment
checks use 4,526 chunks across eleven filings.

| Ticker | Company | Reporting period | Filed | Blocks | Chunks | Narrative | Table chunks |
|---|---|---:|---:|---:|---:|---:|---:|
| APTV | Aptiv PLC | 2025-12-31 | 2026-02-06 | 1,367 | 528 | 394 | 134 |
| AUR | Aurora Innovation, Inc. | 2025-12-31 | 2026-02-11 | 909 | 261 | 227 | 34 |
| F | Ford Motor Company | 2025-12-31 | 2026-02-11 | 1,713 | 576 | 444 | 132 |
| GM | General Motors Company | 2025-12-31 | 2026-01-27 | 1,066 | 410 | 286 | 124 |
| GOOGL | Alphabet Inc. | 2025-12-31 | 2026-02-05 | 1,260 | 512 | 345 | 167 |
| MBLY | Mobileye Global Inc. | 2025-12-27 | 2026-02-12 | 1,258 | 462 | 412 | 50 |
| NVDA | NVIDIA Corporation | 2026-01-25 | 2026-02-25 | 980 | 329 | 273 | 56 |
| OUST | Ouster, Inc. | 2025-12-31 | 2026-03-02 | 1,112 | 375 | 315 | 60 |
| QCOM | QUALCOMM Incorporated | 2025-09-28 | 2025-11-05 | 830 | 321 | 267 | 54 |
| RIVN | Rivian Automotive, Inc. | 2025-12-31 | 2026-02-12 | 1,162 | 411 | 323 | 88 |
| TSLA | Tesla, Inc. | 2025-12-31 | 2026-01-29 | 945 | 341 | 275 | 66 |
| **Total** |  |  |  | **12,602** | **4,526** | **3,561** | **965** |

Each metadata record includes the company, ticker, zero-padded CIK, form,
filing date, reporting period, accession number, and exact SEC source URL. This
is important because “latest” changes over time. A downloaded filing is a frozen
input, not a moving API result.

## 3. Filing acquisition

The acquisition code first calls the SEC company-submissions endpoint using the
configured CIK, searches the recent filing list for the first exact `10-K`, and
then downloads the filing's primary HTML document from the SEC archive. The
request requires a descriptive `SEC_USER_AGENT`, includes 30-second network
timeouts, and uses the correct SEC hosts for submissions and filing content.

The raw output layout is:

```text
data/raw/TICKER/YEAR-10-K.html
data/raw/TICKER/YEAR-10-K.metadata.json
```

Acquisition and preprocessing are separate commands. Existing raw HTML or
metadata raises `FileExistsError` unless overwrite is explicitly requested.
Later stages read the bytes and validate that ticker, CIK, filename year,
reporting period, and form agree. A file identified as anything other than a
normal `10-K` is rejected during extraction.

The main robustness lesson from acquisition was that reproducibility must start
before machine learning. Without immutable HTML, accession metadata, and hashes,
a later parser or retrieval comparison could accidentally use different source
documents and produce an invalid “improvement.”

## 4. HTML cleaning and structured parsing

### 4.1 Why flattening was rejected

The first tempting approach was to call `get_text()` over the entire filing.
That loses paragraph boundaries, section membership, table cell relationships,
and document order. It also makes it difficult to trace a model claim back to a
specific source element. The adopted parser instead emits ordered JSONL blocks
for headings, paragraphs, list items, and tables.

The DOM cleaning stage removes scripts, styles, `noscript`, hidden Inline XBRL,
viewer/navigation material when safely identifiable, and non-text visual nodes.
Visible Inline XBRL wrappers are unwrapped while preserving their text.
Whitespace normalization handles non-breaking spaces, soft hyphens, zero-width
characters, HTML entities, and repeated whitespace.

Heading recognition cannot rely only on `h1` and `h2`, because SEC filings often
encode headings as styled or bold paragraphs. Section state is therefore
maintained during an ordered, emit-once DOM traversal. Extraction starts at the
actual Item 1 content so cover-page and table-of-contents duplicates do not
become the main business section. Each block receives deterministic identity,
filing metadata, `section`, `section_path`, source tag, source anchor, and source
URL.

### 4.2 SEC table failures

Tables were the hardest ingestion problem. Real filings frequently use bold
`td` elements as headers, split currency symbols and values into separate
physical cells, use empty columns only for alignment, and continue one logical
table across multiple HTML tables. Early idealized tests passed while real
Mobileye tables failed in several concrete ways:

- `Name | Age | Position` was treated as data because the headers were `td`
  rather than `th`;
- a beginning-balance row was promoted into a header;
- years such as 2026–2028 were mistaken for column headings;
- an RSU beginning-balance row displaced the title;
- multi-row debt-investment headers remained in the body;
- a table containing both money and percentages received one incorrect global
  unit;
- large tables were split into row fragments, which made distant rows and totals
  difficult to retrieve together.

The repair introduced table-schema-v2. The parser first records immutable
physical evidence: raw cells, coordinates, row/column spans, formatting,
cleaned-DOM XPath, and HTML fingerprint. It then constructs a separate logical
table with semantic lanes, logical rows, header paths, row roles, title,
document region, per-column units, classifications, continuation links, and
raw-cell mappings. This distinction prevents presentation geometry from being
mistaken for data semantics.

The current processed QA files report 1,005 table fragments and 1,005 valid
Markdown renderings, minimum raw non-empty-cell accounting of 1.0, and zero
standalone marker columns. The corpus has 978 logical tables: 965 included data
or text tables and 13 navigation tables excluded from retrieval. Processed QA
records 183 explicitly tracked normalization fallbacks; the included chunk
scope records 182, so the stage-specific totals should not be merged as if they
were the same population. These fallbacks retain source evidence instead of
silently dropping uncertain layouts.

### 4.3 Parser architecture and auditability

The preprocessing implementation was divided by responsibility:

| Module | Responsibility |
|---|---|
| `filing_io.py` | local file discovery, byte loading, HTML parsing, metadata validation |
| `dom_processing.py` | text cleanup, hidden-content removal, page furniture and heading signals |
| `table_processing.py` | physical grid reconstruction, logical normalization, classification, headers and units |
| `block_extraction.py` | ordered traversal, section state, typed block emission |
| `preprocess_filing.py` | orchestration, validation, atomic JSONL output |
| `audit_tables.py` | read-only corpus, schema, provenance, Markdown and comparison audits |

This split was operationally useful. For example, a header-classification fix
could be tested without changing filing discovery or output serialization.
Atomic writes and release-state guards also prevent readers from observing a
partially promoted corpus.

## 5. Chunking experiments and selected strategy

The first chunking baseline used character counts, including a historical
1,200-character/150-character-overlap configuration. It was replaced because
characters do not correspond consistently to model tokens, especially for
financial tables, abbreviations, and punctuation.

The controlled benchmark compared recursive and fixed token splitting at 128,
192, 250, and 500 tokens on Mobileye and Tesla. All valid configurations kept
100% source coverage and section accuracy. The discriminating metric was
boundary accuracy: the share of narrative chunks ending at sentence
punctuation.

| Filing | Strategy | Size/overlap | Chunks | Median tokens | Boundary accuracy |
|---|---|---:|---:|---:|---:|
| MBLY | recursive | 250/32 | 778 | 164.5 | 76.4% |
| MBLY | fixed | 250/32 | 662 | 250 | 39.2% |
| MBLY | recursive | 500/32 | 464 | 253.5 | **91.7%** |
| MBLY | fixed | 500/32 | 426 | 258 | 62.6% |
| TSLA | recursive | 250/32 | 531 | 176 | 77.8% |
| TSLA | fixed | 250/32 | 474 | 250 | 45.3% |
| TSLA | recursive | 500/32 | 343 | 239 | **88.7%** |
| TSLA | fixed | 500/32 | 330 | 230 | 66.4% |

Recursive 500/32 was selected because it preserved sentence boundaries much
better while producing fewer chunks than the 250-token option. The active
separator order is paragraph, line, sentence-like boundary, word, then
character. Chunks never cross `section_path` boundaries, and the section prefix
counts against the token budget.

The configured overlap is not the same as realized overlap. The current files
record median actual overlap of zero because the recursive splitter usually
finds a natural separator before an overlapping fragment is needed. Recording
both values prevented us from incorrectly claiming that every adjacent chunk
shares 32 tokens.

The 500-token limit applies only to narrative. Every included logical table is
stored intact in one chunk. This preserves row-to-row comparisons and source
integrity, but current table chunks can be much larger: the largest current
retrieval chunk is 1,886 tokenizer tokens. This trade-off later affected the
embedding stage.

## 6. Embedding generation and model selection

The project initially produced MiniLM embeddings because the model is small and
fast enough for a local baseline. BGE-base v1.5 was promoted after retrieval
evaluation showed materially better ranking. The current model contract is:

- repository: `BAAI/bge-base-en-v1.5`;
- revision: `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`;
- output: normalized `float32`, 768 dimensions;
- query prefix: `Represent this sentence for searching relevant passages: `;
- document prefix: empty;
- maximum sequence length: 512 tokens.

Every embedding NPZ is accompanied by manifest schema 3. The manifest records
the source chunk hash, exact model revision, ordered input hashes, embedding
file hash, dimension, dtype, normalization, prefix policy, token lengths, and
truncated inputs. Loading fails if chunk order, count, hashes, shape, finite
values, or vector norms do not match.

Table embedding text is not raw Markdown. It is a deterministic searchable
representation containing effective section/region, title, units, and logical
header/row/value statements. The complete structured table remains in JSONL for
display and citation.

There is an important current limitation. The eleven current BGE manifests
record 322 table embedding inputs above the 512-token model limit, with a
maximum prepared table input of 4,606 tokens. They record zero truncated
narrative inputs. Older release documentation mentioned only 28 reviewed table
truncations; that number belongs to an earlier ten-company snapshot and is not
the current manifest total. Because the full table source remains intact, this
does not destroy source data, but information near the end of a large table may
be absent from its dense vector. BM25, structured table text, and future
measured row/column views are therefore important complements. Splitting the
source table was not used as an unmeasured shortcut.

## 7. Retrieval evaluation and optimization

### 7.1 Evaluation set discipline

Retrieval was evaluated before adding generation. The main corpus benchmark has
300 queries, 30 for each of the original ten companies, and contains direct,
table, and multi-evidence questions. A benchmark audit corrected 51 stale chunk
assignments and two unnecessarily broad gold sets. No query was rewritten or
removed, and 246 valid assignments stayed unchanged. Recording this matters:
an improved score after correcting invalid labels must not be attributed to a
retrieval algorithm alone.

Mobileye also has a versioned 60-question gold-v2 set with 102 evidence links.
Its table-schema-v2 BGE baseline recorded overall Recall@10 0.6167 and MRR@10
0.4446. The preserved historical 34-question subset recorded Recall@10 0.720588:
0.833 for single-narrative questions, 0.625 for single-table questions, and
0.5625 for multi-chunk questions. Review of incomplete cases found the required
evidence present and aligned; the remaining errors were ranking and compact
table-representation limitations rather than parser data loss.

### 7.2 Dense, lexical, hybrid, and scope-aware results

The following are the latest comparable saved 300-query runs. `Recall@10`
measures the mean fraction of gold chunks retrieved in the first ten results;
`complete@10` requires all gold chunks for a query; `MRR@10` rewards placing the
first relevant result high in the list.

| Retrieval path | Recall@10 | Complete@10 | MRR@10 | Mean recorded latency/query |
|---|---:|---:|---:|---:|
| MiniLM dense | 0.5472 | 0.4700 | 0.3171 | 850.2 ms |
| BGE-base dense | 0.7072 | 0.6167 | 0.5168 | 1,162.8 ms |
| BM25 | 0.7517 | 0.6700 | 0.5428 | 1.13 ms |
| BGE + BM25 RRF | 0.8117 | 0.7333 | 0.6041 | 1,297.9 ms |
| Scope-aware BGE + BM25 RRF | **0.8411** | **0.7667** | **0.6157** | 1,265.9 ms |

These timings came from separate local evaluation runs and are diagnostic, not
production SLOs. The quality changes are still informative:

- BGE increased dense Recall@10 by 0.1600 and MRR@10 by 0.1997 over MiniLM.
- Hybrid RRF increased Recall@10 by 0.1044 and MRR@10 by 0.0872 over BGE dense.
- Scope awareness added another 0.0294 Recall@10 and 0.0117 MRR@10 over global
  hybrid fusion.

BM25's strong result showed that SEC filings contain exact names, terminology,
exhibit numbers, and numeric labels that lexical search handles well. Dense
search remained valuable for paraphrases. Reciprocal-rank fusion with `rrf_k=60`
combined rank positions without requiring incomparable dense and BM25 scores to
be calibrated.

### 7.3 Scope detection, planning, and balanced evidence

The initial detector used regex aliases and exact tickers. Its frozen 46-case
baseline scored 60.87% overall, 96.15% on exact cases, and 0% on typo cases; it
also missed the single-letter Ford ticker `F`. The shared resolver now performs
Unicode and punctuation normalization, exact matching, special handling for
`F`, and thresholded Damerau-Levenshtein matching with a winner-margin check.
The structured planner receives these matches as advisory hints and can return
only tickers from the eleven-company allowlist.

The current resolver passes 46/46 cases: 100% exact, typo, scope, and ambiguity
accuracy with a zero false-company rate. Mean local resolution latency is 14.64
ms in the current saved run. Ambiguous or out-of-corpus targets clarify instead
of silently searching the wrong filing.

The next failure occurred after candidate retrieval. The original selector
searched a combined company scope and kept only ten final chunks. Candidate
recall on five P0 cases was 1.0, but mean final recall was 0.5887. Company counts
for two through five-company requests were `6/4`, `5/3/2`, `3/3/2/2`, and
`2/2/2/2/2`. This proved that the earliest failing stage was final evidence
selection, not embedding search.

The repaired path creates an independent 10-candidate dense/BM25/RRF pool for
each relevant company and subquery. It merges by stable chunk ID, preserves all
rank provenance, applies deterministic diversity, and allocates evidence
round-robin. The final policy is a hard maximum of 10 chunks per company and 50
per request:

- one to five explicit companies target ten chunks each;
- larger scopes divide 50 slots as evenly as possible;
- complete chunks are packed under the model token limit without truncation;
- when a target cannot fit, AVA retains a balanced partial set and records the
  unmet quota rather than discarding the whole answer.

During the earlier Phase 3 policy, the three comparable configured cases moved
from 0.7111 to 0.8444 mean final recall, and final MRR moved from 0.7333 to
0.7778. After the policy was finalized at 10-per-company/50-total, the current
five-case P0 gate reaches:

| Metric | Initial P0 baseline | Current path |
|---|---:|---:|
| Resolver accuracy | 0.6087 | 1.0000 |
| Candidate gold recall | 1.0000 | 1.0000 |
| Final gold recall | 0.5887 | 1.0000 |
| Quota satisfaction | not available | 1.0000 |
| Gold evidence survival | not available | 1.0000 |
| Table candidate/final recall | not available | 1.0000 / 1.0000 |
| Source-display exactness | 0.4286 | 1.0000 |
| Retrieval p50 | 529.6 ms | 1,345.2 ms |
| Mean final generation input | not exactly measured | 11,817 tokens |

The result is a better-supported answer path at a measurable latency and context
cost. This is preferable to claiming a free optimization.

### 7.4 Executive-query failure and query formulation

A real multi-company CEO query exposed a narrower retrieval bug. Ford's
authoritative executive table, `F-2025-CHUNK-000123`, was in the corpus but did
not survive final selection, so the model abstained incorrectly. A redundant
`Company scope` suffix had been appended even when the planner query already
contained the exact company. Removing that suffix for canonical queries and
reformatting “who” queries as `Company + expanded executive title + name` moved
the Ford CEO table from rank 8/rejected to rank 1 for the measured query. A live
three-company run then selected 7 Tesla, 9 Mobileye, and 6 Ford chunks, included
the table, named James D. Farley Jr., and resolved eight citations.

This bug showed why query rewriting must be meaning-preserving and measured.
Adding words that look helpful can reduce retrieval quality.

## 8. Qdrant migration

The original local pipeline loaded NPZ vectors, chunk JSONL, and an in-memory
BM25 index in each API process. This was useful as a reproducible oracle but not
ideal for persistent filtered retrieval. Phase 5 added Qdrant without changing
the evaluated lexical or RRF ranking.

The implementation pins Qdrant server 1.18.2 and Python client 1.19.0. Each
filing chunk becomes one point with:

- a deterministic UUID;
- a named 768-dimensional `dense_bge_base_v1_5` vector using dot product;
- complete chunk and filing metadata in the payload;
- content, embedding-input, and artifact hashes;
- indexed ticker, filing, content-type, and artifact-version fields.

The physical collection is content-addressed and accessed through the stable
alias `ava_filing_chunks_current`. The imported collection
`ava_filing_chunks_89d3a5be9e7d7a8e` contains all 4,526 points. The migration
validated source manifests, dimensions, normalization, chunk order, every point
ID, payload hashes, vectors, and a ticker-filtered search before alias
activation. A snapshot was created, restored to a separate collection, audited,
and the alias rollback was exercised without deleting the original collection.

The saved parity gate accepted all 11 dense queries. Nine matched the full
candidate order, all matched the exact top ten, and the minimum candidate-depth
ID overlap was 98%. Three end-to-end hybrid selection cases matched exact final
chunk IDs and order. During rollout, `shadow` mode left local dense results
authoritative while measuring Qdrant. The one-command launcher and production
Compose now use Qdrant as the primary dense backend, while NPZ/BM25 artifacts
remain the reproducibility and rollback oracle. BM25 and custom RRF are still
local; Qdrant-native sparse retrieval was deliberately not introduced during
the storage migration.

## 9. Grounded generation and citations

Generation uses one structured planner followed by one grounded answer call.
The planner determines in-corpus scope, atomic retrieval subqueries, semantic
comparison intent, and operation type. It does not answer the question. The
user's original text is preserved for generation; only internal retrieval
queries may receive validated canonical company context.

The final context contains explicit source IDs and complete selected evidence.
The prompt requires claims to be supported by that context and tells the model
to abstain when the filings do not provide enough evidence. Tables remain
structured through the backend and frontend instead of being reverse-parsed
from model-generated Markdown.

An important source bug existed in the initial API: if no generated citation
resolved, the backend returned every final-context chunk. This made the Sources
panel look authoritative even when the answer had not cited those chunks. Phase
1 changed citation resolution to:

1. parse exact IDs from the completed raw answer;
2. intersect them with final generation evidence;
3. preserve citation order and remove duplicates;
4. reject invented, malformed, or candidate-only IDs;
5. return no source cards when no valid citation resolves.

On seven frozen citation cases, source-display exactness improved from 42.86%
to 100% without changing any retrieval candidate or selected ID. Internal
citation markers are retained for backend audit but removed from visible answer
text only when they match validated evidence. Arbitrary bracketed content is not
stripped.

Generation quality is evaluated separately on seven reviewed categories:
direct fact, table, numerical, calculation, synthesis, comparison, and supported
abstention. Metrics include claim support, completeness, numerical correctness,
abstention, comparison coverage, contradiction, citation precision/recall,
uncited factual claims, source exactness, model, corpus fingerprint, and latency.

The first Phase 10 prompt experiment is a useful negative result. A stricter
absence prompt improved abstention accuracy from 0.8571 to 1.0 and reduced the
judge's unsupported-claim rate from 0.0571 to 0.0. However, it also:

- reduced completeness from 1.0 to 0.875;
- reduced labeled support from 1.0 to 0.875;
- reduced numerical correctness from 1.0 to 0.5;
- reduced citation recall from 1.0 to 0.8571;
- introduced a 0.125 contradiction rate;
- increased the judge uncited-claim rate from 0.0 to 0.09375;
- increased mean latency from 3,282 ms to 4,216 ms (+28.45%);
- increased maximum latency from 5,940 ms to 12,100 ms (+103.70%).

The candidate was rejected. The earlier prompt remains the default, while
`AVA_STRICT_ABSTENTION_PROMPT=true` preserves the experiment for reproduction.
This prevented a narrow metric improvement from being promoted despite broader
regressions.

## 10. FastAPI and React application

FastAPI is a thin adapter around the shared resolver, retriever, evidence policy,
generator, conversation service, document service, and tools. The same core
retrieval path is imported by the API, evaluator, and notebook; notebooks are
not runtime dependencies.

This shared module was itself a robustness repair. At the start of the web work,
the generation notebook contained planning/generation behavior, the scope-aware
evaluation script contained the authoritative aliases and RRF behavior, and a
separate notebook contained a cross-encoder experiment. There was no single
production module that truthfully represented all three. The coherent path was
extracted into normal Python packages and both the API and evaluation entry
points were made to use it. The active path does not silently include the
cross-encoder.

The chat endpoint uses streamed POST responses with SSE events:

```text
delta -> sources -> done
              \-> error on failure
```

The current Unique/OpenAI-compatible gateway was tested with `stream=True` and
`Accept: text/event-stream`, but returned HTTP 201 JSON with zero stream
fragments. Instead of generating fake typing, the runtime exposes
`answer_delivery=buffered` and emits the completed grounded answer as one
`delta`. Native provider streaming remains supported when a provider actually
returns SSE.

The React 19 + TypeScript frontend includes:

- responsive desktop and mobile layouts;
- light and dark themes with supplied AVA assets;
- safe Markdown rendering with raw HTML disabled;
- structured narrative, table, web, and uploaded-document source cards;
- accessible keyboard focus, live status, reduced-motion behavior, and
  horizontally scrollable wide tables;
- an assistant avatar only beside assistant messages;
- explicit waiting, no-reference, clarification, service-error, partial-stream,
  and deleted-conversation states;
- a left history/memory sidebar with New chat, pinned and recent chats, and
  hover/focus/right-click menus for pin, rename, and delete;
- a `+` document-upload control opposite Send and a per-chat Sources drawer.

The source adapter currently validates 959 of 965 table chunks as safe
rectangular frontend tables. Six chunks have no trustworthy logical headers and
are rejected instead of receiving fabricated headers or being reconstructed
from Markdown. A cited answer containing one of these can report an unrenderable
source item without substituting unrelated evidence.

Early frontend dependency pins exposed high-severity Vite/Vitest audit findings.
The dependencies were upgraded to patched releases, after which the audit
reported zero high-severity findings. Manual browser checks caught a white
native scrollbar track in Firefox dark mode and verified real horizontal table
overflow (`scrollWidth` 496 px versus `clientWidth` 384 px, with 112 px of
successful horizontal movement).

## 11. Conversation history and memory

Short-term and long-term memory have different meanings and storage paths.
PostgreSQL is the authoritative transcript database; Qdrant is not used to order
chat messages.

### 11.1 PostgreSQL conversation state

PostgreSQL stores server-owned tenants/users, conversations, ordered user and
assistant messages, summaries, exact source-use records, feedback, pin state,
session state, and deletion audit records. Chat requests carry a server-issued
`conversation_id` and UUID `client_turn_id`.

Turn creation is atomic and idempotent:

- a completed duplicate replays the stored answer and sources;
- an in-progress duplicate returns a conflict;
- an interrupted turn becomes retryable;
- the user message is not duplicated.

Short-term context selects whole completed recent turns under a token budget and
uses a versioned, rebuildable extractive summary for older turns. It is local to
the conversation but server-side rather than browser-only. This design preserves
history across page reloads, makes ordering authoritative, and supports deletion
and ownership checks. It does not mean every stored message is sent to the model.

### 11.2 Opt-in long-term memory

Long-term memory stores only eligible conversation summaries and durable context
in a separate Qdrant collection. Every search and deletion includes tenant and
user filters. Memory is disabled for every new conversation. Enabling it makes
eligible summaries available across that owner's conversations subject to score,
count, and token limits. Disabling it removes that conversation's derived memory
points. Memory is never cited as SEC filing evidence.

The frozen Phase 7 provider-backed evaluation contained nine turns. Query-only
scope accuracy for the two history-dependent turns was 0%; contextual accuracy
was 100%. Standalone accuracy stayed at 100% with zero regressions, all three
topic-switch turns passed, no planner error remained, and all four deletion and
cross-user/cross-conversation state cases passed.

Local development uses an explicitly acknowledged server-owned single-user
identity. The production reference includes OIDC authorization-code/PKCE,
strict token validation, opaque HttpOnly sessions, CSRF protection, and
owner-filtered conversation operations. That code path still requires real
identity-provider configuration and operator validation before a public rollout.

## 12. Bounded routing, tools, and uploaded documents

### 12.1 Why routing was necessary

Before Phase 9, every query could fall into SEC retrieval. A simple `Hello`
therefore received irrelevant Aurora filing text. The solution was not to make
retrieval broader; it was to decide whether retrieval was appropriate before
running it.

The typed route families are:

- `conversation_only`;
- `filing_rag`;
- `uploaded_document_rag`;
- `web_search`;
- `calculator`;
- bounded evidence-plus-calculator combinations;
- `clarify`.

High-confidence greetings, AVA help, arithmetic, and out-of-scope requests are
handled deterministically. Ambiguous cases use one structured model routing
decision. Routes record reason codes, evidence requirements, allowed tools,
company scope, and whether calculation is mandatory, but never chain-of-thought.
The initial Phase 9 gate passed 24/24 route labels with zero unnecessary filing
routes.

### 12.2 Calculator

Calculations use deterministic `Decimal` arithmetic and an allow-listed grammar;
the model is not trusted to calculate and Python `eval` is never used. The
calculator supports direct expressions and bounded wording for percentages,
sums, subtraction, multiplication, division, ratios, differences, growth rates,
and rounding.

For filing-, upload-, or web-derived arithmetic, the model may extract ordered
operands only. The backend validates each numeric value verbatim against cited
evidence, checks units, and then performs the calculation locally. Missing or
ambiguous operands cause clarification or abstention.

The first natural-language regression set exposed that routing worked but the
parser rejected eight of ten common phrasings. Baseline exactness was 2/10. The
bounded normalization repair reached 10/10, with mean execution latency 0.421 ms
and maximum 3.636 ms.

### 12.3 Web search

The web tool is a provider-neutral interface with a Brave Search adapter. It
allows at most ten safe public HTTPS results, a 1 MiB response, 1,000-character
excerpts, no redirects or follow-on page fetching, and preserves title,
canonical URL, publisher domain, retrieval time, and excerpt. Returned content
is untrusted evidence and cannot issue instructions or authorize another tool.

Web search is disabled by default because no backend Brave API key is currently
configured. External/current questions return an explicit unavailable response;
they do not fall back to unrelated filings or unstated model knowledge. An
ordinary question about a resolved corpus company is filing-first. Web is chosen
only when the user explicitly asks for current/latest/live information, news,
market data, online information, or a web search.

This behavior was corrected after `Who is Tesla CEO?` was over-routed to the
disabled web tool. The regression baseline passed 11/13 routes (84.62%) with
984 ms mean latency. The promoted deterministic-first correction passes 13/13
with 15.25 ms mean route latency, while the original 24/24 gate remains exact.

### 12.4 Conversation-scoped document upload

AVA accepts PDF and UTF-8 text files as immutable, attributable chat sources.
Files are capped at 20 MiB, PDFs at 200 pages, extracted content at 200,000
tokens, and each chat at 20 documents/100 MiB. The parser rejects MIME,
extension, or signature mismatches, invalid UTF-8, NUL-bearing text, encrypted
or malformed PDFs, JavaScript, open/launch actions, embedded files, rich media,
and XFA markers.

Bytes are stored with UUID-only names in a private filesystem/volume using
`0700` directories and `0600` files. PostgreSQL owns document metadata and
lifecycle. Derived BGE chunks are stored in a separate
`ava_uploaded_documents_v1` Qdrant collection with mandatory tenant, user, and
conversation filters. Upload points never share the filing or long-term-memory
collection. Failed indexing rolls back metadata and bytes, and deletion cascades
through metadata, vectors, source-use rows, and private files.

Uploaded text is delimited as untrusted quoted evidence. Text resembling a
system prompt, tool command, link instruction, or secret request has no
authority. The Phase 9 acceptance gate reported zero prompt-boundary violations
and zero source owner-scope violations.

### 12.5 Task-scope guard

The assistant later failed in a different way. Given CEO names, it returned an
incorrect letter-frequency map. It then answered a sliding-window programming
question and invented a six-CEO scope. These were not calculation defects; they
were unsupported product tasks.

The new guard rejects programming exercises, arbitrary name/letter/vowel or
encoding transformations, unrelated creative generation, investment advice or
trade execution, external actions, and prompt/secret extraction before company
resolution can trigger retrieval. The pipeline returns a fixed boundary message
and runs no filing retrieval, web search, upload search, calculator, source
display, or answer-generation call.

The saved candidate passes 20/20 scope cases with zero unnecessary filing routes
and zero errors. Nineteen cases were deterministic; only the preserved external
factual boundary used the model. The original 24/24 route gate and later 13/13
routing regression gate both remain exact.

## 13. Observability and failure diagnosis

Every real request emits one backend-only schema-v1 completion record. It
contains an opaque request ID, corpus/index version, original query, resolution,
subqueries, dense/BM25/RRF candidate provenance, quota decisions, token counts,
final evidence, generated/resolved/rejected citations, source status, memory and
tool references, stage latency, complete latency, cancellation, safe error class,
and provider usage when available. The browser receives only `X-Request-ID`.
Prompts, raw provider errors, scores, secrets, and stack traces are not exposed.

One measured real buffered request started from a process with 4,844 ms startup
time and 690 MB observed RSS. Corpus loading took 1,050 ms, BM25 construction
585 ms, and model loading 3,174 ms. The request completed in 3,384 ms: 1,523 ms
planning, 629 ms retrieval/selection, 1,204 ms generation, and sub-millisecond
citation/source adaptation. The gateway returned no token-usage metadata, so the
trace left usage empty instead of fabricating a cost estimate.

The debugging rule used throughout the project was to identify the earliest
failing stage:

```text
company resolved?
  -> evidence present in frozen filing?
  -> evidence preserved in blocks/table schema?
  -> represented in chunks and vectors?
  -> entered the correct candidate pool?
  -> survived diversity, quota and token packing?
  -> supplied to generation?
  -> used and cited correctly?
  -> exactly that source displayed?
```

This rule prevented generation prompts from being used to hide retrieval or
parsing defects.

## 14. Production hardening and deployment

Phase 8 converted the local vertical slice into a reproducible self-hosted
reference deployment. The production topology contains:

- PostgreSQL 18.1 for authoritative identity/conversation state;
- Qdrant 1.18.2 for filing, memory, and upload vector collections;
- one non-root FastAPI/Uvicorn worker, because each worker owns significant
  model and retrieval memory;
- a non-root Nginx frontend that proxies `/api` without SSE buffering;
- private internal database/vector networking, with only the frontend bound to
  loopback for an external TLS edge;
- persistent volumes `ava_postgres_data`, `ava_qdrant_data`,
  `ava_model_cache`, and `ava_upload_data`.

The API and frontend images are pinned and reproducible. The API runs as UID/GID
10001, has separate liveness/readiness probes, graceful shutdown, request IDs,
JSON logs, body/rate/time limits, provider retry/timeout controls, a circuit
breaker, secure headers, and safe partial-stream behavior. Backup tooling creates
checksummed PostgreSQL and Qdrant state bundles and requires isolated, guarded
restore targets. Retention is dry-run-first and deletes derived memory before
canonical conversation state.

CI run `33488909029` passed backend and frontend gates, live PostgreSQL/Qdrant
contracts, migrations, production image builds, proxied SSE smoke/load tests,
and fixed HIGH/CRITICAL security scans. The local proxy load check completed
10/10 concurrent requests without errors, and the final images had zero fixed
HIGH/CRITICAL findings in the current Trivy database.

`./start_app.sh` starts PostgreSQL, Qdrant, audits/builds and activates the filing
index if necessary, starts the real FastAPI application with short-term history
and opt-in long-term memory, waits for readiness, and starts Vite. Ctrl+C stops
processes started by the script while preserving Docker volumes. The script
also fails clearly on missing `.env`, virtual environment, frontend dependencies,
Docker access, occupied ports, service health, or API readiness.

## 15. Important defects and what they changed

| Observed problem | Earliest failing stage | Repair | Measured or practical effect |
|---|---|---|---|
| Flattened HTML lost structure | preprocessing design | ordered typed blocks with section/source metadata | enabled traceable chunks and section filters |
| SEC `td` headers, split units, spans and continuations corrupted tables | table parsing | physical evidence plus logical table-schema-v2 | 1,005/1,005 valid Markdown tables and full raw-cell accounting |
| Character chunk size did not match model context | chunking | pinned token counter and recursive 500/32 benchmark | MBLY boundary accuracy 91.7% versus 62.6% fixed at 500 |
| Large tables lost global context when row-split | chunking policy | one complete logical table per chunk | preserved full source; exposed BGE truncation limitation explicitly |
| MiniLM semantic ranking was weak | embedding/retrieval | BGE-base v1.5 | Recall@10 0.5472 -> 0.7072 |
| Dense retrieval missed exact SEC terminology | retrieval | BM25 plus RRF | BGE Recall@10 0.7072 -> hybrid 0.8117 |
| Global hybrid ranking ignored company intent | scope/retrieval | scope-aware aliases and filters | hybrid Recall@10 0.8117 -> 0.8411 |
| Typos and ticker `F` were missed | company resolution | normalized exact + bounded fuzzy resolver | 46-case accuracy 60.87% -> 100% |
| Fixed ten-chunk context dropped supported companies | final selection | independent pools and 10/company, 50/request allocation | P0 final recall 0.5887 -> 1.0; p50 latency also rose |
| Intermediate all-company policy selected 60 chunks | evidence policy | replaced five-plus-supplemental rule with hard 50/request and 10/company caps | ten-company requests now target five each; larger scopes cannot exceed 50 |
| No-citation answers displayed all final chunks | citation/source adapter | exact cited-only intersection | source-display exactness 42.86% -> 100% |
| CEO was expanded as Chief Operating Officer | planner/generation prompt | corrected CEO/COO meanings and tests | removed systematic executive-query corruption |
| Provider ignored streaming | provider boundary | explicit buffered mode | truthful one-delta SSE; no fake typing |
| Empty or inconsistent valid planner fields caused failures | planner contract | narrow deterministic normalization of redundant fields | valid scope proceeds; malformed/out-of-corpus plans still fail closed |
| Multiple companies were incorrectly treated as semantic comparison | planner intent | made the single planner authoritative for comparison intent while resolver remains a ticker safety boundary | independent CEO facts no longer conflict with `comparison=false` |
| Plural `CEOs` looked like an unknown ticker | resolution | exempted grammatical plurals of known role/domain acronyms | valid multi-company executive request completed |
| Ford CEO evidence was retrieved poorly | retrieval query formulation | exact role/name reformulation and no redundant suffix | authoritative table moved to rank 1 in measured variant |
| `Hello` returned Aurora filing values | pre-retrieval routing | conversation-only greeting route | zero filing retrieval on greeting gate |
| Tesla CEO was over-routed to disabled web | routing | filing-first for resolved corpus companies without current/web cues | route gate 11/13 -> 13/13 |
| Natural calculations reached tool but parser rejected them | calculator parser | bounded natural-language normalization | exactness 2/10 -> 10/10 |
| Uploaded documents could contain instructions | document trust boundary | passive extraction, untrusted delimiters, scoped storage | zero prompt-boundary and ownership violations in Phase 9 gate |
| Assistant answered programming/text games | product scope | deterministic + structured out-of-scope guard | 20/20 scope cases; no blocked-case tool or generation calls |
| Stricter abstention prompt improved one metric but harmed others | generation experiment | rejected candidate and kept rollback flag | avoided regressions in completeness, numeric correctness, citations and latency |
| Vulnerable frontend development pins | dependency/security | patched Vite/Vitest versions | subsequent high-severity audit reported zero findings |
| Startup consumed rate-limit capacity through health polling | deployment proxy | exempted only liveness/readiness probes | readiness checks no longer consume chat request bucket |

## 16. Verification status

The project uses focused gates as well as repository-wide checks. Important saved
results include:

- strict eleven-company table/chunk and embedding audits passed;
- Qdrant parity: 11/11 dense cases and 3/3 final-selection cases accepted;
- company resolution: 46/46;
- Phase 7 history: 100% contextual follow-up/topic-switch scope and 4/4 state
  cases;
- Phase 9 routing: 24/24;
- Phase 9 calculator path coverage: 4/4 direct, filing, web, and uploaded
  evidence paths;
- Phase 9 backend matrix: 159 tests and 43 subtests passed, with the opt-in live
  PostgreSQL case passing separately;
- Phase 9 frontend: lint, 31 tests, TypeScript, and production build passed;
- Phase 10 routing regression: 13/13;
- Phase 10 calculator regression: 10/10;
- Phase 10 scope gate: 20/20;
- production CI: databases, migrations, containers, security scans, proxy SSE,
  and load probe passed.

The repository-wide Phase 9 run passed 289 tests and 213 subtests with three
intentional skips, but retained two known failures that predated that phase:

1. an August embedding test expects table values to be omitted even though the
   later natural-language table embedder intentionally includes them;
2. a Mobileye review compatibility test expects an older baseline SHA-256.

These failures were documented and not modified because the owner instructed
later phases not to rewrite prior unrelated implementation. They should not be
called passing, but they also did not invalidate the focused Phase 9 feature
gates.

## 17. Remaining limitations and next decisions

The application is close to a complete internship deliverable, but it is not
accurate to describe every optional capability as production-approved.

1. **Filing images are not implemented.** Phase 6 was explicitly skipped. The
   frozen HTML contains 35 labeled image nodes—29 information-bearing, five
   logo/page artifacts, and one low-confidence item—but no image bytes,
   vision/OCR evidence, image citation contract, or UI source cards were added.
2. **Live web search is not enabled.** The adapter and controlled tests exist,
   but production requires a backend Brave key and a live provider validation.
3. **Provider delivery is buffered.** Genuine token streaming needs a provider
   endpoint that returns `text/event-stream`; it must not be simulated.
4. **Large-table dense representation is incomplete.** Current BGE manifests
   show 322 truncated table inputs. A future table row/column retrieval view or
   another measured representation should be evaluated without losing the
   complete source object.
5. **Qdrant sparse retrieval is not active.** Local BM25 plus custom RRF remains
   the evaluated lexical baseline. A native sparse migration should have its own
   parity experiment.
6. **Reranking is not promoted.** Cross-encoder code exists as an experiment,
   but current P0 retrieval gates are perfect on their small labeled set. A
   larger saved failure set is needed before accepting its latency cost.
7. **The current evaluation sets are limited.** Perfect results on 5, 13, 20,
   24, or 46 frozen cases prove those contracts, not general correctness for all
   SEC questions. More adversarial and unseen evaluation data is required.
8. **Multi-user deployment needs operator integration.** OIDC and ownership code
   exists, but a real issuer, tenant claim, TLS edge, secrets, retention policy,
   backup schedule, and restore drill must be configured and signed off.
9. **Provider cost is not measured.** The current gateway omits usage and price
   fields. AVA records latency and leaves cost unavailable rather than inventing
   it.
10. **Two legacy consistency tests remain unresolved.** They should be handled
    in a separate, explicitly authorized maintenance change with regenerated
    evidence where necessary.
11. **Six filing tables are not frontend-renderable.** Their logical headers are
    not trustworthy. The current safe behavior is to reject their display rather
    than invent a schema; an upstream table repair is still needed.

Phase 10 should remain evidence-driven. The implementation plan permits
reranking, an evidence selector, query expansion/HyDE, section weighting,
table-focused views, multimodal embeddings, semantic caching, citation
highlighting, and export improvements only after a saved failure demonstrates
the need and a before/after run measures quality, latency, and rollback.

## 18. What I learned as a junior Machine Learning Engineer

The largest lesson was that a RAG system is primarily an evidence system. The
embedding model matters, but it is only one component. In this project:

- table normalization changed what could be embedded at all;
- chunk boundaries changed the gold evidence identities;
- BM25 outperformed dense retrieval on many exact filing terms;
- company scope and final quota allocation determined whether already-retrieved
  evidence survived;
- citation resolution determined whether the UI told the truth about support;
- routing determined whether retrieval should have run in the first place;
- identity and deletion rules determined whether memory was safe to use.

I also learned to preserve negative results. The strict-abstention prompt looked
better on unsupported-claim rate, but its completeness, numerical correctness,
citations, contradictions, and latency were worse. Keeping the rejected run and
feature flag is more useful than reporting only the improved metric.

Another lesson was to distinguish data integrity from model behavior. The
Mobileye misses were inspected through raw filing, blocks, chunks, embeddings,
and ranking. Because the evidence was present and aligned, changing the parser
would have been the wrong fix. Conversely, the Ford CEO failure was not a model
hallucination; the authoritative table never reached final context because of
query formulation and ranking.

Finally, production details affected ML correctness. A database readiness
failure, duplicate client retry, proxy buffering, stale Qdrant alias, cross-user
memory result, or fabricated source fallback can invalidate an answer even if
the language model itself behaves well. Reproducible artifacts, strict
boundaries, and stage-level telemetry made those failures diagnosable.

## 19. Conclusion

AVA progressed from a local SEC HTML experiment into a structured,
evaluation-driven RAG application. The final system preserves immutable filing
inputs, reconstructs difficult SEC tables, uses measured token chunking,
versions its BGE embeddings, combines semantic and lexical retrieval, allocates
evidence fairly across companies, validates exact citations, and exposes only
used sources. It adds a real browser interface, conversation persistence,
bounded memory, deterministic arithmetic, guarded external/document evidence,
and a reproducible Docker deployment.

The work also produced concrete engineering evidence rather than only feature
claims: retrieval comparisons over 300 questions, stage-specific P0 gates,
Qdrant parity, history and isolation tests, route/calculator/scope regressions,
generation experiments with rejection criteria, and production CI/security
checks. The remaining work is clearly bounded: live web/provider deployment,
large-table retrieval experiments, broader unseen evaluation, operator rollout,
and optional image support only if the owner resumes the skipped phase.

## Appendix A — primary evidence and reproduction points

- Canonical implementation state and decisions:
  [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- Current setup and commands: [README.md](README.md)
- Deployment behavior: [DEPLOYMENT.md](DEPLOYMENT.md)
- Chronological application work:
  [src/frontend/PROGRESS_REPORT.md](src/frontend/PROGRESS_REPORT.md)
- Chunk configuration: [data/chunks/chunking-config.json](data/chunks/chunking-config.json)
- Retrieval runs: `data/evaluation/dense_retrieval/`,
  `data/evaluation/bm25_retrieval/`, `data/evaluation/fusion_retrieval/`, and
  `data/evaluation/scope_aware_hybrid_retrieval/`
- Current P0 evidence chain:
  `data/evaluation/ava_p0/v1/runs/phase-10-current-baseline/`
- Qdrant parity: [data/evaluation/qdrant_parity_v1.json](data/evaluation/qdrant_parity_v1.json)
- Phase 7 history gate:
  [data/evaluation/ava_p0/v1/runs/phase-7-conversation-history.json](data/evaluation/ava_p0/v1/runs/phase-7-conversation-history.json)
- Phase 9 acceptance:
  [data/evaluation/ava_p0/v1/runs/phase-9-acceptance.json](data/evaluation/ava_p0/v1/runs/phase-9-acceptance.json)
- Phase 10 strict-prompt decision:
  [data/evaluation/ava_p0/v1/runs/phase-10-strict-absence-decision.json](data/evaluation/ava_p0/v1/runs/phase-10-strict-absence-decision.json)
- Phase 10 routing/calculator decision:
  [data/evaluation/ava_p0/v1/runs/phase-10-routing-calculator-decision.json](data/evaluation/ava_p0/v1/runs/phase-10-routing-calculator-decision.json)
- Phase 10 task-scope decision:
  [data/evaluation/ava_p0/v1/runs/phase-10-scope-guard-decision.json](data/evaluation/ava_p0/v1/runs/phase-10-scope-guard-decision.json)

Core reproducibility commands are documented in the README. The most important
ones are the strict table/embedding audits, the 300-query retrieval evaluators,
the P0 before/after evaluator, the Qdrant parity evaluator, generation-quality
evaluation, conversation-history gate, backend test suite, and frontend lint,
test, type-check, and production build.
