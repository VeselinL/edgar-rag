# Cleaning and Chunking Report

The maintained report is [`../CLEANING_AND_CHUNKING_IZVESTAJ.md`](docs/CLEANING_AND_CHUNKING_IZVESTAJ.md).

Supporting evidence:

- [`CLEANING_AND_CHUNKING_NOTES.md`](docs/CLEANING_AND_CHUNKING_NOTES.md) — current QA inventory, decisions, and open checks;
- [`../CHUNKING_REPORT.md`](docs/CHUNKING_REPORT.md) — reproducible token-based strategy benchmark.

The selected baseline is recursive chunking with a 500-token narrative limit,
32-token configured overlap, and one complete included logical table per table
chunk. The 13 August 2026 `table-v2-chunk-v3.20260813-r2` release is aligned
across all ten issuers: 4,115 chunks (3,238 narrative and 877 table), with 12 processed
navigation tables correctly producing zero chunks. Tesla has been regenerated;
there is no remaining character-based artifact exception.
