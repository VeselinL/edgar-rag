> **Archived 4 September 2026.** This previously ignored local document is
> preserved as a read-only historical record, not a current plan or authority.
> See [`FINALIZATION.md`](../../../../FINALIZATION.md) for the sole remaining-work plan.
>
# Cleaning and Chunking QA Notes

**Status date:** 2026-08-13
**Release:** `table-v2-chunk-v3.20260813-r2`
**Selected narrative configuration:** recursive 500 tokens, 32 configured overlap

This is the working evidence log for the repaired SEC table corpus. The prior
physical-grid representation produced marker columns, sparse Markdown, weak
headers/titles, and one chunk per HTML fragment; its measurements remain
historical and are not the current baseline.

## Current corpus inventory

| Ticker | Blocks | Physical table fragments | Logical tables | Chunks | Narrative | Table chunks | BGE truncations |
|---|---:|---:|---:|---:|---:|---:|---:|
| APTV | 1,367 | 138 | 135 | 528 | 394 | 134 | 5 |
| AUR | 909 | 36 | 35 | 261 | 227 | 34 | 2 |
| F | 1,713 | 143 | 134 | 576 | 444 | 132 | 4 |
| GM | 1,066 | 126 | 125 | 410 | 286 | 124 | 4 |
| GOOGL | 1,260 | 172 | 169 | 512 | 345 | 167 | 3 |
| MBLY | 1,258 | 53 | 51 | 462 | 412 | 50 | 2 |
| NVDA | 980 | 58 | 57 | 329 | 273 | 56 | 2 |
| OUST | 1,112 | 62 | 61 | 375 | 315 | 60 | 1 |
| QCOM | 830 | 57 | 55 | 321 | 267 | 54 | 3 |
| TSLA | 945 | 69 | 67 | 341 | 275 | 66 | 2 |
| **Total** | **11,440** | **914** | **889** | **4,115** | **3,238** | **877** | **28** |

The 889 logical tables include 12 navigation tables retained in processed
evidence and excluded from chunks. Thus every one of the 877 included logical
tables maps to exactly one table chunk.

Relative to the superseded r1 release, one Aptiv cash-flow continuation and one
Qualcomm exhibit continuation now compose from bounded generic evidence. This
accounts for the two fewer logical tables, chunks, and vectors without changing
any narrative block or chunk.

## Physical and logical table contracts

- Raw SEC HTML under `data/raw/` remains byte-for-byte unchanged.
- Each retained table records stable HTML/raw-cell IDs, cleaned-DOM XPath,
  fingerprint, raw text, origin coordinates, `rowspan`/`colspan`, formatting,
  and both source-coordinate and physical-display grids.
- `table_schema_version: 2` adds logical lanes, row roles, header paths,
  per-column units, document region, classification/title provenance,
  continuation relationships, logical rowspans, and exact raw-cell mappings.
- Boundary-aware text collection preserves visible Inline XBRL and adds visible
  word boundaries without splitting inline-formatting text or duplicating nested
  tables.
- Typed recognition covers SEC currency/percent markers, negatives, footnotes,
  ranges, dates, years, durations, identifiers, and sensitivity pairs.
- Titles are bounded native evidence with accepted/rejected provenance. Missing
  titles remain null instead of borrowing a company, page, or generic notes
  heading.
- Markdown is rendered only from logical data. Physical fields are evidence,
  not a silent production fallback.

## Chunk and embedding contracts

- Narrative grouping, recursive separators, 500-token limit, 32-token configured
  overlap, pinned MiniLM tokenizer, and source-span algorithm are unchanged.
- Chunks use schema 3. Vertical/horizontal/compound continuations produce one
  complete included logical table chunk with all source block, table, anchor,
  row, cell, and citation provenance.
- BGE-base v1.5 uses the pinned revision, unchanged prefixes, 512-token model
  limit, normalized `float32`, and dimension 768.
- Table embedding text consumes logical fields only. Complete table/citation
  text remains in the chunk even if the compact embedding input is truncated.

## Measured gates

The live audit reports:

- 100% non-heading/non-navigation source block and anchor coverage;
- 100% raw non-empty-cell accounting and valid table Markdown;
- zero normalization collisions, unmapped values, standalone marker cells,
  `row_text_fallback` tables, unknown kinds, invalid financial titles, or
  rejected prose-caption cues;
- 716 financial/structured logical tables in the density target, with one
  reviewed native sparse exception (0.1395%);
- 145 reviewed normalization support warnings: 133 one-row leaf-header supports
  and 12 conservative separate lanes;
- 174 reviewed financial fragments with no trustworthy native title;
- 10 reviewed horizontal and six reviewed compound compositions;
- 72 reviewed explicit parenthetical continuation cues that safely remain
  orphaned because the neighboring evidence is incompatible;
- one reviewed 535-character Qualcomm native caption retained as complete source
  evidence;
- 28 reviewed embedding inputs over 512 tokens: 24 exhibit lists, two structured
  tables, two financial tables, and zero narrative chunks;
- all 10,526 narrative blocks and all 3,238 narrative chunks byte-identical to
  the pre-repair artifacts, in the same order and section paths.

The all-ten frozen SEC fixtures, 15 priority cases, six positive continuation
groups, and 41-table NVIDIA region inventory pass. The checked-in corpus baseline
prevents aggregate drift without rewriting expected values during a test.

## Reproducibility records

- release manifest SHA-256:
  `1e47a8d1623a8d9f4c2722825d743c1c29b883f75bbaadf2cb4ccce6076f7036`;
- live table audit SHA-256:
  `df171e1cee8ed8e9bae0a8d9e84b6c19c125818d7e82cbadf15e2480814c1328`;
- embedding release SHA-256:
  `a1208dd56b4d03a3bcc1ebd05f09bfe1e0f6cc31819be593f3a50d4bee791f5e`;
- manual-review manifest SHA-256:
  `5d710a89d1bd477a59ff075909ef89ff9902dcdfbcb71f5291a4b9deadfdb66f`;
- Mobileye gold-v2 SHA-256:
  `1c7ec7041cb596b18a018f1f0b00df015f3feea5351879ca5d3957497716b301`;
- Mobileye semantic-baseline SHA-256:
  `356c1c13551eb485daf5d938f9406ead99cdcae0172c2156cee22dbb68fa2cac`;
- Mobileye baseline-review SHA-256:
  `efe70c7c72cbaa808c2cc98d48616d82e311ba96c133710c7d0a727226f32764`;
- chunking config SHA-256:
  `c9c2556c845a5867451a942d4f4c9778d127286c0728629a32b8c31079ea0738`.

The original processed/chunk filenames and hashes are preserved in
`data/manifests/table-v1-artifacts.json`; recoverable copies are under
`table-v1-backup.20260813/`. The superseded r1 release, embeddings, and
evaluation outputs are separately recoverable under the corresponding
`table-v2-r1-*.20260813/` backups and r1 manifests.

## Remaining work

The parser repair is complete. Page-number provenance remains unset because the
frozen HTML does not provide a uniformly trustworthy page mapping. Persistent
indexing, retrieval service integration, reranking, generation, citations, and
the UI remain separate roadmap stages. Do not tune parsing/chunking from a single
retrieval miss; first inspect the saved gold evidence, current chunk, embedding
input, and retrieval result.
> **Archived 4 September 2026.** This previously ignored local document is\n> preserved as a read-only historical record, not a current plan or authority.\n> See [\`FINALIZATION.md\`](../../../../FINALIZATION.md) for the sole remaining-work plan.\n>\n# Cleaning and Chunking QA Notes
