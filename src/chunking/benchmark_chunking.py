import argparse
import copy
import time
from importlib.metadata import version
from pathlib import Path

from .chunk_documents import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    chunk_blocks,
    chunk_statistics,
    load_chunk_config,
    load_jsonl,
)


DEFAULT_INPUTS = (
    PROJECT_ROOT / "data" / "processed" / "MBLY" / "2025-10-K.blocks.jsonl",
    PROJECT_ROOT / "data" / "processed" / "TSLA" / "2025-10-K.blocks.jsonl",
)
EXPERIMENTS = (
    ("recursive", 250, 25),
    ("recursive", 500, 50),
    ("recursive", 800, 100),
    ("recursive", 1200, 150),
    ("recursive", 1600, 200),
    ("fixed", 250, 25),
    ("fixed", 500, 50),
    ("fixed", 800, 100),
    ("fixed", 1200, 150),
    ("fixed", 1600, 200),
)


def percentage(value: float) -> str:
    return f"{value * 100:.1f}%"


def run_experiments(input_path: Path, base_config: dict) -> dict:
    blocks = load_jsonl(input_path)
    results = []
    for strategy, size, overlap in EXPERIMENTS:
        config = copy.deepcopy(base_config)
        config.update(
            {"strategy": strategy, "chunk_size": size, "chunk_overlap": overlap}
        )
        started = time.perf_counter()
        try:
            chunks = chunk_blocks(blocks, config)
            metrics = chunk_statistics(chunks, config, blocks)
            metrics.update(
                {
                    "status": "ok",
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                }
            )
            results.append(metrics)
        except ValueError as error:
            results.append(
                {
                    "status": "infeasible",
                    "strategy": strategy,
                    "chunk_size": size,
                    "configured_overlap": overlap,
                    "error": str(error),
                }
            )
    return {"input": input_path, "blocks": blocks, "results": results}


def result_table(results: list[dict]) -> list[str]:
    lines = [
        "| Strategy | Size | Config overlap | Actual overlap | Chunks | Narrative | Tables | Min | Median | P95 | Max | Boundary accuracy | Coverage | Section accuracy | Table context | Time |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        if result["status"] != "ok":
            continue
        lines.append(
            "| {strategy} | {chunk_size} | {configured_overlap} | "
            "{actual_overlap_median:g} | {chunk_count} | {narrative_chunks} | "
            "{table_chunks} | {length_min} | {length_median:g} | {length_p95} | "
            "{length_max} | {boundary} | {coverage} | {section} | {table_context} | "
            "{elapsed_ms:.1f} ms |".format(
                **result,
                boundary=percentage(result["boundary_accuracy"]),
                coverage=percentage(result["source_block_coverage"]),
                section=percentage(result["section_accuracy"]),
                table_context=percentage(result["table_context_accuracy"]),
            )
        )
    return lines


def filing_section(run: dict) -> list[str]:
    blocks = run["blocks"]
    failed = [result for result in run["results"] if result["status"] == "infeasible"]
    lines = [
        f"## {blocks[0]['company']}",
        "",
        f"- Input: `{run['input'].relative_to(PROJECT_ROOT)}`",
        f"- Filing: {blocks[0]['filing_year']} {blocks[0]['form']}",
        f"- Structured blocks: {len(blocks)}",
        f"- Longest source block: {max(len(block['text']) for block in blocks):,} characters",
        "",
        "### Results",
        "",
        *result_table(run["results"]),
    ]
    if failed:
        lines.extend(
            [
                "",
                "### Infeasible Configurations",
                "",
                "| Strategy | Size | Overlap | Reason |",
                "|---|---:|---:|---|",
            ]
        )
        for result in failed:
            lines.append(
                f"| {result['strategy']} | {result['chunk_size']} | "
                f"{result['configured_overlap']} | {result['error']} |"
            )
    lines.append("")
    return lines


def baseline_result(run: dict) -> dict:
    return next(
        result
        for result in run["results"]
        if result["status"] == "ok"
        and result["strategy"] == "recursive"
        and result["chunk_size"] == 1200
    )


def build_report(runs: list[dict]) -> str:
    lines = [
        "# Chunking Report",
        "",
        "## Method",
        "",
        f"- Splitter package: `langchain-text-splitters=={version('langchain-text-splitters')}`",
        "- Tables use identical row-group chunking in every run.",
        "- Navigation is excluded and chunks never cross `section_path` boundaries.",
        "- `Boundary accuracy` is the share of narrative chunks ending at sentence punctuation.",
        "- Boundary accuracy is not retrieval accuracy; retrieval requires embeddings and labels.",
        "- `Actual overlap` is median source-character overlap between narrative chunks.",
        "- `Coverage` is the share of evidence blocks represented in at least one chunk.",
        "",
    ]
    for run in runs:
        lines.extend(filing_section(run))

    lines.extend(
        [
            "## Recursive 1,200 / 150 Comparison",
            "",
            "| Filing | Source blocks | Chunks | Median | Min | Max | Boundary accuracy | Actual overlap |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in runs:
        result = baseline_result(run)
        lines.append(
            f"| {run['blocks'][0]['ticker']} | {len(run['blocks'])} | "
            f"{result['chunk_count']} | {result['length_median']:g} | "
            f"{result['length_min']} | {result['length_max']} | "
            f"{percentage(result['boundary_accuracy'])} | "
            f"{result['actual_overlap_median']:g} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Recursive splitting consistently preserves sentence boundaries better than fixed slicing.",
            "- Recursive overlap is not guaranteed; separator-aware runs can have actual overlap 0.",
            "- Fixed splitting produces the configured overlap exactly but often cuts sentences.",
            "- A 250-character maximum is infeasible when repeated table context exceeds the limit.",
            "- The 500-character runs create many small table fragments.",
            "- The current baseline remains recursive splitting with size 1,200 and overlap 150.",
            "",
            "## Reproduce",
            "",
            "```bash",
            "python -m src.chunking.benchmark_chunking",
            "python -m src.chunking.chunk_documents --overwrite",
            "python -m src.chunking.chunk_documents data/processed/TSLA/2025-10-K.blocks.jsonl --overwrite",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare filing chunking strategies.")
    parser.add_argument("inputs", nargs="*", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "CHUNKING_REPORT.md",
    )
    arguments = parser.parse_args()

    config = load_chunk_config(arguments.config)
    runs = [run_experiments(input_path, config) for input_path in arguments.inputs]
    arguments.report.write_text(build_report(runs), encoding="utf-8")
    print(f"Wrote {len(EXPERIMENTS) * len(runs)} results to {arguments.report}")


if __name__ == "__main__":
    main()
