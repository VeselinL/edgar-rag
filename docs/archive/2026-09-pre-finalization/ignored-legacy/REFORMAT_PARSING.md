> **Archived 4 September 2026.** This previously ignored local document is
> preserved as a read-only historical record, not a current plan or authority.
> See [`FINALIZATION.md`](../../../../FINALIZATION.md) for the sole remaining-work plan.
>
# SEC Filing Table Parsing Repair Specification

**Status:** implementation ground truth

**Audit date:** 2026-08-13

**Audience:** the Codex agent that will implement the parser repair after explicit user approval

**Scope:** targeted repairs to table extraction, context, rendering, chunking, embedding inputs, QA, and the table-inspection notebook

This document is intentionally prescriptive. It records what was verified in the repository, what must change, what must remain unchanged, the required output contracts, the implementation order, regression fixtures, and acceptance gates. It is not permission to expand the project beyond the current ten-company RAG baseline.

## 1. Executive decision

Do not replace the current parser wholesale. Raw acquisition, DOM cleanup, ordered traversal, rowspan/colspan reconstruction, raw-cell evidence, narrative extraction, token-based narrative chunking, and embedding persistence are useful and must remain.

The blocking defect is narrower: a physical SEC presentation grid is being treated as a semantic data grid. SEC issuers commonly dedicate different physical `<td>` positions to labels, whitespace, currency symbols, amounts, percentage signs, and layout gaps. The parser preserves those positions correctly as source evidence, but every later stage mistakenly treats each physical position as a logical column.

The required repair is:

```text
raw SEC table
  -> preserved raw cells and span-aware physical grid
  -> typed cell and row analysis
  -> logical-column normalization
  -> logical headers and logical units
  -> content-first table classification
  -> title and continuation context
  -> logical Markdown rendering
  -> one complete logical table per chunk
  -> logical table embedding input
```

The physical representation must remain available for traceability. It must no longer be used as retrieval text, chunk table rows, logical units, or notebook columns.

Reranking remains paused. All ten filings must be regenerated from processed blocks through embeddings after the parser is repaired and the gates in this document pass.

## 2. Scope and non-goals

### 2.1 In scope

- Repair inline text joining at real SEC `<br>` boundaries.
- Preserve and narrowly harden raw cell formatting evidence.
- Recognize financial values, ranges, percentages, dates, footnotes, missing markers, and identifiers.
- Detect explicit, inferred, and genuinely absent table headers.
- Reconstruct logical columns from physical cell intervals without row-wise left shifting.
- Infer units only after logical-column reconstruction.
- Separate the literal SEC Item from the effective document region.
- Remove the unconditional Item 15 table-classification shortcut.
- Distinguish financial data, structured text, indexes/navigation, exhibit lists, semantic lists, layout, and unknown tables.
- Improve title selection and record title provenance.
- Link high-confidence continuation fragments with a stable logical table ID.
- Render valid Markdown from logical data.
- Chunk one complete logical table, which may contain more than one HTML fragment.
- Make downstream table embedding input consume logical fields.
- Replace misleading table QA metrics with structural metrics.
- Fix `notebooks/experiment.ipynb` so it consumes nested lists directly.
- Add real SEC regression fixtures for all ten approved companies.
- Regenerate and validate all derived artifacts after implementation.

### 2.2 Explicitly out of scope

- Do not change the fixed ten-company corpus.
- Do not redownload or edit anything under `data/raw/`.
- Do not replace the parser with `pandas.read_html()`, browser rendering, an LLM parser, OCR, or another framework.
- Do not rewrite SEC acquisition or normal 10-K selection.
- Do not redesign narrative extraction or the recursive 500-token/32-token-overlap narrative baseline.
- Do not add hybrid retrieval, BM25, query rewriting, Graph RAG, agentic RAG, or a reranker as part of this repair.
- Do not change BGE-base model selection, prefixes, normalization, vector format, or vector ordering.
- Do not attempt page-number provenance in this change.
- Do not silently drop ambiguous tables or non-numeric structured tables.
- Do not optimize unrelated style, naming, formatting, or module organization.

## 3. Verified current state

### 3.1 Baseline tests

The complete current suite passes 31 of 31 tests. This is false confidence, not evidence that table parsing is acceptable. Existing table tests primarily use compact synthetic HTML. They test that fields are present and copied, not that real SEC physical grids are reconstructed into logical tables.

### 3.2 Persisted corpus measurements

The audit found 914 persisted table blocks, including retained data/text/unknown tables and navigation tables. Of those, 908 currently become table chunks because six navigation tables are excluded.

| Ticker | Processed table blocks | Chunked tables | Tables over 50% empty | Maximum physical-display width | Data tables missing titles |
|---|---:|---:|---:|---:|---:|
| APTV | 138 | 138 | 136 | 69 | 28 |
| AUR | 36 | 35 | 36 | 36 | 4 |
| F | 143 | 142 | 142 | 60 | 5 |
| GM | 126 | 126 | 122 | 54 | 0 |
| GOOGL | 172 | 171 | 171 | 36 | 3 |
| MBLY | 53 | 52 | 27 | 18 | 5 |
| NVDA | 58 | 57 | 57 | 45 | 0 |
| OUST | 62 | 62 | 62 | 33 | 3 |
| QCOM | 57 | 57 | 57 | 33 | 8 |
| TSLA | 69 | 68 | 69 | 45 | 7 |
| **Total** | **914** | **908** | **879 (96.17%)** | — | **63** |

These are fresh counts from the files present on the audit date. “Processed table blocks” and the sparsity column include navigation tables; “chunked tables” excludes the six navigation blocks under the current configuration. This explains several one-table differences from narrower data/text-only counts. The current persisted Tesla file has seven null-title `data_table` blocks, while the supplied earlier findings reported two; treat that as artifact/version drift, not as a reason to tune a rule to either count. The post-repair report must state its exact schema version, input hash, inclusion policy, and measured count.

Additional measured failures:

- Persisted classes are 671 data, 233 text, four unknown, and six navigation tables.
- 688 of 914 table blocks contain standalone currency or percent marker cells.
- 310 physical columns, spread across 134 tables, consist only of standalone currency/percent markers.
- Current table chunk text contains 64,933 adjacent empty-pipe pairs.
- Zero of 908 table chunks contains a Markdown header delimiter row.
- Every saved chunk-statistics file nevertheless reports `table_context_accuracy = 1.0`.
- Current BGE manifests report 29 truncated table embedding inputs.
- GM has no null data-table titles but many titles are generic company or `NOTES ... (Continued)` headers, proving that “non-null title” is not a useful quality gate.

### 3.3 Persisted schema staleness

The processed table corpus contains two schemas:

- 582 current-style data tables.
- 89 stale data tables: all 30 Aurora data tables plus all 59 Tesla data tables.

All 4,279 Aurora raw cells and all 9,522 Tesla raw cells lack the current `is_bold`, `alignment`, and `has_bottom_border` signals. Those 89 data tables also lack `column_units`. Running the current parser in memory restores the signals, so the raw filings are not the cause.

There is also a documentation/artifact discrepancy. Current on-disk Tesla chunk stats identify recursive token-based 500/32 chunking, while project status documents describe Tesla as character-chunked. Do not choose one description and trust it: Tesla's current-format chunks were produced from stale parsed table blocks. Aurora is stale too. After this repair, every company must be regenerated from blocks through embeddings regardless of current hashes or timestamps.

### 3.4 Current failure path

```text
preprocess_filing.extract_filing_to_jsonl()
  -> parse_filing_html()
  -> DOM cleanup
  -> block_extraction.extract_blocks()
     -> visit_node()
        -> emit_table()
           -> extract_table_structure()
           -> classify_table()
           -> detect_table_header_rows()
           -> find_table_title()
           -> extract_column_units()
           -> extract_table_units()
           -> build_column_headers()
           -> render_table_text()
  -> validate_blocks()
  -> write_blocks_jsonl()

chunk_documents.chunk_blocks()
  -> chunk_table()
  -> chunk_statistics()

embed_chunks.embedding_text()
  -> table_embedding_text()
```

Exact current hotspots, before implementation shifts line numbers:

- `src/filings/table_processing.py:76-198`: raw/span-aware physical grid.
- `src/filings/table_processing.py:201-220`: narrow numeric recognition and first-number header rule.
- `src/filings/table_processing.py:223-303`: physical-column headers and units.
- `src/filings/table_processing.py:306-354`: stateless title scan.
- `src/filings/table_processing.py:357-442`: classification and unconditional Item 15 shortcut.
- `src/filings/table_processing.py:445-458`: physical-grid pipe rendering.
- `src/filings/block_extraction.py:159-231`: table orchestration and persisted schema.
- `src/filings/block_extraction.py:86-143`: section/subsection state.
- `src/filings/dom_processing.py:57-65,160-174`: whitespace normalization and fragment concatenation.
- `src/chunking/chunk_documents.py:269-301`: physical rows copied into chunks.
- `src/chunking/chunk_documents.py:356-369`: no table-shape validation.
- `src/chunking/chunk_documents.py:414-442`: context-copy metric presented as table quality.
- `src/embeddings/embed_chunks.py:122-148`: sparse headers/rows used for table embedding text.
- `notebooks/experiment.ipynb`, code cell 7 / `In[25]`: nested lists converted to strings and split on commas.

The exact structural failure in `extract_table_structure()` is:

1. A spanning header repeats through all covered physical `slots`.
2. `nonempty_columns` sees those repeated span values and retains spacer positions.
3. `rows` blanks non-origin span positions but retains the positions themselves.
4. `expanded_rows` repeats header text through those positions.
5. Header, unit, renderer, chunker, and embedding code all treat those positions as semantic columns.

The raw geometry is useful; the interpretation is wrong.

## 4. Definitions and target contracts

### 4.1 HTML table fragment

One source `<table>` element after existing DOM cleanup. A fragment receives a stable `html_table_id`. One fragment is not necessarily one semantic/logical table.

### 4.2 Raw cell

One source `<th>` or `<td>` origin cell, with visible text, source row/column, rowspan, colspan, tag, formatting signals, and a stable cell ID. This is the immutable evidence representation within a processed artifact.

### 4.3 Physical grid

The current span-aware rectangular grid based on HTML positions. It is retained for traceability and diagnostics. Its width must never be described as logical table width.

### 4.4 Logical cell

One semantic cell reconstructed from one or more raw cells. Examples include `$` plus `14,600` becoming `$14,600`, and `17.2` plus `%` becoming `17.2%`. A logical cell stores display text separately from raw-cell provenance.

### 4.5 Logical column

A semantic lane inferred from repeated body-cell physical intervals and header spans. It is not obtained by deleting empty cells independently in each row.

### 4.6 Logical table

One semantic table identified by `logical_table_id`. It contains one or more HTML table fragments. Each fragment is normalized independently before any safe horizontal/vertical composition.

### 4.7 Original Item and document region

`section` remains the literal SEC Item provenance already emitted by the parser. `document_region` is a separate effective context used by table classification and retrieval context. Appended financial statements may therefore retain `section = Item 15` or `Item 16` while correctly having `document_region = financial_statements` or `financial_statement_notes`.

## 5. Compatibility strategy and schema versions

This repair must be explicit about stale artifacts.

- Add `table_schema_version = 2` to every retained processed table block.
- Bump `data/chunks/chunking-config.json` from schema version 2 to 3 when the table policy changes from one HTML block to one logical table.
- Add `chunk_schema_version = 3` to every generated chunk.
- Bump the embedding manifest from schema version 2 to 3 because table embedding input semantics change.
- Record the table schema version and table embedding policy in the embedding manifest.
- Make the chunk loader reject physical-only/stale table blocks. Do not silently fall back from `logical_rows` to `rows`.
- For one transition, old ambiguous fields may remain as deprecated aliases, but all production consumers must read explicit logical fields.

Recommended transition rule:

```text
processed rows                    = deprecated physical grid alias
processed column_headers          = deprecated physical header alias
processed column_units            = deprecated physical unit alias

processed physical_rows           = canonical physical display grid
processed logical_rows            = canonical semantic body rows
processed logical_column_headers  = canonical flattened semantic headers
processed logical_column_units    = canonical semantic units
```

Do not silently redefine `rows` from physical to logical. That would destroy the ability to distinguish old and new artifacts and could make stale files appear valid.

## 6. Required processed-table schema

Every retained table fragment must contain the following concepts. Exact internal helper classes are an implementation detail; the serialized meanings are not. The JSON below is abridged: it shows one of GM's three body rows, and the empty raw/physical arrays stand in for the full preserved evidence that a production record is required to populate.

```json
{
  "table_schema_version": 2,
  "table_heuristics_version": "sec-logical-v1",
  "html_table_id": "GM-2025-HTMLTABLE-0010",
  "html_table_index": 10,
  "html_table_xpath": "/html/body/div[83]/table",
  "html_table_fingerprint": "sha256:...",
  "html_table_fingerprint_version": "cleaned-lxml-html-v1",
  "table_class": "data",
  "table_kind": "financial_data",
  "normalization_mode": "semantic_grid",
  "classification_reasons": ["period_headers", "repeated_record_grid", "percent_values", "financial_terms"],
  "classification_scores": {"financial_data": 0.99, "structured_text": 0.45, "index_navigation": 0.0},
  "document_region": "filing_body",
  "effective_section_path": ["Item 1 — Business"],
  "logical_table_id": "GM-2025-TABLE-0010",
  "table_fragment_index": 1,
  "is_continuation": false,
  "continued_from_block_id": null,
  "continuation_mode_hint": null,
  "continuation_reasons": [],
  "native_context": {
    "title": "The following table summarizes wholesale vehicle sales by our Automotive operations (vehicles in thousands)",
    "title_source": "prose_caption",
    "header_mode": "inferred",
    "header_signature": "sha256:...",
    "unit_signature": "sha256:...",
    "provisional_table_kind": "financial_data",
    "provisional_classification_reasons": ["period_headers", "repeated_record_grid"],
    "missing_fields": []
  },
  "inherited_context": {
    "title_from_block_id": null,
    "header_from_block_id": null,
    "units_from_block_id": null
  },
  "title": "The following table summarizes wholesale vehicle sales by our Automotive operations (vehicles in thousands)",
  "title_source": "prose_caption",
  "title_source_block_id": "GM-2025-000015",
  "title_source_raw_cell_ids": [],
  "title_source_locator": "block:GM-2025-000015",
  "title_confidence": 1.0,
  "title_quality_status": "accepted_caption",
  "rejected_title_candidates": [
    {"text": "GENERAL MOTORS COMPANY AND SUBSIDIARIES", "source": "heading", "reason_codes": ["company_header"]}
  ],
  "units": "mixed",
  "header_mode": "inferred",
  "header_confidence": 0.98,
  "header_reasons": ["bold_period_row", "centered_year_groups", "repeated_body_signature"],
  "header_row_source_indexes": [1, 2],
  "row_role_diagnostics": [],
  "logical_header_rows": [
    ["", "Years Ended December 31,", "Years Ended December 31,", "Years Ended December 31,", "Years Ended December 31,", "Years Ended December 31,", "Years Ended December 31,"],
    ["", "2025", "2025", "2024", "2024", "2023", "2023"]
  ],
  "logical_header_paths": [
    [],
    ["Years Ended December 31,", "2025"],
    ["Years Ended December 31,", "2025"],
    ["Years Ended December 31,", "2024"],
    ["Years Ended December 31,", "2024"],
    ["Years Ended December 31,", "2023"],
    ["Years Ended December 31,", "2023"]
  ],
  "logical_header_context": ["Years Ended December 31,"],
  "logical_header_context_source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R1-C3"],
  "logical_column_headers": [
    "Line item", "2025 — Volume", "2025 — Percent",
    "2024 — Volume", "2024 — Percent", "2023 — Volume", "2023 — Percent"
  ],
  "logical_column_header_metadata": [
    {"source_raw_cell_ids": [], "generated_components": ["Line item"]},
    {"source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R1-C3", "GM-2025-HTMLTABLE-0010-R2-C3"], "generated_components": ["Volume"]},
    {"source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R1-C3", "GM-2025-HTMLTABLE-0010-R2-C3"], "generated_components": ["Percent"]},
    {"source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R1-C3", "GM-2025-HTMLTABLE-0010-R2-C15"], "generated_components": ["Volume"]},
    {"source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R1-C3", "GM-2025-HTMLTABLE-0010-R2-C15"], "generated_components": ["Percent"]},
    {"source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R1-C3", "GM-2025-HTMLTABLE-0010-R2-C27"], "generated_components": ["Volume"]},
    {"source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R1-C3", "GM-2025-HTMLTABLE-0010-R2-C27"], "generated_components": ["Percent"]}
  ],
  "logical_column_units": [
    "text", "vehicles_thousands", "percent", "vehicles_thousands",
    "percent", "vehicles_thousands", "percent"
  ],
  "logical_column_unit_metadata": [
    {"source_kind": "column_role", "source_block_ids": [], "source_raw_cell_ids": [], "inherited_from_block_id": null, "confidence": 1.0, "reason_code": "row_label_lane"},
    {"source_kind": "title_scale", "source_block_ids": ["GM-2025-000015"], "source_raw_cell_ids": [], "inherited_from_block_id": null, "confidence": 1.0, "reason_code": "vehicles_in_thousands_caption"},
    {"source_kind": "body_markers", "source_block_ids": [], "source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R3-C11"], "inherited_from_block_id": null, "confidence": 1.0, "reason_code": "explicit_percent_marker"},
    {"source_kind": "title_scale", "source_block_ids": ["GM-2025-000015"], "source_raw_cell_ids": [], "inherited_from_block_id": null, "confidence": 1.0, "reason_code": "vehicles_in_thousands_caption"},
    {"source_kind": "body_markers", "source_block_ids": [], "source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R3-C23"], "inherited_from_block_id": null, "confidence": 1.0, "reason_code": "explicit_percent_marker"},
    {"source_kind": "title_scale", "source_block_ids": ["GM-2025-000015"], "source_raw_cell_ids": [], "inherited_from_block_id": null, "confidence": 1.0, "reason_code": "vehicles_in_thousands_caption"},
    {"source_kind": "body_markers", "source_block_ids": [], "source_raw_cell_ids": ["GM-2025-HTMLTABLE-0010-R3-C35"], "inherited_from_block_id": null, "confidence": 1.0, "reason_code": "explicit_percent_marker"}
  ],
  "logical_rows": [["GMNA", "3,296", "86.8%", "3,464", "86.4%", "3,147", "83.5%"]],
  "logical_row_source_indexes": [3],
  "logical_row_roles": ["data"],
  "logical_body_rowspans": [],
  "logical_cell_states": [["present", "present", "present", "present", "present", "present", "present"]],
  "logical_cell_sources": [[
    ["GM-2025-HTMLTABLE-0010-R3-C0"],
    ["GM-2025-HTMLTABLE-0010-R3-C3"],
    ["GM-2025-HTMLTABLE-0010-R3-C9", "GM-2025-HTMLTABLE-0010-R3-C11"],
    ["GM-2025-HTMLTABLE-0010-R3-C15"],
    ["GM-2025-HTMLTABLE-0010-R3-C21", "GM-2025-HTMLTABLE-0010-R3-C23"],
    ["GM-2025-HTMLTABLE-0010-R3-C27"],
    ["GM-2025-HTMLTABLE-0010-R3-C33", "GM-2025-HTMLTABLE-0010-R3-C35"]
  ]],
  "logical_columns": [
    {"logical_index": 0, "physical_intervals": [[0, 3]], "role": "row_label", "unit": "text"},
    {"logical_index": 1, "physical_intervals": [[3, 5]], "role": "value", "unit": "vehicles_thousands"},
    {"logical_index": 2, "physical_intervals": [[9, 12]], "role": "value", "unit": "percent"},
    {"logical_index": 3, "physical_intervals": [[15, 17]], "role": "value", "unit": "vehicles_thousands"},
    {"logical_index": 4, "physical_intervals": [[21, 24]], "role": "value", "unit": "percent"},
    {"logical_index": 5, "physical_intervals": [[27, 29]], "role": "value", "unit": "vehicles_thousands"},
    {"logical_index": 6, "physical_intervals": [[33, 36]], "role": "value", "unit": "percent"}
  ],
  "raw_rows": [],
  "raw_cells": [],
  "physical_rows": [],
  "physical_expanded_rows": [],
  "physical_source_row_indexes": [],
  "physical_source_column_indexes": [],
  "normalization_diagnostics": {
    "status": "ok",
    "source_coordinate_width": 36,
    "physical_display_width": 36,
    "logical_width": 7,
    "source_coordinate_empty_density": 0.86,
    "physical_display_empty_density": 0.86,
    "logical_empty_density": 0.0,
    "unmapped_nonempty_raw_cell_ids": [],
    "ignored_raw_cells": [],
    "collisions": [],
    "fallback_used": false
  }
}
```

### 6.1 Schema invariants

For every fragment:

```text
logical_width > 0
len(logical_column_headers) == logical_width
len(logical_column_header_metadata) == logical_width
len(logical_header_paths) == logical_width
len(logical_column_units) == logical_width
len(logical_column_unit_metadata) == logical_width
len(logical_columns) == logical_width
all(len(row) == logical_width for row in logical_header_rows)
all(len(row) == logical_width for row in logical_rows)
len(logical_row_source_indexes) == len(logical_rows)
len(logical_row_roles) == len(logical_rows)
logical_cell_sources has the same row/column shape as logical_rows
logical_cell_states has the same row/column shape as logical_rows
no logical column is entirely empty
no logical body cell is exactly "$", "€", "£", "¥", or "%"
all nonempty raw cells are mapped to logical content/header/title/unit metadata,
or appear in ignored_raw_cells with an explicit reason
raw_cells is never mutated after extraction
raw_cell_id values are unique within a filing and every referenced raw-cell ID exists
logical_columns.logical_index values are exactly 0..logical_width-1
html_table_index is one-based and agrees with html_table_id
html_table_fingerprint and html_table_fingerprint_version are nonempty
table_heuristics_version is recognized by the validator
table_fragment_index is one-based within logical_table_id
```

Header raw cells may project to more than one logical header path when their colspan covers multiple logical lanes. Body raw cells must not be silently copied into multiple logical body cells.

`logical_header_paths` contains only filing-derived header text. `logical_column_headers` is the usable semantic label and may add a deterministic role/unit component when two lanes have the same source path or the row-label header is blank. Every added component must be named in `logical_column_header_metadata.generated_components`; `source_raw_cell_ids` records the actual filing header evidence. Generated components must never be represented as verbatim filing text in citation provenance. For a genuinely headerless table, keep source paths empty and use neutral labels for display as described below.

`logical_header_context` is the longest exact normalized prefix shared by every nonempty value-column source path when at least two value columns share it. Do not remove the prefix if doing so empties any participating value path. Store the source raw-cell IDs once in `logical_header_context_source_raw_cell_ids`; keep the full source paths unchanged. `logical_column_headers` may elide this common prefix to avoid repeating it in every cell, but rendering must emit it as a `Header context:` line and table embedding text must include it exactly once.

Use `logical_row_roles` values `data`, `section_label`, `subtotal`, `total`, or `footnote`. Title rows, unit rows, and repeated continuation headers are promoted to their dedicated metadata and provenance fields rather than emitted as ordinary logical body rows. `row_role_diagnostics` may be compact, but it must retain the source-row role, score components, selected boundary, and reason codes needed to explain header/body decisions.

Represent a genuine body `rowspan` once at its origin logical row and record its covered logical row interval in `logical_body_rowspans` as `{source_raw_cell_id, logical_column, logical_row_start, logical_row_end}` with an exclusive end. Leave covered continuation cells blank in `logical_rows`; do not duplicate an amount into several records. Header rowspans may contribute the same source header cell to each covered lane/path, with duplicate display labels removed during path flattening.

### 6.2 Raw cell additions

Preserve every existing raw-cell field and add:

```json
{
  "raw_cell_id": "GM-2025-HTMLTABLE-0010-R3-C15",
  "physical_start": 15,
  "physical_end": 17,
  "bold_text_ratio": 0.0
}
```

`physical_end` is exclusive. Keep `is_bold` for compatibility, but base new header scoring on `bold_text_ratio`. Correct border parsing so `0`, `0px`, `0pt`, `none`, and `hidden` are not treated as visible borders.

For `html_table_fingerprint_version = cleaned-lxml-html-v1`, hash the UTF-8 bytes returned for the cleaned top-level table by `lxml.html.tostring(node, encoding="utf-8", method="html", with_tail=False)` before logical processing. Record the fingerprint version and runtime lxml version in the processed-file QA manifest. This fingerprint is a deterministic identifier for the same frozen raw input/parser cleanup version, not a cross-parser canonical HTML standard.

### 6.3 Table taxonomy

Keep the current coarse class for compatibility and add a canonical fine-grained kind.

| `table_kind` | Meaning | Coarse `table_class` | Existing `content_type` |
|---|---|---|---|
| `financial_data` | Financial statements, notes, KPI matrices, monetary/rate schedules | `data` | `data_table` |
| `structured_text` | Record-oriented table whose value is not primarily financial, such as insider trading | `text` | `text_table` |
| `index_navigation` | TOC or financial-statement/page index | `navigation` | `navigation` |
| `exhibit_list` | Exhibit number/description/form/filing registry | `text` | `text_table` |
| `semantic_list` | Bullet-layout table already converted to list items | `list` | `list_item` |
| `layout` | Signature/form/layout scaffolding without a meaningful record grid | `text` | `text_table` |
| `unknown` | Retained unresolved evidence | `unknown` | `unknown_table` |

This mapping minimizes changes to working narrative and chunk routing. Do not automatically discard `structured_text`, `exhibit_list`, `layout`, or `unknown` in this repair. Existing retrieval inclusion remains unchanged except that true `index_navigation` continues to be excluded. The new kind enables later filtering without losing evidence now.

`semantic_list` is the deliberate exception to the retained-table schema: the existing early bullet-layout branch emits `list_item` blocks with table-row provenance rather than a table block. Its source HTML table still consumes an `html_table_index`, and its list-conversion regression tests must pass, but it does not need a `logical_table_id` or logical grid unless the classifier cannot confidently take that branch.

`classification_scores` are deterministic evidence totals or normalized per-kind confidences used for diagnostics; they are not required to sum to one. Document the score scale and thresholds as constants next to the classifier, and emit the decisive `classification_reasons` independently of raw scores.

## 7. Implementation sequence

Follow the sequence below. Each phase has a stop condition. Do not proceed to full-corpus regeneration while an earlier phase's fixture gates fail.

### Phase 0 — Freeze diagnostics and add fixtures

1. Record current processed/chunk/embedding hashes as historical diagnostics; do not call them a valid baseline.
2. Extract minimal byte-faithful table snippets from frozen raw filings into `tests/fixtures/tables/<ticker>/`; do not manually prettify, normalize, or repair their HTML.
3. Add contextual snippets containing the exact preceding headings/captions and relevant adjacent table fragments for Item 15/16, title, and continuation tests. Geometry-only tests may use the isolated outer `<table>` element.
4. Add a fixture manifest with ticker, filing, accession, source URL, current block ID, cleaned table ordinal, cleaned XPath, fixture SHA-256, source-fragment fingerprint, included context boundaries, and asserted behavior.
5. Do not alter source HTML in `data/raw/`.
6. Keep fixtures small enough for unit tests; do not snapshot entire filings or entire processed JSONL files.

Stop condition: every priority fixture in Section 13 can be parsed independently and reproduces the current failure before the fix.

### Phase 1 — Repair visible text joining

`normalize_text()` cannot recover boundaries after `lxml.text_content()` has concatenated them. Add one shared visible-text collector in `src/filings/dom_processing.py` and use it for paragraph, heading, title, and table-cell extraction.

Required behavior:

- Preserve exact adjacency through ordinary inline wrappers such as `span`, `b`, `strong`, visible Inline XBRL wrappers, and formatting-only elements. Inline markup can divide one word.
- Insert a boundary marker for `<br>` and genuine block-child transitions.
- When resolving a boundary, remove a soft hyphen and join; preserve an ordinary trailing hyphen and join without an extra space; avoid a space before closing punctuation; otherwise insert one space.
- Continue normalizing non-breaking spaces, zero-width characters, repeated spaces, and Unicode NFC.
- Continue excluding nested tables when extracting an outer table cell.

Because the shared collector is also used for paragraphs/headings, a real `<br>` or block-child boundary may produce a localized narrative-text correction. That is authorized. Preserve narrative block count/order, section assignment, and the span-calculation algorithm/schema, but regenerate character/token offsets from corrected source text. A corpus diff must show that every narrative-text change is explained by one of the boundary rules above; unrelated prose normalization is a regression.

Required examples:

```text
of<br/>contractual               -> of contractual
Commodity<br/>pass-<br/>through  -> Commodity pass-through
inter<span>national</span>       -> international
<b>Net</b> income                -> Net income
```

Do not insert a space at every inline-element boundary. That would turn formatting-split words into different words.

Stop condition: the Aptiv real fixture produces `Volume, net of contractual price reductions` and `Commodity pass-through`, while inline formatting-split words remain intact.

### Phase 2 — Add typed cell-value analysis

Replace the single Boolean numeric regex as the basis for header detection and classification. Keep a compatibility `is_numeric_table_value()` wrapper only if needed, but make it delegate to typed analysis.

Add a detection-only normalizer. It may normalize dashes and strip footnote suffixes for matching, but must not overwrite display text.

The analyzer must distinguish at least:

```text
empty
missing_numeric
currency_marker
percent_marker
numeric_scalar
numeric_range
percentage
percentage_range
year
year_range
date
duration
footnote_marker
exhibit_or_file_identifier
text
```

Use two deterministic passes so token ambiguity is not resolved by a table class that has not been computed yet.

1. `analyze_cell_lexically()` returns all plausible `candidate_kinds` plus lexical features; it does not force one context-sensitive kind.
2. `refine_cell_kind()` uses only already available row/column evidence: header tokens, repeated column grammar, neighboring value shapes, stable identifier prefixes, and whether the cell participates in an exhibit/page-index record schema. It does not read the final `table_kind`.

Return a structured analysis rather than only the enum. It must retain at least `candidate_kinds`, `refined_kind`, detection-normalized text, optional numeric/range endpoints, currency code or marker, scale, percent flag, accounting-negative flag, missing-value flag, footnote suffix, identifier-pattern features, confidence, and reason codes. Thus `3,984*` normally refines to `numeric_scalar` plus `footnote_suffix = "*"`; `$10.3-11.7` refines to `numeric_range` plus a currency attribute. Display text remains unchanged outside this analysis object.

Required recognition:

- Optional signs and accounting parentheses: `-17`, `+17`, `(17)`, `(1.5)%`.
- Thousands separators and decimals: `3,984`, `21.0`, `0.1`.
- Currency-prefixed values: `$10`, `$ 10`, `€10`.
- Missing numeric markers: em dash, en dash, and contextually a standalone hyphen.
- Numeric ranges with ASCII/Unicode dashes or `to`: `10.3-11.7`, `10.3–11.7`, `2027 - 2053`.
- Percentage ranges: `2.39% - 5.12%`.
- Footnoted values: `3,984*`, `3,984†`, `3,984(1)`.
- Dates: `12/10/2025`, ISO dates, and month-name dates.
- Years as their own type rather than ordinary numeric scalars.

Lexical candidate order:

1. Empty/missing/markers.
2. Dates.
3. High-specificity identifiers such as SEC file numbers, exhibit numbers with suffix markers, and alphanumeric page IDs.
4. Accounting scalars and percentages.
5. Ranges whose endpoints are valid typed values.
6. General identifier candidates.
7. Text fallback.

Candidate recognition is non-exclusive. `10.24*` may be both a footnoted scalar and exhibit identifier candidate; a column headed `Exhibit Number` containing repeated dotted identifiers refines it to `exhibit_or_file_identifier`. `001-39463` matches the high-specificity SEC file-number grammar and must not refine to a numeric range. `F-1` and `59` may refine to page identifiers when their column and row schema form a document-title/page index. The final table classifier consumes these refined cell kinds; it does not feed them.

Stop condition: GM guidance ranges, NVIDIA footnoted shares/dates, the Aptiv hedge value, and Qualcomm maturity/rate ranges are data-like inputs rather than unknown strings.

### Phase 3 — Preserve and harden physical extraction

Do not rewrite existing rowspan/colspan slot reconstruction. Extend it narrowly:

1. Assign a stable top-level table ordinal and `html_table_id` during traversal.
2. Record a cleaned-DOM XPath and deterministic fingerprint.
3. Assign stable raw-cell IDs.
4. Serialize `expanded_rows`, `source_row_indexes`, and `source_column_indexes`, which extraction currently calculates but emission drops.
5. Rename canonical serialized forms to `physical_*`, retaining old fields only as deprecated aliases.
6. Add `bold_text_ratio`; correct zero-border handling.
7. Preserve nested-table exclusion and all original span coordinates.

Recommended stable IDs:

```text
html_table_id = {TICKER}-{YEAR}-HTMLTABLE-{1-based top-level ordinal:04d}
raw_cell_id = {html_table_id}-R{source_row}-C{source_column}
new logical_table_id = {TICKER}-{YEAR}-TABLE-{first HTML table ordinal:04d}
```

Using the first fragment's source ordinal prevents unrelated later logical IDs from shifting when continuation linkage changes.

Stop condition: current raw-cell/span tests still pass, and each real fixture has stable raw locators and unchanged source evidence.

### Phase 4 — Detect row roles and header boundaries

Replace `detect_table_header_rows()` with a row-profile detector that uses nonempty raw origin cells and the formatting signals already recorded. Do not score empty physical slots as evidence.

For each nonempty source row, calculate:

- `<th>` text ratio.
- Bold text ratio.
- Centered-cell ratio.
- Visible bottom-border ratio.
- Colspan and rowspan patterns.
- Single-cell/wide-cell coverage.
- Year/reporting-period/date density.
- Unit/measure vocabulary density.
- Numeric/currency/percentage/range density.
- First-cell-label plus later-value pattern.
- Similarity of its typed-cell signature to the next two or three likely body rows.

Choose a contiguous leading header prefix only. Once body rows begin, later text-only rows such as `North America`, `Assets`, `Foreign tax effects`, or `Reported as:` remain body section rows unless explicit evidence identifies a repeated internal header.

A useful initial scoring model is:

```text
header evidence:
  strong <th>, bold, centered, bottom-border, colspan, period, unit,
  and measure-label signals

body evidence:
  repeated record signature
  row label followed by scalar/range/date/identifier values
  currency/percent bundles
  similarity to subsequent rows

negative header evidence:
  opening-balance/total/ordinary row-label pattern
  a row whose value positions match later body rows
```

Evaluate candidate boundaries `0..N` over a bounded leading window and select the boundary with the best header-prefix/body-suffix separation. Store scores and reasons; do not hide low confidence.

Required modes:

- `explicit`: `<th>` or very strong formatting/semantic evidence.
- `inferred`: a defensible leading header prefix without explicit `<th>`.
- `headerless`: no defensible header; this is valid, not an error.

Required safeguards:

- A four-digit year alone is not enough to call a row a header.
- A year/value maturity row matching later rows is a body row.
- Numeric ranges and dates may legitimately occur in headers or body rows; use row relationships.
- Bold opening-balance and total rows remain body when their value geometry matches the body.
- A section-label row following headers does not extend the header prefix.
- If the best boundary has low confidence or is not materially better than zero, select `headerless` rather than consuming evidence as headers.

Run a second consistency check after logical lanes are built. Allow at most one deterministic boundary correction based on logical row signatures; do not create an iterative heuristic loop.

Stop condition: Tesla fair-value headers are rows 0 and 1; Aurora amount/percent headers include period, year, and measure rows; GM `North America` remains a body section row; GOOGL's maturity fixture remains headerless.

### Phase 5 — Reconstruct logical columns

Add a distinct `normalize_logical_columns(structure, row_analysis)` stage immediately after physical extraction. This is the core repair.

#### 5.1 Never use row-wise blank deletion

The following is prohibited as a method of forming logical rows:

```python
[value for value in row if value]
```

Missing values and different colspans would shift later values into the wrong columns.

#### 5.2 Form atomic body cells

Group nonempty raw origin cells by source row and form atomic candidates with physical half-open intervals `[column, column + colspan)`.

Bundle only adjacent compatible affixes in the same source row:

```text
currency marker + scalar/range/missing value -> one currency logical atom
scalar/range + percent marker                -> one percentage logical atom
currency marker + missing marker            -> one currency-missing atom
number + footnote-only suffix                -> one footnoted-number atom
separate accounting parentheses, if present -> one accounting-number atom
```

Preserve every contributing raw-cell ID. Preserve filing-visible display text with conservative spacing:

```text
"$" + "14,600" -> "$14,600"
"17.2" + "%"   -> "17.2%"
"$" + "—"      -> "$—"
```

Do not bundle cells that co-occur as independent measures. An amount and a percentage can be merged only when the second cell is the percent marker itself, not when it is a percentage value.

#### 5.3 Select body rows for lane inference

Exclude from lane-definition evidence:

- detected header rows;
- empty rows;
- unit-only rows;
- a single table-wide title/caption row;
- one-cell section-label rows spanning most of the table.

Retain those rows in final metadata or body output as appropriate; they are excluded only from lane inference.

Prefer data-rich body rows with:

- the highest repeated atomic-cell count;
- interval patterns repeated in other rows;
- a label atom plus several data atoms;
- no single atom spanning most of the table.

For a one-row table, supplement body anchors with leaf-header geometry.

#### 5.4 Infer lane intervals

Use repeated physical intervals, not physical column numbers alone.

1. Start with the most-supported body-row interval signature.
2. Treat bundled intervals such as `[3,5)` as one lane.
3. Cluster intervals from other body rows by maximum overlap and compatible typed role.
4. Allow a narrower interval to map to a wider learned lane when they overlap and never co-occur independently in the same row.
5. Two atoms that occur in the same source row can never collapse into the same lane.
6. Prefer interval overlap, then nearest center, then nearest start as deterministic tie-breakers.
7. Add a new lane only when an interval recurs, a leaf header supports it, or the one-row-table case requires it.
8. Keep the left row-label lane separate from value lanes. A wide left-aligned text label must not absorb value lanes merely because its colspan reaches toward them.
9. Sort final lanes by physical position.

GM example:

```text
row A: column 3 "$" + column 4 "3,296" -> interval [3,5)
row B: column 3 colspan=2 "503"         -> interval [3,5)

Both map to the same logical volume lane.
```

Percent example:

```text
column 15 colspan=2 "17.2" + column 17 "%" -> interval [15,18)
column 15 colspan=2 "—"                      -> compatible interval [15,17)

Both map to the same logical percentage lane.
```

#### 5.5 Map every body row

For each body atom:

1. Find compatible logical lanes by interval overlap and role.
2. Use deterministic tie-breakers.
3. Place the atom in exactly one logical cell.
4. Leave a blank when a source row has no atom for a known lane.
5. If two independent atoms map to one lane, record the attempted collision as a normalization decision and allocate separate lanes rather than overwrite either value.
6. If an atom cannot be mapped to an existing lane safely, allocate a conservative separate lane when alignment can still be represented. If even that is impossible, return a failed in-memory normalization result with the atom listed as unresolved. Never silently discard it.

Every emitted row is padded to exactly `logical_width`.

An empty raw `<td>` is normally presentation geometry, not a semantic source cell, and need not appear in `logical_cell_sources`. A value that is semantically missing must have a visible missing marker such as `—`, `-`, or `N/A`, or be inferred as a blank at a known lane because other cells in the same aligned record prove the lane exists. In the latter case keep the logical display value empty and record a state such as `missing_blank_in_aligned_lane` in same-shape `logical_cell_states`; use `present`, `missing_marker`, `missing_blank_in_aligned_lane`, `rowspan_covered`, and `not_applicable` as the bounded vocabulary. Never manufacture a zero. Raw-cell accounting gates apply to non-empty cells, while empty-origin counts remain available in the physical diagnostics.

#### 5.6 Project headers onto lanes

For each header origin cell:

1. Project it to every logical lane whose anchor center lies inside the cell's physical colspan.
2. If no center lies inside, use positive interval overlap as a fallback.
3. Preserve source row order to form a header path for each logical column.
4. Remove exact repeated labels within one path.
5. Route unit-only labels to unit inference rather than polluting every flattened header.
6. Preserve parent headers such as period/year and child headers such as measure.

Store both:

- `logical_header_paths`: one ordered label path per logical column.
- `logical_column_headers`: a flattened ` — ` join used for display/search.
- `logical_column_header_metadata`: source raw-cell IDs plus any deterministic generated component.
- `logical_header_context`: any exact common source prefix elided from flattened display headers, with its raw-cell provenance.

Keep the paths source-only. When two semantic lanes share the same source path, append a conservative role/unit label such as `Amount`, `Percent`, or `Rate` to `logical_column_headers` and record it as generated metadata. When the left header is blank but the column is the stable row-label lane, `Line item` is an allowed generated component. Do not invent a financial measure from nearby prose when lane type/unit evidence does not support it.

Compute common-prefix elision only across nonempty value-column paths, after exact whitespace/case normalization for comparison. Preserve the full paths and place the common prefix in `logical_header_context`. If there is no prefix shared by every participating value column, flatten each full path. This makes the example headers `2025 — Volume`, `2025 — Percent`, and so on while retaining `Years Ended December 31,` once as header context.

The raw header cell still appears once in raw evidence even if it projects to several logical paths.

#### 5.7 Headerless tables

For `header_mode = headerless`:

- Keep empty source-derived header paths.
- Do not promote the first data row.
- Infer column roles only as metadata when confidence is high.
- Generate neutral display headers only at rendering time, such as `Row label`, `Value 1`, and `Value 2`.
- Do not claim display labels were present in the filing.

#### 5.8 Fallback policy

Ambiguity must be visible.

- Set `fallback_used = true` and retain reason codes when leaf headers, a low-support interval, or a conservative separate lane is required.
- Preserve attempted/resolved mapping decisions for audit. The serialized `collisions` and `unmapped_nonempty_raw_cell_ids` lists represent unresolved problems and must be empty in a production block.
- A conservative but structurally valid fallback may be retained as `unknown`, with its complete physical evidence and warning diagnostics.
- Do not render the physical grid as if it were a valid logical result.
- A normalization result that cannot satisfy source accounting and rectangular logical invariants must fail strict processed-block serialization. The audit/test path may inspect the failed in-memory result, but it must not silently pass into a production `.blocks.jsonl` via old `rows`.

There is one explicit lossless serializable fallback for evidence that is genuinely non-grid layout or remains low-evidence `unknown` after semantic normalization fails: `normalization_mode = row_text_fallback`.

```text
logical_width = 1
header_mode = headerless
logical_column_headers = ["Row content"]          # generated metadata
logical_column_units = ["text"]
logical_rows = one row per nonempty source row
logical_cell_sources = every nonempty origin raw-cell ID in source order for that row
normalization_diagnostics.status = "lossless_row_fallback"
fallback_used = true
```

Construct each fallback display cell by joining the nonempty origin-cell display strings in source order with a documented visible boundary token; escape it in Markdown and preserve the individual raw strings/cells unchanged in physical evidence. This is a retrieval/rendering view, not a claim that the row is one filing cell.

Allow this fallback only when either:

- high-confidence raw/profile evidence already identifies `layout`; or
- semantic normalization fails, financial/structured/index/exhibit predicates are not met, and final kind is `unknown`.

It is forbidden for a table with financial-data, structured-record, index, or exhibit evidence strong enough to meet its predicate; those tables must normalize correctly or fail serialization. Fallback tables remain in processed output and follow the existing coarse content-type/chunk inclusion policy, are reported separately from semantic-grid density/header metrics, and require manual review. They still require 100% nonempty raw-cell accounting, valid one-column Markdown, stable logical/HTML IDs, and full provenance.

Stop condition: every priority fixture reaches its exact expected logical width with zero unexplained body-cell loss and zero standalone marker cells.

### Phase 6 — Infer units from logical columns

Retire physical-position unit inference as the canonical result. Run unit inference after header projection and logical body mapping.

Use evidence in this precedence order:

1. Explicit percent marker or percent header.
2. Explicit currency marker plus a scale phrase.
3. Explicit shares/vehicles/unit-of-measure phrase plus scale.
4. Partial-width unit/header rows projected onto lanes.
5. Table caption/title unit phrase.
6. Repeated typed body values.
7. Generic number/date/text inference.

Recommended normalized vocabulary:

```text
text
number
number_thousands
number_millions
percent
usd
usd_thousands
usd_millions
usd_billions
eur
eur_thousands
eur_millions
gbp
jpy
currency
currency_thousands
currency_millions
currency_billions
shares
shares_thousands
shares_millions
vehicles_thousands
years
date
unknown
```

Use an ISO-specific unit when filing/table evidence identifies the currency. Use generic `currency_*` only when a marker is real but the currency is ambiguous. Do not assume that plain `in millions` means USD unless currency evidence exists. Do not interpret every string beginning with `in ` as shares.

Keep `units` as a human-readable compatibility summary:

- one common explicit unit when appropriate;
- `mixed` when multiple non-text kinds exist;
- `null` when there is no reliable table-level summary.

`logical_column_units` is canonical. It must have exactly `logical_width` entries, including `text` for row-label columns.

Persist a same-width `logical_column_unit_metadata` array. Every entry has exactly `source_kind`, `source_block_ids`, `source_raw_cell_ids`, `inherited_from_block_id`, `confidence`, and `reason_code`; use empty lists/null rather than mixing ID types in one field. An inherited continuation unit must point to the fragment/block from which it was inherited. This keeps an inferred scale from being presented as an explicit cell-level unit.

Stop condition: Aurora alternates USD-millions/percent columns; GM vehicle tables alternate vehicle-thousands/percent; currency symbols no longer occupy their own unit columns.

### Phase 7 — Repair document context and classification

#### 7.1 Preserve the literal Item

Do not overwrite current SEC Item provenance to make a table appear under Item 8. Keep `section` as the literal Item encountered in the filing. Add `document_region` to `ExtractionContext` and every emitted block. Keep current `section_path` semantics unchanged for narrative grouping; for table retrieval context, store a separate `effective_section_path` that begins with the literal Item and appends a human-readable region/financial-heading component when it adds information. Do not replace the literal Item inside citation metadata.

Required region values:

```text
filing_body
financial_statements
financial_statement_notes
financial_statement_schedules
exhibits
signatures
```

#### 7.2 Region transitions

An explicit Item heading updates `section` as it does now and resets the region deterministically. Item 8 enters `financial_statements`; ordinary business/risk/MD&A Items enter `filing_body`; Items 15 and 16 reset to `filing_body` rather than assuming either exhibits or financial statements because those Items contain mixed appended material. High-confidence non-Item headings then update the region.

Enter financial regions on exact/high-precision patterns such as:

```text
CONSOLIDATED STATEMENTS OF ...
CONSOLIDATED BALANCE SHEETS
CONSOLIDATED STATEMENTS OF CASH FLOWS
CONSOLIDATED STATEMENTS OF STOCKHOLDERS' EQUITY
NOTES TO [THE] [CONSOLIDATED] FINANCIAL STATEMENTS
NOTE <number> - <title>, once already inside financial reporting
FINANCIAL STATEMENT SCHEDULES
SCHEDULE II - ...
```

Enter `signatures` on `SIGNATURES` or `POWER OF ATTORNEY`. Enter `exhibits` on an actual exhibit heading/table boundary, not merely because the literal Item is 15. Financial headings may move the region from signatures or exhibits into financial statements because Ford and Qualcomm append financials after Item 16/signatures.

Do not infer a financial region from generic words such as `income`, `assets`, or `cash` alone.

Persist both literal Item and effective region in chunks. If retrieval text needs corrected context, prefix a human-readable region label while retaining the original Item as metadata.

#### 7.3 Remove the unconditional Item 15 shortcut

Delete the current rule equivalent to:

```python
if section.startswith("Item 15"):
    return "text"
```

Item number may be a weak prior. It can never determine table kind by itself.

#### 7.4 Classification precedence

Run the same evidence predicates after logical normalization in this order. Before continuation linking, store a provisional result from native fragment evidence. After the one allowed inheritance pass in Phase 9, recompute once and serialize only the final result; inherited context can fill missing evidence but cannot erase contradictory native content.

```text
1. index/navigation
2. exhibit list
3. semantic bullet list
4. financial data
5. structured text
6. layout
7. unknown
```

High-confidence index/navigation rule:

```text
at least three record rows
AND a stable two- or three-logical-column schema
AND the final column is predominantly page identifiers
AND the first column is predominantly document/section titles
AND currency/amount evidence is absent or weak
```

Existing TOC link/Item-reference rules remain and feed the same kind.

High-confidence exhibit-list rule:

```text
headers contain exhibit number/description/form/filing date/file number
OR repeated exhibit identifiers occur with long descriptions and filing/form references
```

Financial-data evidence includes effective financial region, projected period headers, monetary/percentage/scale evidence, financial terms, and repeated label-plus-financial-value rows. Structured-text evidence includes stable width, meaningful headers, and repeated name/role/date/identifier records even without monetary evidence. Layout covers signature/form scaffolding without a meaningful record grid. Unknown remains retained.

Return stable reason codes, human-readable reasons, and component scores. Classification tests should assert the kind and decisive reason category so later threshold changes remain explainable.

Stop condition:

- All four current unknown tables resolve: Aptiv hedge to financial data, Ford lease to financial data, GM guidance to financial data, NVIDIA trading plan to structured text.
- NVIDIA's 40 financial-reporting fragments are no longer text merely because the source Item is 15.
- Ouster's financial-statement page index becomes index/navigation.
- Actual exhibit tables remain exhibit lists.

### Phase 8 — Repair titles

Replace the unbounded/stateless title lookup with ranked candidates using the table element plus recently emitted extraction context.

At this phase select only the fragment's native title evidence. Do not inherit from the previous table yet; continuation has not been established. A missing native title remains neutral input to the linker, not a reason to reject a continuation.

Title priority:

1. Explicit `<caption>`.
2. A single full-width pre-header title row inside the table.
3. The nearest preceding caption sentence containing cues such as `following table`, `table below`, `summarizes`, `presents`, `were as follows`, or `was as follows`.
4. The nearest meaningful heading/subheading in the same document region.
5. After Phase 9 confirms a continuation, an effective title inherited from that logical table.

For a multi-sentence paragraph, extract the final sentence matching a caption cue. Do not store the entire preceding paragraph merely because its final sentence introduces a table.

Reject:

- configured company name or `COMPANY AND SUBSIDIARIES` boilerplate;
- `NOTES TO ... FINANCIAL STATEMENTS`, with or without `(Continued)`, as the sole title;
- page labels and page numbers such as `F-14` or `66`;
- `Table of Contents`;
- unit-only or period-only strings;
- flattened text from a previous table;
- an incomplete candidate ending in a conjunction/preposition;
- long prose without an explicit table-caption cue.

Assign titles to all structured retained table kinds, not only tables already classified as `data`.

Persist:

```text
title
title_source = html_caption | internal_title_row | prose_caption | heading | inherited | none
title_source_block_id
title_source_raw_cell_ids (for an internal table title; otherwise empty)
title_source_locator (block ID, caption XPath, or table/raw-cell locator)
title_confidence
title_quality_status = accepted_caption | accepted_heading | accepted_internal | inherited | missing
rejected_title_candidates = bounded nearest candidates with source and reason codes
```

Keep at most the nearest five rejected candidates per fragment so diagnostics do not duplicate unbounded filing prose. When no trustworthy title exists, keep `title = null`, record `title_source = none`, set `title_quality_status = missing`, and retain the rejection reasons. A null with explicit provenance is better than a misleading company/page header.

Before linking, retain a compact `native_context` with the native title/source, native header mode/signature, native unit signature, provisional kind/reasons, and the fields that are missing. After linking, top-level title/header/unit fields are the effective values used by rendering and downstream consumers. Record any inheritance explicitly in `inherited_context` and the existing source-block/raw-cell provenance fields. Never overwrite or relabel native evidence as if it occurred in the continuation fragment.

Compute `header_signature` as SHA-256 of canonical JSON containing the native source-only header paths plus logical lane roles; compute `unit_signature` from the native logical-unit vector. Canonical JSON uses UTF-8, sorted object keys, compact separators, and original list order. `native_context.missing_fields` uses only `title`, `header`, and `units`. `inherited_context` stores a nullable source block for each of those fields. These signatures are comparison aids; full effective arrays and raw provenance remain canonical.

Stop condition: no financial table title equals a company/page header or generic continued-notes header; every caption-cue fixture receives the caption sentence; all remaining null titles are reportable and manually reviewable.

### Phase 9 — Link continuation fragments

Keep each source HTML fragment as its own processed block. Link fragments with `logical_table_id`; do not mutate raw evidence or collapse processed blocks.

Add bounded continuation state to `ExtractionContext`:

```text
last logical table ID
last table block ID
last title and source
last header paths/signature
last logical width and lane signature
last units
last section and document region
meaningful blocks/headings seen since the last table
```

A fragment may reuse the previous `logical_table_id` only when all mandatory conditions hold. Evaluate them from the fragment's native geometry/context and the previous logical table's effective signature; do not require values that could exist only after inheritance:

- same literal section, or a recognized page-boundary continuation in the same effective region;
- no meaningful new heading or different-table caption intervened;
- no conflicting explicit title;
- no conflict between provisional table-kind families when both fragments have enough native evidence; a missing/low-confidence provisional kind is neutral;
- no conflict between native units when both fragments expose them; missing native units are neutral;
- compatible native logical header/lane signature or high row-label overlap; a missing native header is allowed only with another strong positive continuation signal;
- at least one positive continuation signal.

Positive continuation signals:

- explicit `(Continued)` cue;
- missing title/header on the later fragment with compatible prior schema;
- repeated headers with complementary row sequences;
- high row-label overlap with different period headers;
- immediate adjacency separated only by empty/page-furniture elements.

Never link solely because titles match, widths match, or tables are adjacent.

Once the link decision is final:

1. Inherit only missing title/header/unit fields and record their source block/raw-cell IDs.
2. Build the effective header and unit signatures without changing the fragment's native-context record.
3. Run final table classification from the fragment's logical content, document region, native evidence, and explicitly inherited context.
4. If finalization exposes a hard contradiction that native evidence could not reveal, reject the link, discard inherited values, restore native fields, allocate a new logical ID, and finalize that independent fragment once.
5. Render only after this final metadata/classification pass.

This is a bounded two-stage decision, not an iterative loop: provisional evidence -> link/inherit -> one final classification/validation.

Persist:

```json
{
  "logical_table_id": "MBLY-2025-TABLE-0181",
  "table_fragment_index": 2,
  "is_continuation": true,
  "continued_from_block_id": "MBLY-2025-001170",
  "title_source": "inherited",
  "header_source_block_id": "MBLY-2025-001170",
  "continuation_reasons": ["adjacent", "row-label overlap", "different period"]
}
```

#### 9.1 Composition modes at chunk time

Normalize fragments independently first. Then use one of three modes:

- `vertical`: header paths match and body rows are complementary. Deduplicate repeated headers and append rows.
- `horizontal`: normalized row labels are unique and have high overlap, while period/header groups are complementary. Outer-join on row-label key and preserve each period's columns.
- `compound`: linkage is certain but safe rectangular merge is not. Render ordered valid Markdown subtables inside one logical-table chunk.

Initial horizontal safety threshold:

```text
at least 80% normalized row-label overlap
AND unique nonempty row labels within each fragment
AND no conflicting value for the same header path
AND disjoint/complementary period header groups
```

If any condition fails, use `compound`; do not force a merge.

Stop condition: all positive continuation fixtures share a logical table ID and produce one table chunk, while negative adjacent-table fixtures remain separate.

### Phase 10 — Render only logical tables

Replace current pipe-joined physical rendering with one canonical Markdown renderer used by processed table text and table chunks.

Required rendering:

```markdown
The following table summarizes wholesale vehicle sales by our Automotive operations (vehicles in thousands)

| Line item | 2025 — Volume | 2025 — Percent |
| :--- | ---: | ---: |
| GMNA | 3,296 | 86.8% |
```

Rules:

- Render title, optional units summary, then table.
- Render nonempty `logical_header_context` once between units and the Markdown table as `Header context: ...`.
- Render from logical header paths/headers and logical rows only.
- Always emit a Markdown header delimiter row.
- Right-align numeric, currency, percentage, year, and date columns; left-align text labels.
- Escape literal `|` characters.
- Replace embedded newlines with safe spaces or `<br>` consistently.
- Pad rows to exact logical width.
- Generate neutral display headers for source-headerless tables without changing `header_mode` or pretending they came from the filing.
- Flatten multi-row headers using parent-to-child paths.
- Resolve duplicate display headers first with the provenance-marked semantic role/unit component from Phase 5. If duplicates still remain, add a deterministic display-only ordinal suffix; preserve source paths separately.
- Do not place unit-only rows in the body.
- For `compound` logical tables, render each fragment as a separate valid Markdown subtable beneath one logical title.
- Never fall back to physical-grid rendering in production output.

Stop condition: 100% of retained table chunks parse as equal-width Markdown with a delimiter row and no long empty-spacer runs.

### Phase 11 — Chunk one logical table

Change `src/chunking/chunk_documents.py` narrowly. Narrative chunking remains untouched.

Current one-block/one-table-chunk behavior becomes one-`logical_table_id`/one-table-chunk behavior. A logical table's fragments are normally consecutive among table blocks, though excluded navigation/page-furniture may sit between them. Buffer one candidate logical table at a time; if a previously closed logical ID appears again, fail validation rather than collecting nonlocal fragments across intervening semantic content.

1. Group table fragments by logical table ID while preserving first-fragment document order.
2. Flush narrative groups as current code does around table evidence. A navigation block still forms a hard boundary and produces no chunk.
3. Compose linked fragments using the safe mode from Phase 9.
4. Preserve every contributing block ID, HTML table ID, fragment index, and source anchor.
5. Use `source_group = table-{logical_table_id}`.
6. Keep the complete logical table even when it exceeds the 500-token narrative limit.

Required chunk concepts:

```json
{
  "chunk_schema_version": 3,
  "table_schema_version": 2,
  "table_heuristics_version": "sec-logical-v1",
  "chunking_config_sha256": "sha256:...",
  "source_processed_sha256": "sha256:...",
  "content_type": "table",
  "table_class": "data",
  "table_kind": "financial_data",
  "document_region": "financial_statement_notes",
  "effective_section_path": ["Item 15 — Exhibits and Financial Statement Schedules", "Financial statement notes"],
  "logical_table_id": "MBLY-2025-TABLE-0181",
  "composition_mode": "horizontal",
  "table_fragment_count": 2,
  "fragment_block_ids": ["MBLY-2025-001170", "MBLY-2025-001171"],
  "html_table_ids": [],
  "title": "Debt Investments",
  "header_mode": "inferred",
  "logical_header_paths": [],
  "logical_header_context": [],
  "logical_header_context_source_raw_cell_ids": [],
  "logical_column_headers": [],
  "logical_column_header_metadata": [],
  "logical_column_units": [],
  "logical_column_unit_metadata": [],
  "logical_rows": [],
  "logical_row_roles": [],
  "logical_cell_states": [],
  "logical_cell_sources": [],
  "logical_body_rowspans": [],
  "logical_row_sources": [],
  "logical_fragments": [],
  "text": "valid logical Markdown"
}
```

`logical_row_sources` identifies at least source block ID, fragment index, and source row index. `logical_cell_sources` and `logical_cell_states` have exactly the same shape as `logical_rows`; each cell source entry retains source block ID, HTML table ID, fragment index, raw-cell IDs, and source row index. Carry `logical_body_rowspans` with coordinates remapped to the composed row/column space. For `compound`, `logical_fragments` is canonical and top-level unified rows may be null/empty; every fragment must retain all three provenance structures and satisfy logical-width invariants. For single, vertical, and horizontal modes, top-level logical rows and their same-shape cell provenance are canonical.

During one transition, `table_headers`, `table_rows`, and `column_units` may alias logical fields for older consumers. They must never alias the physical grid again.

Strict chunk input validation must reject a table block lacking current `table_schema_version` or logical fields. This intentionally forces all stale artifacts to be regenerated.

Every generated chunk, narrative or table, carries `chunk_schema_version = 3`, `chunking_config_sha256`, and `source_processed_sha256`. Table chunks additionally carry `table_schema_version` and `table_heuristics_version`. The stats file repeats these release-level values. This prevents a 500-token mapping from being selected solely because its size happens to match another artifact.

Stop condition: every included logical table ID maps to exactly one chunk; every fragment block ID appears in exactly one logical-table chunk; no unrelated table is merged.

### Phase 12 — Update table embedding input only

Change only the table accessor/policy in `src/embeddings/embed_chunks.py`.

- Read logical headers, rows, units, and linked fragments.
- Keep the policy of embedding section/region, title, units, headers, and descriptive row labels rather than every numeric value.
- Include period/measure header paths so queries can retrieve the correct table.
- Include `logical_header_context` exactly once when common-prefix elision is used.
- Exclude spacer artifacts because none should exist in logical schema.
- Preserve narrative embedding text behavior.
- Preserve model selection, prefixes, normalized encoding, NPZ structure, chunk ordering, hashes, and atomic writes.
- Update `TABLE_EMBEDDING_POLICY` to name logical schema version 2.
- Set embedding manifest `schema_version = 3` and record `chunk_schema_version = 3`, `table_schema_version = 2`, `table_heuristics_version`, chunking-config SHA-256, logical-table policy, renderer policy, and embedding-text policy.

After regeneration, audit all remaining truncated table inputs. A remaining truncation is acceptable only when caused by a genuinely large logical table, not sparse HTML layout.

Stop condition: embedding unit tests use logical fields; manifests bind vectors to the new chunk hash/table policy; stale vectors cannot validate against regenerated chunks.

### Phase 13 — Replace table QA metrics

Keep existing `table_context_accuracy` only as a serialization smoke test and rename/document it as `table_context_copy_completeness`. It cannot be used as a quality score.

Add reusable processed-table metrics and expose them in chunk stats/benchmark reports where applicable.

#### Source-coordinate and physical-display empty-cell density

```text
source-coordinate density = empty source-coordinate positions /
                            (included source rows * source_coordinate_width)

physical-display density = empty positions in physical_rows /
                           (len(physical_rows) * physical_display_width)
```

`source_coordinate_width` is the maximum exclusive end coordinate across raw cells in included rows. Use the origin-preserving source-coordinate grid reconstructed from `raw_cells`, not `physical_expanded_rows`, so a colspan does not fabricate repeated non-empty values. `physical_display_width` is the width of the current globally pruned rectangular `physical_rows` view and must equal the length of `physical_source_column_indexes`. The row universe excludes wholly empty source rows in both calculations. Report both; high physical sparsity is expected in SEC HTML.

Do not serialize an ambiguous `physical_width` field in table-schema-v2 diagnostics. Historical corpus values and every `X -> Y` fixture in Section 13 use `physical_display_width -> logical_width`; source-coordinate width may be larger and is reported separately.

#### Logical empty-cell density

Calculate on data-bearing logical rows, excluding header rows and one-cell section-label rows:

```text
empty logical cells / (data-bearing rows * logical width)
```

#### Compression

Report both:

```text
source_coordinate_to_logical_width_ratio = source_coordinate_width / logical_width
physical_display_to_logical_width_ratio = physical_display_width / logical_width
logical_to_physical_display_fraction = logical_width / physical_display_width
```

#### Logical width consistency

Share of headers, unit arrays, and rows matching `logical_width`. Must be 100%.

#### Header coverage

For non-headerless tables, share of non-label value columns with at least one meaningful source-derived header label. Headerless tables are reported separately, not counted as failures.

#### Raw-cell accounting coverage

Share of nonempty raw cells mapped to body/header/title/unit evidence or explicitly ignored with a documented reason. Must be 100%.

#### Standalone marker count

Logical cells exactly equal to a currency or percent marker. Must be zero.

#### Title quality

Counts/rates for null, company-header, generic-notes, page-label, overlong, truncated-looking, caption-derived, heading-derived, and inherited titles.

#### Continuation quality

Counts for candidate pairs, accepted links, rejected links with reasons, orphaned `(Continued)` fragments, logical table count, and physical fragment count.

#### Classification quality

Accuracy against human-labeled real fixtures. Never compare heuristics to their own outputs.

#### Markdown validity

Share of rendered tables with one delimiter row per subtable and identical column counts in header, delimiter, and rows. Must be 100%.

Add a small read-only corpus audit entry point, preferably `src/filings/audit_tables.py`, that reads processed blocks and prints or writes machine-readable per-company/aggregate metrics. It must not modify raw or processed data.

Update `chunk_statistics()` and `benchmark_chunking.py` to report logical-table counts and new structural metrics. Do not let benchmark language claim semantic table quality from copied strings.

Stop condition: a deliberately sparse/misaligned synthetic table fails new gates even though all source text is copied.

### Phase 14 — Fix the notebook consumer

The parser defect and notebook defect are independent.

In `notebooks/experiment.ipynb`, code cell 7 currently converts `table_headers` and `table_rows` to strings, removes brackets with regex, and splits rows/headers on commas.

For the first GM record, native nested data has two 36-cell header rows and three 36-cell body rows. String splitting invents 73 headers and row widths of 36 to 39. It splits `3,296`, `Years Ended December 31,`, and comma-bearing job titles.

`pd.read_json(..., lines=True)` already returns nested Python lists. Consume them directly:

```python
headers = row["logical_column_headers"]
rows = row["logical_rows"]

assert isinstance(headers, list)
assert isinstance(rows, list)
assert all(isinstance(values, list) for values in rows)
assert all(len(values) == len(headers) for values in rows)

display_headers = [value or f"Column {index + 1}" for index, value in enumerate(headers)]
frame = pd.DataFrame(rows, columns=display_headers)
```

For compound tables, construct one DataFrame per `logical_fragments` entry or use composed Markdown. Do not use `str()`, regex parsing, comma splitting, or `literal_eval` for nested JSON arrays. If an external transport serializes a field, parse it once with `json.loads`.

Filter table chunks with `content_type == "table"`, not merely `table_class.notna()` on a Pandas union schema.

The notebook's `to_json(..., orient="records", lines=True)` export is not the source of extra columns; it preserves arrays correctly. `notebooks/GM_tables.jsonl` contains upstream sparse grids and should be regenerated only after the logical pipeline is complete.

Stop condition: a GM DataFrame preserves `3,296` as one cell and comma-bearing captions/titles do not change column count.

## 8. Per-file change map

This section defines where changes belong and where they do not.

### `src/filings/dom_processing.py`

Change only:

- Add the shared visible-text collector/boundary joiner.
- Make `text_excluding_descendants()` use it.
- Route paragraph/heading/table-cell text through it.
- Preserve existing normalization and hidden/XBRL behavior.

Do not reorganize DOM cleanup or heading detection generally.

### `src/filings/table_processing.py`

Keep:

- `parse_table_span()`.
- Nested-table-safe row enumeration.
- Slot/span reconstruction.
- Raw cells and formatting evidence.
- Semantic bullet grouping.
- Existing TOC rules as inputs to richer classification.

Add or replace narrowly:

- Typed value analysis.
- Raw row profiles.
- Explicit/inferred/headerless detection.
- Logical affix bundling and lane inference.
- Header projection.
- Logical unit inference.
- Fine-grained classification.
- Ranked title-candidate helpers.
- Canonical logical Markdown rendering.
- Structural metric helpers.

Do not introduce issuer-specific `if ticker == ...` branches. Fixture-driven generic rules are required.

### `src/filings/block_extraction.py`

Change:

- Extend `ExtractionContext` with document region, stable table ordinal, recent title context, and prior logical-table state.
- Update region state on high-confidence headings.
- Assign stable HTML/logical table IDs.
- Orchestrate physical extraction -> row analysis -> logical normalization -> units/classification/title/continuation -> rendering.
- Persist physical and logical fields.
- Assign title/header/unit context to all retained structured tables.
- Preserve current list conversion behavior.

Do not rewrite generic ordered traversal or deterministic block indexing.

### `src/filings/preprocess_filing.py`

Change:

- Add strict table-block validation for schema version, widths, provenance accounting, and JSON serializability.
- Keep atomic writes unchanged.
- Optionally surface a concise table-quality summary after processing.

Do not change acquisition, raw discovery, metadata loading, or overwrite safety.

### `src/chunking/chunk_documents.py`

Change:

- Require current table schema.
- Group by logical table ID.
- Compose fragments conservatively.
- Render/copy logical fields.
- Preserve all fragment provenance.
- Add hard table-chunk invariants.
- Replace/rename misleading metrics.
- Include processed-source hash and schema versions in stats.

Do not change narrative splitting, section-boundary behavior, tokenizer, token-span algorithm/schema, or configured/actual-overlap reporting. Numeric span values and possibly boundary-sensitive chunk IDs/counts may be regenerated when Phase 1 corrects narrative text; review those diffs rather than forcing stale offsets to remain.

### `src/chunking/benchmark_chunking.py`

Change:

- Report logical table count rather than HTML fragment count.
- Include new structural table metrics.
- Label context-copy completeness honestly.
- Correct generated reproduce commands so they pass configured company names.

Do not rerun or retune narrative chunk-size experiments as part of this repair unless a logical-table change reveals a direct bug.

### `src/embeddings/embed_chunks.py`

Change only:

- Logical-table accessor and table embedding text.
- Policy/schema metadata in manifests.
- Strict rejection of stale table chunks.

Leave model behavior and vector persistence unchanged.

### `tests/test_table_processing.py`

Keep raw physical assertions and add logical assertions. Update tests that currently encode broken physical behavior as expected semantic behavior.

Specifically:

- The lease-row test may still assert physical `['2026', '$', '20']`, but must separately assert logical `['2026', '$20']`.
- The mixed-unit test must no longer call five physical positions the logical unit vector.
- The continuation test must assert a shared logical table ID and one chunk, not merely two independently rediscovered titles.
- The bold-header test must prove style signals affect the decision, not just that they were recorded.

### `tests/test_chunk_documents.py`

Add:

- Current logical-schema input.
- One logical table from multiple fragments.
- Vertical, horizontal, compound, and negative-link cases.
- Valid Markdown delimiter/escaping.
- Strict stale-schema rejection.
- All-fragment provenance.
- Headerless rendering.

Keep existing narrative tests and expectations except where a test explicitly encodes the broken `<br>`/block-boundary concatenation; add regression tests proving unrelated narrative text and ordering remain unchanged.

### `tests/test_embed_chunks.py`

Update only the table fixture to logical schema. Continue asserting that descriptive labels and headers are embedded while raw numeric cells are not indiscriminately appended.

### `notebooks/experiment.ipynb`

Replace the string parser with direct list consumption only. Do not turn the notebook into production pipeline code.

### `notebooks/compare_embeddings.ipynb` or a replacement evaluator

The current notebook hard-codes embedding manifest schema version 2 and selects gold mappings primarily by chunk size. Both become invalid in this migration.

Change only the evaluation/artifact-loading boundary:

- Load the versioned evaluation JSON/JSONL exported in Phase 15.7 instead of treating notebook cells as the authoritative dataset.
- Require embedding manifest schema version 3, chunk schema version 3, table schema version 2, exact `source_chunks_sha256`, chunking-config hash, table-heuristics version, and embedding-input policy before evaluation.
- Select a gold mapping by exact artifact/evidence identity, never `chunk_size` alone.
- Retain the current query encoder, semantic similarity, Recall@k/MRR calculations, per-category reporting, and embedding-to-chunk source-hash protection.
- Save the new table-v2/chunk-v3 BGE baseline separately from historical results.

If this logic is moved into a small reproducible evaluator under `src/evaluation/`, the notebook may call it; do not maintain two divergent validation/evaluation implementations.

### New focused files allowed

The repair may add:

- `tests/fixtures/tables/...` for minimal real SEC excerpts.
- `tests/fixtures/tables/manifest.json` for provenance and expected outcomes.
- `tests/test_sec_table_fixtures.py` for cross-company golden cases.
- `src/filings/audit_tables.py` for read-only corpus QA.

Do not add a new parsing framework or service layer.

### Suggested internal API boundaries

Keep the implementation testable through small deterministic functions. Names may follow existing project conventions, but the responsibility and dependency direction should remain equivalent to:

```python
collect_visible_text(node, *, excluded_descendant_tags=()) -> str
analyze_cell_lexically(text) -> CellValueAnalysis
refine_cell_kind(analysis, *, row_profile, column_profile, header_tokens) -> CellValueAnalysis
build_row_profiles(structure) -> list[RowProfile]
detect_table_header_rows(structure, row_profiles) -> HeaderDetection
normalize_logical_columns(structure, header_detection, row_profiles) -> LogicalTableFragment
project_logical_headers(structure, logical_fragment, header_detection) -> HeaderProjection
infer_logical_column_units(logical_fragment, title_candidate) -> UnitInference
classify_table(structure, logical_fragment, region, title_candidate, *, provisional=False) -> ClassificationResult
select_table_title(table_node, recent_blocks, internal_title_rows) -> TitleResult
link_table_continuation(native_fragment_context, previous_effective_context) -> ContinuationResult
apply_inherited_context(fragment, continuation_result) -> LogicalTableFragment
render_logical_table(fragment_or_composition) -> str
validate_logical_table(fragment_or_composition, *, strict=True) -> None
```

Equivalent dictionaries/typed dictionaries are acceptable; introducing dataclasses is not itself a goal. Each result must expose its values, confidence, reason codes, and source provenance instead of relying on module globals.

The non-list table path must execute in this order:

```text
physical extraction and stable source IDs
→ lexical candidates, context-refined cell kinds, row profiles, raw layout evidence,
  and internal-title candidates
→ existing high-confidence semantic-bullet early branch when applicable
→ initial header decision
→ semantic logical lane normalization or the exact Phase 5.8 row-text fallback
→ one allowed header-boundary consistency correction
→ source-header projection
→ native bounded title selection and native logical unit inference
→ provisional content/region classification from native evidence
→ continuation decision and logical-table ID from native/provisional signatures
→ one inheritance pass for missing title/header/unit context
→ final unit inference and final content/region classification from effective context
→ logical rendering
→ block validation/emission
```

Run the existing high-confidence semantic-bullet detector immediately after physical extraction as an early branch and preserve its current list-item emission. Do not force bullet-layout tables through a financial-grid normalizer merely to make all paths look uniform. All other retained table kinds, including navigation and unknown, go through logical normalization so classification and QA use semantic rather than physical width.

The top-level HTML-table counter increments for every top-level source `<table>` encountered after the existing deterministic DOM cleanup, before classification or list conversion. That makes `html_table_index` independent of whether a table later becomes navigation, list items, or a retained table block. The fingerprint is SHA-256 over a documented canonical serialization of that cleaned table element; record the canonicalization version because XPath/ordinal alone can move if cleanup rules later change.

### Required heuristic constants and tie-breakers

Put heuristic values in one immutable configuration object/module and serialize `table_heuristics_version = sec-logical-v1`. Do not scatter anonymous numbers through conditionals. These are the initial defaults; change one only when a frozen fixture or reviewed corpus failure demonstrates why, then bump the heuristics version and update the labeled baseline.

```text
HEADER_SCAN_MAX_NONEMPTY_ROWS = 8
HEADER_MIN_OBJECTIVE_MARGIN_OVER_HEADERLESS = 2.0
HEADER_TIE_EPSILON = 0.25

LANE_INTERVAL_OVERLAP_COEFFICIENT = 0.50
LANE_MIN_SUPPORT_ROWS = 2
LANE_MIN_SUPPORT_FRACTION = 0.25

TITLE_LOOKBACK_MAX_MEANINGFUL_BLOCKS = 6
TITLE_CAPTION_MAX_CHARACTERS = 500
TITLE_HEADING_MAX_CHARACTERS = 200

CONTINUATION_MAX_INTERVENING_MEANINGFUL_BLOCKS = 0
CONTINUATION_ROW_LABEL_OVERLAP = 0.80
CONTINUATION_HEADER_PATH_SIMILARITY = 0.80

INDEX_MIN_RECORD_ROWS = 3
INDEX_PAGE_VALUE_FRACTION = 0.80
INDEX_TEXT_LABEL_FRACTION = 0.70
STRUCTURED_MIN_RECORD_ROWS = 2
STRUCTURED_SIGNATURE_SUPPORT_FRACTION = 0.70
```

For header scoring, calculate this signed score per row over the bounded leading window, using ratios in `[0, 1]` and excluding year tokens from ordinary numeric density:

```text
+ 3.0 * th_ratio
+ 2.0 * bold_text_ratio
+ 1.0 * centered_ratio
+ 1.0 * visible_bottom_border_ratio
+ 1.5 * colspan_or_group_header_ratio
+ 2.0 * period_or_year_group_ratio
+ 1.0 * unit_or_measure_vocabulary_ratio
- 3.0 * repeated_body_signature_similarity
- 2.0 * nonyear_numeric_currency_range_density
- 1.0 * label_followed_by_values_ratio
```

For candidate boundary `b`, the objective is the sum of row scores before `b` minus the sum after `b`. Candidate `b = 0` is the headerless baseline; do not allow a boundary that consumes every usable row. Select a nonzero boundary only when its objective exceeds headerless by at least 2.0. Within 0.25, prefer fewer header rows, then `headerless`. Explicit `<th>` is evidence through the score, not an unconditional override. Persist the chosen boundary, runner-up, margin, features, and reason codes.

Lane interval overlap is `intersection_width / min(interval_widths)`. A body-supported lane normally needs both two source rows and 25% of eligible data rows. Exceptions are a one-row table with leaf-header support and a separate same-row atom that cannot legally share an existing lane; mark either as fallback with a reason. Header projection does not create a body lane by itself when it would collapse two co-occurring atoms. Existing center/start tie-breakers in Phase 5 remain final.

Title candidates are limited to the six nearest meaningful emitted blocks in the same region. Apply source priority first, then nearest distance, then the shortest complete cue-bearing sentence; candidates beyond the character limits are rejected unless the exact caption sentence can be extracted within the limit. Do not search past an intervening retained table, explicit Item heading, or region boundary.

Continuation accepts zero intervening meaningful blocks; ignored page furniture does not count. Row-label overlap is intersection over the smaller unique normalized label set. Header-path similarity is multiset Jaccard after source-text normalization. Missing native title/unit/header evidence is neutral, conflicting explicit evidence rejects, and a score tie rejects linkage. Horizontal composition additionally uses the 80% rule already defined in Phase 9.1.

Classification uses the precedence in Phase 7 and named predicates rather than an opaque probability cutoff. Index predicates use the constants above. Exhibit classification requires explicit exhibit headers or at least two record rows with a repeated exhibit-identifier/description grammar. `financial_data` requires at least two record rows plus two independent evidence families among financial region/title/header terms, monetary/rate/percent values, scale/currency evidence, and period columns; a high-confidence consolidated-statement heading plus repeated value rows counts as the two families. `structured_text` requires at least two rows, rectangular logical width, and 70% support for one repeated record signature. Ties go to the earlier precedence kind; unresolved low evidence remains `unknown`. Diagnostic scores need not sum to one.

## 9. Detailed classification and normalization safeguards

### 9.1 Reason codes

Use stable reason codes in addition to human-readable messages. Recommended examples:

```text
index_page_column
index_item_links
exhibit_headers
financial_region
financial_terms
currency_values
percent_values
period_headers
repeated_record_grid
structured_name_date_grid
semantic_bullets
signature_layout
low_confidence
```

Tests should check decisive reason codes rather than complete free-form strings.

### 9.2 Source evidence cannot disappear

Every nonempty raw cell must end in one of these buckets:

```text
body logical cell source
logical header source
title source
unit source
intentional ignored source with reason
```

Allowed ignore reasons should be enumerated, for example:

```text
duplicate_span_projection
layout_spacer_with_text_equivalent_elsewhere
promoted_internal_title
promoted_unit_row
repeated_continuation_header
```

`layout_spacer_with_text_equivalent_elsewhere` must not become a loophole for arbitrary deletion. Store the equivalent raw-cell ID.

Each `ignored_raw_cells` entry has this exact shape:

```json
{
  "raw_cell_id": "...",
  "reason_code": "duplicate_span_projection",
  "equivalent_raw_cell_id": "...",
  "promoted_to": null,
  "note": null
}
```

`equivalent_raw_cell_id` is mandatory for duplicate/equivalent-text reasons and must reference an existing cell. `promoted_to` is one of `title`, `header`, `unit`, `repeated_continuation_header`, or null. A promoted cell should normally be recorded in its canonical provenance field; this audit entry explains only why it does not also appear as a body cell. Free-form `note` is optional and never substitutes for a reason code.

### 9.3 Section-label rows

Rows such as `Assets`, `North America`, `Foreign`, and `Reported as:` may have one text value and otherwise blank cells. Preserve them as logical body section rows with the label in the row-label column. Exclude them from logical empty-density calculations and numeric DataFrame calculations, but keep them in rendered evidence.

### 9.4 Repeated headers inside vertical fragments

When vertically composing fragments:

- Compare normalized header paths, not flattened strings alone.
- Allow period/page header repetition.
- Remove a repeated header only from composed body output.
- Retain it in source-fragment schema and provenance.
- Do not remove a body row that merely resembles a header without formatting/position evidence.

### 9.5 Footnotes

Do not strip footnote markers from display values or row labels. Detection may separate a suffix internally. Logical values must preserve `3,984*`, `China(a)`, and similar visible evidence. Header matching/continuation keys may use a normalized comparison key with a separately retained display label.

### 9.6 Dashes

Preserve the original visible dash in display text. Normalize dash variants only for typed matching and continuation-key comparison. A standalone dash is a missing-value marker; a dash between valid endpoints can be a range.

### 9.7 Dates versus amounts

Dates can make a structured table data-bearing without making it financial. Monetary/rate/financial-region evidence is required for `financial_data`; names/titles/actions/dates can support `structured_text`.

## 10. Logical-table chunk and citation provenance

Table citations will eventually need to identify evidence within a linked logical table. Preserve:

- Every processed `block_id`.
- Every `html_table_id`.
- Fragment order.
- Original source row index.
- Raw-cell IDs for each logical cell.
- Source anchor(s) and SEC URL.
- Title source block/fragment.
- Inherited header/unit source block.

Current source anchors are not table-unique: 213 table blocks have no anchor, only 67 distinct anchors are used by the remaining blocks, and one anchor is reused by many tables. `html_table_id` plus block ID therefore becomes the stable local source location; do not pretend a shared SEC anchor uniquely identifies a table.

Chunk-level `block_ids` must include every fragment. `source_anchors` should remain deduplicated but must not replace fragment IDs.

## 11. Validation behavior

### 11.1 Processed block validation

For a current table block, fail before serialization when:

- Schema version is missing/wrong.
- A logical-width invariant fails.
- A nonempty raw body cell is unexplained.
- A collision is unresolved.
- A logical column is entirely empty.
- A logical cell is a standalone currency/percent marker.
- Rendered Markdown width is inconsistent.
- Fragment index/logical ID is missing.
- A continuation points to a future or nonexistent block.

Unknown classification is not itself a serialization failure. A conservative `fallback_used` table may serialize only when every invariant and source-accounting rule passes; it remains a review warning. Unresolved/unsafe normalization fails serialization and therefore cannot reach chunking or embeddings.

`row_text_fallback` is valid only under the exact Phase 5.8 contract. Validation must reject that mode for financial, structured, index, or exhibit kinds and must reject any unaccounted nonempty source cell.

### 11.2 Chunk validation

Fail when:

- Stale table schema is encountered.
- One logical table ID maps to multiple chunks.
- Linked fragment block IDs are missing or duplicated.
- Logical widths are inconsistent.
- Logical cell-state/source matrices disagree with the logical row shape or reference absent raw cells.
- Markdown delimiter is absent/malformed.
- Table text is generated from physical rows.
- Top-level composed fields disagree with fragment fields/provenance.
- A composed logical cell lacks its contributing source block/fragment/raw-cell references, except an explicitly state-marked blank with no source cell.

Narrative length validation remains unchanged.

### 11.3 Embedding validation

Fail when a table chunk lacks current logical schema. Existing source-chunk hash, chunk ID order, vector shape, finite-value, normalization, and text-hash checks remain mandatory.

## 12. Quality gates

The parser repair is acceptable only when all hard gates pass.

### 12.1 Hard structural gates

- 100% of retained table fragments have `table_schema_version = 2`.
- 100% have stable HTML and logical table IDs.
- 100% of logical rows, header arrays, and unit arrays have consistent width.
- Zero entirely empty logical columns.
- Zero standalone `$`, `€`, `£`, `¥`, or `%` logical cells.
- 100% nonempty raw-cell accounting coverage.
- Zero unresolved normalization collisions.
- Zero unexplained unmapped nonempty raw body cells.
- 100% table Markdown validity.
- Every `row_text_fallback` is kind `layout` or `unknown`, is counted separately, and has a recorded manual review decision before promotion.
- Every included logical table ID produces exactly one table chunk.
- Every linked fragment appears exactly once in its logical-table chunk.
- All priority golden fixtures match exact expected class/kind, width, selected headers/rows, units, and context.

### 12.2 Corpus quality targets

- No more than 5% of `financial_data` plus `structured_text` logical tables may have greater than 50% empty density on data-bearing rows. Every exception must be listed and manually reviewed. Report `index_navigation`, `exhibit_list`, and `layout` separately rather than forcing presentation-oriented tables to satisfy a financial-grid density target.
- Zero financial titles may be a company/page header or generic `NOTES ... (Continued)` string.
- Every caption-cue table must receive its caption sentence.
- Every remaining missing title must report `title_source = none` and be included in the audit report.
- All high-confidence continuation fixtures must link; all negative controls must remain unlinked.
- All four current unknown tables must resolve as specified.
- All 40 labeled NVIDIA financial-region fragments must be classified as financial data rather than Item-15 text.
- Ouster's labeled statement index must be navigation, not financial data.
- Headerless fixtures must remain explicitly headerless and retain all body rows.
- Truncated table embedding inputs must be remeasured; every remaining case must be explained by real logical content.

### 12.3 Regression gates for working behavior

- All preexisting raw span/rowspan/colspan assertions remain valid.
- Hidden content and visible Inline XBRL behavior remain valid.
- Bullet-table list conversion remains valid.
- Navigation exclusion remains valid.
- Narrative block count/order/sections must not shift. Localized narrative text and span values may change only under Phase 1 boundary rules; table regrouping can shift sequential chunk IDs, and corrected text can rarely shift a chunk boundary/count, but the splitting logic itself remains unchanged.
- Embedding vectors remain normalized and manifests remain hash-verifiable.

## 13. Cross-company real SEC fixture matrix

Raw table ordinals/XPaths below were measured after applying existing cleanup. The displayed `table N` values are the audit script's zero-based positions; the new persisted `html_table_index` and ID contract in Phase 3 is explicitly one-based. Store both conventions unambiguously in the fixture manifest with accession/source metadata, and use the XPath/fingerprint to guard against off-by-one mistakes.

| Company | Current source | Cleaned raw locator | Exact regression contract |
|---|---|---|---|
| APTV | `APTV-2025-000455` | table 13, `/html/body/div[584]/table` | Text boundaries repaired; variance table compresses 45 -> 9 logical columns; no standalone markers. |
| APTV | `APTV-2025-001126` | table 96, `/html/body/div[1427]/table` | 15 -> 4: Commodity, Quantity Hedged, Unit of Measure, Notional Amount; row contains `Copper`, `77,871`, `pounds`, `$415`; kind `financial_data`. |
| AUR | `AUR-2025-000831` | table 34, `/html/body/div[1031]/table` | 36 -> 7: label plus Amount/Percent for 2025, 2024, 2023; units alternate USD millions and percent; period/year/measure rows are headers. |
| F | `F-2025-001358` | table 115, `/html/body/div[2627]/table` | Lease schedule 21 -> 7; label, 2026-2030, Total; financial despite Item 16; title/header preserved. |
| GM | `GM-2025-000016` | table 9, `/html/body/div[83]/table` | 36 -> 7; GMNA row is `GMNA, 3,296, 86.8%, 3,464, 86.4%, 3,147, 83.5%`; title is caption sentence, not whole paragraph. |
| GM | `GM-2025-000021` | table 10, `/html/body/div[91]/table` | 54 -> 10: row label plus Industry/GM/Market Share for three years; `North America` remains a body section row. |
| GM | `GM-2025-000233` | table 18, `/html/body/div[502]/table` | 6 -> 2; `$10.3-11.7`, `2.6-3.2`, `$13.0-15.0` recognized; kind financial. |
| GOOGL | `GOOGL-2025-000996` | table 130, `/html/body/div[1150]/table` | 6 -> 2; maturity/year-value rows retained; `header_mode = headerless`; first row is not consumed as a header. |
| MBLY | `MBLY-2025-001170`, `MBLY-2025-001171` | tables 180-181, `/html/body/div[135]/div[2]/table[3]` and `[4]` | Each 18 -> 7; both share one logical table ID/title; horizontal or safe compound composition yields one chunk with both periods. |
| NVDA | `NVDA-2026-000541` | table 19, `/html/body/div[718]/table` | 18 -> 6; kind `structured_text`; header uses formatting evidence; dates and `3,984*` preserved. |
| NVDA | `NVDA-2026-000601` | table 21, `/html/body/div[800]/table` | 18 -> 4; title `Consolidated Statements of Income`; financial despite Item 15; effective financial region recorded. |
| OUST | `OUST-2025-000630` | table 19, `/html/body/div[785]/table` | 6 -> 2; financial-statement/page list is `index_navigation`, not financial data; page values 59-65 preserved. |
| QCOM | `QCOM-2025-000761` | table 58, `/html/body/div[978]/table` | 33 -> 7; maturity, amount, and effective-rate columns for each period; ranges preserved; financial region despite Item 16. |
| TSLA | `TSLA-2025-000678` | table 38, `/html/body/div[916]/table` | 45 -> 9; label plus Fair Value/Level I/II/III for two years; header rows 0-1; currency merged with amounts. |

### 13.1 NVIDIA financial-region gold inventory

The “40 NVIDIA fragments” gate is a human-labeled inventory, not a count derived from the repaired classifier. The current processed block IDs are:

```text
NVDA-2026-000601  NVDA-2026-000606  NVDA-2026-000611  NVDA-2026-000615
NVDA-2026-000620  NVDA-2026-000717  NVDA-2026-000719  NVDA-2026-000725
NVDA-2026-000740  NVDA-2026-000745  NVDA-2026-000755  NVDA-2026-000758
NVDA-2026-000764  NVDA-2026-000772  NVDA-2026-000774  NVDA-2026-000777
NVDA-2026-000785  NVDA-2026-000791  NVDA-2026-000793  NVDA-2026-000803
NVDA-2026-000806  NVDA-2026-000811  NVDA-2026-000824  NVDA-2026-000833
NVDA-2026-000849  NVDA-2026-000867  NVDA-2026-000871  NVDA-2026-000873
NVDA-2026-000880  NVDA-2026-000883  NVDA-2026-000888  NVDA-2026-000899
NVDA-2026-000921  NVDA-2026-000928  NVDA-2026-000930  NVDA-2026-000942
NVDA-2026-000944  NVDA-2026-000948  NVDA-2026-000955  NVDA-2026-000959
```

All 40 have expected `table_kind = financial_data`. They occur after the `Consolidated Statements of Income` heading and currently share SEC anchor `i82ea215a7c1f4862b6518f1348ddc832_97`, which proves the anchor alone is not a table identity. During Phase 0, resolve each historical block ID against the unchanged NVIDIA raw file to its zero-based audit ordinal, one-based `html_table_index`, cleaned XPath, and `cleaned-lxml-html-v1` fingerprint, and commit those identities/labels in the fixture manifest before changing classification code. Fail the fixture setup if the current block text cannot be matched uniquely. Pair the inventory with current block `NVDA-2026-000964`, expected `exhibit_list`, as the immediate negative region-boundary control.

The corpus gate loads this committed manifest and compares predictions with the human labels. It must never select “the 40 tables the current region detector calls financial,” which would validate the classifier against itself.

### 13.2 Positive continuation fixtures

- APTV `APTV-2025-001047` through `001049`, raw tables 86-88: vertical fragments with repeated headers.
- AUR `AUR-2025-000746` and `000747`, raw tables 20-21: period pair.
- GOOGL `GOOGL-2025-000884` and `000885`, raw tables 103-104: period pair.
- MBLY `MBLY-2025-001170` and `001171`, raw tables 180-181: period pair.
- OUST `OUST-2025-000797` and `000798`, raw tables 28-29: period pair.
- TSLA `TSLA-2025-000681` and `000682`, raw tables 39-40: period pair.

### 13.3 Required negative controls

Add at least:

- Adjacent unrelated tables with the same physical/logical width.
- Adjacent tables with the same generic heading but conflicting explicit captions.
- An exhibit table followed by a signature table.
- A body section-label row that resembles a one-cell continuation title.
- A repeated page/company header between otherwise unrelated tables.

These must not link.

## 14. Test plan

### 14.1 Unit tests for text collection

- `<br>` adds a word boundary.
- A hyphen across `<br>` does not gain a space.
- Inline formatting does not add a word boundary.
- Nested-table text remains excluded from outer cells.
- NBSP/zero-width/soft-hyphen normalization remains correct.

### 14.2 Unit tests for typed values

Add positive and negative cases for every type in Phase 2, including Unicode dashes, accounting parentheses, footnote suffixes, dates, exhibit numbers, file numbers, page numbers, and missing markers.

### 14.3 Unit tests for header roles

- Explicit `<th>` headers.
- Bold/bordered `<td>` headers.
- Multi-row period/year/measure headers.
- Opening-balance body row.
- Section-label body row.
- Headerless maturity table.
- Text-heavy structured body.
- Numeric-range header versus range body.

### 14.4 Unit tests for logical lanes

- Currency marker plus amount.
- Amount plus percent marker.
- Currency present in one row and omitted in the next.
- Missing value does not shift later columns.
- Row label with a larger colspan remains the row-label lane.
- Multi-row headers project to all covered lanes.
- Same-row independent values never collapse.
- Collision/unmapped diagnostics fail strict validation.
- Raw source IDs survive bundling.
- Lossless one-column row-text fallback for true layout/low-evidence unknown; fallback rejected for a malformed financial table.

### 14.5 Unit tests for classification

- Financial statement under Item 15.
- Financial statement under Item 16.
- Actual exhibit list under Item 15.
- Financial-statement page index.
- Structured insider-trading table.
- Semantic bullet list.
- Layout/signature table.
- Unknown retained.

### 14.6 Unit tests for title/context

- `<caption>` priority.
- Final caption sentence extracted from long prose.
- Company header rejected.
- Generic notes-continued header rejected.
- Internal title row promoted.
- Inherited continuation title.
- Conflicting explicit title prevents inheritance.

### 14.7 Unit tests for continuation/composition

- Vertical merge.
- Horizontal merge.
- Compound fallback.
- Repeated headers deduplicated only in composed output.
- One chunk per included logical ID; true `index_navigation` remains excluded by the current chunk configuration and must produce zero chunks.
- All block IDs and row sources preserved.
- Negative controls remain separate.

### 14.8 Unit tests for rendering

- Delimiter row exists.
- All widths are equal.
- Numeric alignment is valid.
- Pipe escaping works.
- Headerless display headers work.
- Duplicate flattened headers receive deterministic display-only names.
- No standalone marker columns remain.
- Compound fragments each form valid Markdown.

### 14.9 Corpus regression test

Add one slower, explicitly named corpus QA test or audit command that parses all ten frozen filings and compares key aggregate metrics to a checked-in expected baseline. Do not compare entire processed JSONL snapshots. The baseline should contain:

- Table fragment/logical-table counts.
- Class/kind counts.
- Maximum source-coordinate, physical-display, and logical widths.
- Empty-density distributions.
- Marker count.
- Normalization collision/unmapped count.
- Title-quality counts.
- Continuation-link counts.
- Fixture outcomes.

Any baseline update requires manual review; the test must not rewrite its own expected values.

## 15. Artifact migration and corpus regeneration

The parser change invalidates every downstream table artifact, even where a filing currently appears acceptable. Do not regenerate only the visibly broken companies: the schema, rendered table text, table chunk IDs, embedding inputs, and provenance rules change for the entire fixed corpus.

Regeneration is a release step, not part of heuristic development. First make the unit and frozen-fixture suites pass against temporary outputs. Then stage the entire corpus in a new directory and compare it with the current artifacts before replacing anything.

### 15.1 Preconditions

Do not start corpus regeneration until all of the following are true:

- The parser emits `table_schema_version: 2` for every retained table.
- Every table has both immutable physical evidence and a validated logical representation.
- The cross-company fixture matrix in Section 13 passes.
- Processed-block validation requires `table_schema_version = 2`; chunk config/chunks require schema version 3; embedding validation requires manifest schema version 3 and table schema version 2.
- The chunker refuses old physical-only table blocks instead of silently falling back.
- No collision, unmapped-value, or raw-cell-accounting hard error exists in a fixture.
- Narrative extraction and narrative chunking regression tests still pass.
- The selected 500-token/32-token narrative configuration has not changed.
- BGE-base v1.5 configuration has not changed: `BAAI/bge-base-en-v1.5`, requested revision `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`, current query prefix, empty document prefix, maximum sequence length 512, normalized `float32`, and dimension 768.
- The Mobileye retrieval evaluation source questions and expected evidence currently embedded in `notebooks/compare_embeddings.ipynb` have been exported to a versioned, non-generated JSON/JSONL file before any chunk IDs change. Preserve the existing multiple legacy/250/500 mappings as historical data.

### 15.2 Stage instead of overwriting

Use the existing CLI directory/output flags to build a parallel artifact tree. A representative shell sequence is shown below; the implementing agent should turn this into a documented, repeatable command or small orchestration script only if repetition makes manual execution unsafe.

```bash
RUN_ROOT="$(mktemp -d -p . table-v2-chunk-v3.XXXXXX)"
PROCESSED_V2="$RUN_ROOT/processed"
CHUNKS_V2="$RUN_ROOT/chunks"
EMBEDDINGS_V2="$RUN_ROOT/embeddings"
```

Use the exact ten company keys already defined in `src/filings/fetch_data.py`:

```text
aptiv
aurora
ford
general_motors
alphabet
mobileye
nvidia
ouster
qualcomm
tesla
```

Use the repository virtual environment explicitly (`.venv/bin/python`) or activate it first; the audited shell has no `python` executable. Then:

For each key, in that order or another explicitly recorded order:

1. Run `.venv/bin/python -m src.filings.preprocess_filing COMPANY --processed-directory "$PROCESSED_V2"` against the unchanged `data/raw` tree.
2. Validate the produced block file immediately. Stop the batch on a hard schema or provenance error.
3. Run `.venv/bin/python -m src.chunking.chunk_documents COMPANY --processed-directory "$PROCESSED_V2" --output "$CHUNKS_V2/TICKER/YEAR-10-K.chunks.jsonl"` with the checked-in `data/chunks/chunking-config.json`.
4. Validate chunk-schema-v3 output and write chunk-schema-v3 statistics next to the staged chunk file. If the current `--output` path does not place the stats file where expected, fix only that path-handling defect or invoke the statistics writer explicitly; do not copy an old stats file.
5. Run table QA over both the processed blocks and chunks and save its machine-readable report.
6. Do not embed yet. Complete and review the staged processed/chunked corpus first.

The commands must read the existing raw snapshot. They must not call SEC, select newer filings, alter metadata, or write under `data/raw`. The current CLIs choose the latest local snapshot, so before running the batch assert that the selected filename, accession number, and raw SHA-256 equal the approved ten-entry input manifest; fail instead of silently selecting a different local file.

### 15.3 Required staged comparison report

Before promotion, generate one report with a row for each ticker and totals for the corpus. It must show old versus new:

- filing path, accession number, filing date, and source URL;
- raw HTML SHA-256;
- narrative block count and table-fragment count;
- logical-table count;
- table kind/class counts;
- source-coordinate, physical-display, and logical width distributions;
- source-coordinate, physical-display, and logical empty-cell density distributions;
- count of standalone currency/percent marker columns;
- count of normalization collisions and unmapped non-empty cells;
- raw-cell accounting coverage;
- explicit, inferred, and headerless table counts;
- missing, rejected, explicit, internal, nearby, and inherited title counts;
- vertical, horizontal, compound, and unlinked-fragment counts;
- processed-block count, chunk count, narrative chunk count, and table chunk count;
- table-token length maximum and count exceeding the embedding model input limit;
- source-block and source-anchor coverage;
- all hard validation failures and warnings.

Expected changes are fewer logical columns, valid table Markdown, fewer table chunks where fragments compose, richer table metadata, and changed table embedding text. Unexpected changes requiring investigation include a material narrative-block count change, missing physical rows, changed filing metadata, loss of a retained structured table, or newly merged unrelated tables.

Do not use “new output is smaller” as proof of correctness. Compression is valid only when every non-empty physical source cell is accounted for by provenance or an explicit ignored-layout reason.

### 15.4 Manual review sample

The staged corpus is not promotable based on aggregate metrics alone. Review at minimum:

- every fixture in Section 13 in raw HTML, physical structure, logical structure, and rendered form;
- every table with a normalization warning;
- every `unknown` table;
- every horizontal merge and every compound composition;
- every logical table with no accepted title that is classified as `financial_data`;
- every logical table whose rendered or embedding text exceeds the model limit;
- a sample of explicit, inferred, and headerless tables for each issuer;
- a sample of tables classified in Item 15 or Item 16 for NVIDIA, Ford, and Qualcomm;
- all tables whose kind changed from data to index/navigation, or vice versa;
- at least one unaffected narrative passage per issuer that remains byte-identical, plus every changed narrative passage with a boundary-rule explanation; compare chunk text/spans semantically when corrected text shifts a boundary.

Record approval or rejection and the reason. A reviewer must be able to reach the raw XPath/source cells from every reviewed logical value.

### 15.5 Promotion and rollback

After the comparison and manual review pass:

1. Preserve a manifest of the old artifact filenames and hashes.
2. Promote the complete table-schema-v2 processed tree and chunk-schema-v3 tree as one aligned release; do not mix old blocks with new chunks.
3. Prefer a same-filesystem staged-directory rename/symlink switch for release-level atomicity. If only file-level replacement is practical, set a migration marker that causes every loader to refuse the corpus until all expected approved hashes are present; fail if any expected ticker is missing.
4. Regenerate chunk-schema-v3 statistics during promotion; never retain the old `table_context_accuracy` result as the main table-quality signal.
5. Verify the promoted file hashes against the approved staged hashes.
6. Keep the old manifest long enough to identify and roll back a bad release. Do not preserve duplicate raw HTML because raw files are already immutable.

Do not commit generated vector `.npz` files if repository policy ignores them. Do commit the same reproducibility metadata that is currently versioned, plus the new parser/table schema version and source hashes required to prove alignment.

### 15.6 Re-embedding

All ten BGE-base artifacts must be regenerated after the chunk-schema-v3 files backed by table-schema-v2 blocks are promoted. Re-embedding only Tesla is no longer sufficient because table embedding text changes for every issuer.

For each ticker:

1. Load and strictly validate the promoted chunk-schema-v3 file and its declared table schema version 2.
2. Build table embedding text from the logical accessor defined in Phase 12.
3. Record the SHA-256 of the promoted chunk file and the ordered embedding-input text hashes.
4. Encode with `BAAI/bge-base-en-v1.5` at requested revision `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`, preserving the current query/document prefixes, batch semantics, `float32` output, 768 dimensions, and L2 normalization.
5. Validate count, dimension 768, finite values, norm tolerance, ordered chunk IDs, output hash, and source-chunk hash with existing integrity checks.
6. Write embedding manifest schema version 3 with `chunk_schema_version: 3`, `table_schema_version: 2`, `table_heuristics_version`, chunking-config hash, logical-table policy/version, and table-render/embedding policy names.
7. Report truncated inputs by content type and table kind. The current audit found 29 truncated table inputs; the new count must be reviewed, not assumed to be zero.

If a complete logical table is too long for the embedding model, do not split the stored/citation table merely to eliminate truncation. First keep the complete table in its chunk, then use the existing compact descriptive table embedding policy. Any later multi-vector table representation is a separate retrieval experiment and is outside this repair.

### 15.7 Evaluation-label migration

Table composition and corpus-wide rechunking can change chunk IDs. Old Mobileye labels must not be applied to new chunks by matching ordinal IDs.

Migrate labels using evidence identity:

1. Preserve every original evaluation question, category, answerability label, and evidence note.
2. Resolve each old gold chunk to its source block IDs, source anchors, raw table IDs, and quoted evidence.
3. Resolve that evidence to table-schema-v2 `logical_table_id` values or narrative source spans.
4. Generate candidate new chunk IDs from those stable evidence links.
5. Confirm that the expected evidence text/value is present in each candidate.
6. Require manual review for one-to-many, many-to-one, missing, or text-only matches.
7. Save the mapping with old chunk ID, new chunk ID, evidence identifier, matching method, confidence, and reviewer decision.
8. Version the migrated dataset rather than overwriting the legacy mapping without a record.

The versioned evaluation record should include at least `id`, `category`, `query`, original artifact/config label, old relevant chunk IDs, evidence type, source block IDs, logical table IDs or narrative spans, expected evidence excerpt/value, new relevant chunk IDs, matching method, confidence, and review state. Do not leave the only authoritative copy in a notebook cell.

Recompute the BGE-base semantic baseline after migration. The currently reported Mobileye retrieval metrics are historical results for the previous chunk/embedding artifacts; do not present them as table-v2/chunk-v3 results. Compare the new baseline by question type, especially single-table and multi-chunk questions, and inspect every regression before beginning reranking.

### 15.8 Release ordering

The required order is:

```text
parser and schema tests
→ all-ten staged processed blocks
→ all-ten staged chunks and table QA
→ comparison and manual review
→ processed/chunk promotion
→ all-ten BGE-base re-embedding
→ embedding integrity audit
→ Mobileye label migration
→ new semantic-retrieval baseline
→ only then resume the bounded Mobileye reranking pilot
```

Never build a persistent vector index from a partially migrated corpus. The future index manifest must refer to one approved table-v2/chunk-v3 release and its matching all-ten manifest-v3 embedding release.

## 16. Documentation updates after implementation

The implementation agent must update documentation only after measured table-schema-v2, chunk-schema-v3, and embedding-manifest-v3 artifacts exist. Do not copy the targets in this plan into README or reports as if they were results.

Update these statements where present:

- `README.md`: replace the stale nine-current/one-Tesla exception with the actual all-corpus migration state; document physical versus logical table evidence and the new QA command.
- `ARCHITECTURE.md`: replace “one table block maps to one table chunk” with “one included non-navigation logical table maps to one table chunk and may contain multiple source HTML fragments; index/navigation tables remain processed but excluded”; document schema versioning and provenance.
- `ROADMAP.md`: mark parser repair, all-ten regeneration, re-embedding, and evaluation-label migration complete only when their gates pass; keep retrieval/reranking pending until then.
- `docs/CLEANING_AND_CHUNKING_NOTES.md`, `docs/CLEAN_CHUNK_REPORT.md`, `CHUNKING_REPORT.md`, and any Serbian report: distinguish historical results from the new measurements and include dates/artifact hashes.
- Notebook narrative: state that nested lists are consumed directly and remove any claim based on comma-split reconstruction.

Every updated numeric claim must come from a checked-in stats/QA artifact or reproducible command. Use the actual run date and hashes. Do not erase the historical reason for the migration.

## 17. Behavior that must remain unchanged

This repair is intentionally narrow. Unless a failing regression demonstrates direct coupling, do not alter:

- SEC acquisition, user agent behavior, accession selection, or the “latest normal 10-K, not 10-K/A” rule;
- the ten-company corpus or current frozen raw files;
- raw metadata semantics or source URLs;
- removal of `script`, `style`, `noscript`, hidden Inline XBRL, and known page furniture;
- preservation of visible Inline XBRL text;
- narrative block types, narrative section-path behavior, and narrative order; only localized Phase 1 boundary text corrections are allowed;
- the selected recursive narrative chunking strategy, 500-token limit, 32-token configured overlap, separator order, and tokenizer revision;
- narrative source character/token span fields and calculation algorithm; regenerate their numeric values from corrected text rather than preserving stale offsets;
- the policy that a complete logical table may exceed the narrative size limit;
- BGE-base v1.5 model selection, model revision, input prefixes, vector dimension, dtype, normalization, hashing, or manifest integrity checks;
- embedding text for narrative chunks;
- current atomic-write/no-overwrite protections, except for adding equivalent safeguards to new QA artifacts;
- acquisition/preprocessing separation;
- future retriever, reranker, prompt, generation, tool-calling, or UI design.

Do not add pandas, a dataframe abstraction, a generic table-extraction service, an OCR pipeline, an LLM classifier, hybrid retrieval, Graph RAG, or agentic repair to solve this parser defect. The required evidence is already in the SEC DOM and can be handled deterministically with bounded heuristics and explicit diagnostics.

## 18. Implementation checklist for the coding agent

Work in this dependency order. A phase is complete only when its tests and relevant real fixtures pass.

- [ ] Freeze the raw HTML fixture slices and expected physical evidence.
- [ ] Add a table schema-version constant and centralized logical-table field accessors.
- [ ] Repair boundary-aware visible-text collection without changing broad DOM removal.
- [ ] Add Unicode/footnote normalization and typed value recognition.
- [ ] Preserve and enrich physical-cell evidence, identities, spans, and source intervals.
- [ ] Replace binary header guessing with row-role scoring and explicit headerless support.
- [ ] Implement deterministic logical lane inference with full source accounting.
- [ ] Project multi-row header paths onto logical columns.
- [ ] Infer per-logical-column units after normalization.
- [ ] Add independent document-region tracking and content-evidence classification.
- [ ] Replace Item 15/16 shortcuts with actual index/exhibit detection and financial-region resets.
- [ ] Add title candidate scoring, rejection reasons, provenance, and conservative inheritance.
- [ ] Add vertical/horizontal/compound continuation detection with stable logical IDs.
- [ ] Render valid Markdown from logical tables only.
- [ ] Persist both physical evidence and logical schema in processed blocks.
- [ ] Compose one complete included non-navigation logical table per chunk and retain all fragment/block provenance; emit no chunk for index/navigation.
- [ ] Update table embedding text to consume only the logical schema.
- [ ] Replace copy-presence table metrics with structural/class/title/continuation metrics.
- [ ] Add strict validators for table-schema-v2 blocks, chunk-schema-v3 chunks/config, and embedding-manifest-v3 artifacts.
- [ ] Fix the notebook to consume native nested lists without comma parsing.
- [ ] Run unit, integration, cross-company fixture, and full-corpus QA tests.
- [ ] Stage and manually review all ten processed/chunk artifacts.
- [ ] Promote the approved aligned table-v2/chunk-v3 processed/chunk release atomically.
- [ ] Regenerate and validate BGE-base embeddings for all ten issuers.
- [ ] Migrate Mobileye evaluation labels through stable evidence provenance.
- [ ] Establish and save a new semantic-retrieval baseline.
- [ ] Update documentation with measured results.
- [ ] Resume reranking only after the new baseline is trustworthy.

Recommended review/commit boundaries are: fixtures and text normalization; typed cells and header roles; logical normalization and units; context/class/title logic; continuation and schema rendering; downstream consumers and QA; generated-artifact migration. These boundaries make regressions bisectable. They are not authorization to merge an incomplete schema with fallbacks.

## 19. Definition of done

The parsing repair is complete only when all of the following are true:

1. Every one of the ten unchanged raw filings produces versioned processed blocks that retain the physical DOM evidence and expose validated logical tables.
2. The representative fixtures in Section 13 have the expected logical widths, kinds, header modes, titles, units, and continuation relationships.
3. There are no normalization collisions, no unexplained unmapped non-empty cells, and complete raw-cell accounting for retained table content.
4. Logical row widths are consistent, marker-only columns are eliminated, and every rendered simple table is valid Markdown.
5. Headerless tables remain valid and searchable; lack of an explicit header is not itself a failure.
6. Genuine financial tables under Item 15/16 are classified from structure/content and financial-region context, while actual indexes and exhibit lists are not classified as financial data.
7. Titles have provenance and quality status; continuation fragments share stable logical IDs only when bounded evidence supports the link.
8. Every included non-navigation logical table produces exactly one complete table chunk, with all contributing block IDs, raw table IDs, anchors, row/cell source mappings, body-rowspan mappings, and citation metadata; every `index_navigation` logical table produces zero chunks under the current exclusion policy.
9. Narrative block order/path and the selected chunking/embedding algorithms remain unchanged; every localized narrative-text/span difference is attributable to an approved boundary repair and passes regression review.
10. The notebook consumes nested arrays directly and no longer splits values or captions at commas.
11. All unit tests, real-SEC fixture tests, validators, and corpus gates pass, with warnings reviewed rather than hidden.
12. Approved processed blocks and chunks have been regenerated for all ten companies; no old/new schema mixture remains.
13. Matching normalized 768-dimensional BGE-base vectors and manifests have been regenerated and pass source-hash, order, count, dimension, finite-value, and norm checks for all ten companies.
14. Mobileye gold labels have been migrated through stable evidence identity and a new BGE-base semantic baseline has been recorded separately from the historical result.
15. README, architecture, roadmap, and reports describe the measured table-v2/chunk-v3/manifest-v3 state, not the pre-migration state or aspirational targets.

Until all fifteen conditions hold, corpus-wide retrieval, persistent indexing, and reranking remain blocked by untrustworthy table evidence. This is a data-integrity gate, not a request to broaden the RAG architecture.
> **Archived 4 September 2026.** This previously ignored local document is\n> preserved as a read-only historical record, not a current plan or authority.\n> See [\`FINALIZATION.md\`](../../../../FINALIZATION.md) for the sole remaining-work plan.\n>\n# SEC Filing Table Parsing Repair Specification
