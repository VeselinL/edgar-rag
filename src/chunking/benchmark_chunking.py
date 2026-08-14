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
    count_tokens,
    get_tokenizer,
    load_chunk_config,
    load_jsonl,
)


DEFAULT_INPUTS = (
    PROJECT_ROOT / "data" / "processed" / "MBLY" / "2025-10-K.blocks.jsonl",
    PROJECT_ROOT / "data" / "processed" / "TSLA" / "2025-10-K.blocks.jsonl",
)
# format: (chunking strategy, token count, token overlap)
EXPERIMENTS = (
    ("recursive", 128, 16),
    ("recursive", 192, 24),
    ("recursive", 250, 32),
    ("recursive", 500, 32),
    ("fixed", 128, 16),
    ("fixed", 192, 24),
    ("fixed", 250, 32),
    ("fixed", 500, 32)
)


def percentage(value: float) -> str:
    return f"{value * 100:.1f}%"


def run_experiments(input_path: Path, base_config: dict) -> dict:
    input_path = input_path.resolve()
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
    return {
        "input": input_path,
        "blocks": blocks,
        "config": base_config,
        "results": results,
    }


def result_table(results: list[dict]) -> list[str]:
    lines = [
        "| Strategy | Size (tokens) | Config overlap (tokens) | Actual overlap (tokens) | Chunks | Narrative | Logical tables | HTML fragments | Fallback warnings | Min tokens | Median tokens | P95 tokens | Max tokens | Boundary accuracy | Coverage | Section accuracy | Context copy | Markdown | Time |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        if result["status"] != "ok":
            continue
        lines.append(
            "| {strategy} | {chunk_size} | {configured_overlap} | "
            "{actual_overlap_median:g} | {chunk_count} | {narrative_chunks} | "
            "{logical_table_count} | {table_fragment_count} | "
            "{normalization_fallback_fragment_count} | {length_min} | "
            "{length_median:g} | {length_p95} | "
            "{length_max} | {boundary} | {coverage} | {section} | {table_context} | {markdown} | "
            "{elapsed_ms:.1f} ms |".format(
                **result,
                boundary=percentage(result["boundary_accuracy"]),
                coverage=percentage(result["source_block_coverage"]),
                section=percentage(result["section_accuracy"]),
                table_context=percentage(result["table_context_copy_completeness"]),
                markdown=percentage(result["table_markdown_validity"]),
            )
        )
    return lines


def filing_section(run: dict) -> list[str]:
    blocks = run["blocks"]
    tokenizer = get_tokenizer(run["config"])
    failed = [result for result in run["results"] if result["status"] == "infeasible"]
    lines = [
        f"## {blocks[0]['company']}",
        "",
        f"- Input: `{run['input'].relative_to(PROJECT_ROOT)}`",
        f"- Filing: {blocks[0]['filing_year']} {blocks[0]['form']}",
        f"- Structured blocks: {len(blocks)}",
        f"- Longest source block: {max(count_tokens(block['text'], tokenizer) for block in blocks):,} tokens",
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
        and result["chunk_size"] == 500
        and result["configured_overlap"] == 32
    )


def build_report(runs: list[dict]) -> str:
    lines = [
        "# Chunking Report",
        "",
        "## Method",
        "",
        f"- Splitter package: `langchain-text-splitters=={version('langchain-text-splitters')}`",
        f"- Tokenizer: `{runs[0]['config']['tokenizer_model']}`",
        "- Every retained table stays complete in one table chunk in every run.",
        "- Size and overlap are measured in tokenizer tokens.",
        "- The configured size limit applies to narrative chunks, not complete table chunks.",
        "- Navigation is excluded and chunks never cross `section_path` boundaries.",
        "- `Boundary accuracy` is the share of narrative chunks ending at sentence punctuation.",
        "- Boundary accuracy is not retrieval accuracy; retrieval requires embeddings and labels.",
        "- `Actual overlap` is median source-token overlap between narrative chunks.",
        "- `Coverage` is the share of evidence blocks represented in at least one chunk.",
        "- `Context copy` is a serialization smoke test, not semantic table quality.",
        "- Table quality is measured from logical width, source accounting, marker, fallback, and Markdown invariants.",
        "",
    ]
    for run in runs:
        lines.extend(filing_section(run))

    lines.extend(
        [
            "## Selected Recursive 500 / 32 Comparison",
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
            "- Fixed token splitting produces the configured overlap exactly but often cuts sentences.",
            "- Complete table chunks may exceed the configured narrative chunk size by design.",
            "- The selected baseline is recursive splitting with a 500-token narrative limit and 32-token configured overlap.",
            "",
            "## Reproduce",
            "",
            "```bash",
            ".venv/bin/python -m src.chunking.benchmark_chunking",
            ".venv/bin/python -m src.chunking.chunk_documents mobileye --overwrite",
            ".venv/bin/python -m src.chunking.chunk_documents tesla --overwrite",
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
