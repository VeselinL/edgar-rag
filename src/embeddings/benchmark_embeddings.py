import argparse
import json
import math
import os
import platform
import statistics
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .embed_chunks import (
    DEFAULT_CHUNKS_DIRECTORY,
    DEFAULT_MODEL_NAME,
    MODEL_CONFIGS,
    PROJECT_ROOT,
    embedding_text,
    encode_documents,
    encode_query,
    load_chunks,
    load_model,
    package_version,
    prepare_document_text,
    resolved_model_revision,
    sha256_file,
    token_lengths,
    validate_embeddings,
)


DEFAULT_INPUT_PATH = (
    DEFAULT_CHUNKS_DIRECTORY / "MBLY" / "2025-10-K.chunks.jsonl"
)
DEFAULT_REPORT_PATH = PROJECT_ROOT / "EMBEDDING_REPORT.md"
DEFAULT_QUERY = "What are the company's main revenue drivers?"
REPORT_DATA_START = "<!-- EMBEDDING_BENCHMARK_DATA\n"
REPORT_DATA_END = "\n-->"


def percentile(values: list[int], percentage: float) -> int:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * percentage) - 1]


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def synchronize_device(device: object) -> None:
    device_name = str(device)
    try:
        import torch
    except ImportError:
        return
    if device_name.startswith("cuda") and torch.cuda.is_available():
        torch.cuda.synchronize(device)
    elif device_name.startswith("mps") and hasattr(torch.mps, "synchronize"):
        torch.mps.synchronize()


def model_size(model) -> tuple[int, int]:
    parameters = list(model.parameters())
    return (
        sum(parameter.numel() for parameter in parameters),
        sum(parameter.numel() * parameter.element_size() for parameter in parameters),
    )


def benchmark_embeddings(
    input_path: str | Path = DEFAULT_INPUT_PATH,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    device: str | None = None,
    batch_size: int = 32,
    query_repetitions: int = 5,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    show_progress_bar: bool = True,
) -> dict:
    if model_name not in MODEL_CONFIGS:
        choices = ", ".join(MODEL_CONFIGS)
        raise ValueError(f"Unknown model name {model_name!r}; choose from: {choices}")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if query_repetitions <= 0:
        raise ValueError("query_repetitions must be positive")

    input_path = Path(input_path).resolve()
    benchmark_started = time.perf_counter()
    chunks = load_chunks(input_path)

    load_started = time.perf_counter()
    model = load_model(model_name, device=device)
    model_load_seconds = time.perf_counter() - load_started
    actual_device = str(getattr(model, "device", device or "unknown"))

    preparation_started = time.perf_counter()
    texts = [embedding_text(chunk) for chunk in chunks]
    model_inputs = [prepare_document_text(text, model_name) for text in texts]
    lengths = token_lengths(model, model_inputs)
    if not lengths:
        raise RuntimeError("The selected model does not expose tokenizer lengths")
    preparation_seconds = time.perf_counter() - preparation_started

    synchronize_device(actual_device)
    encoding_started = time.perf_counter()
    vectors = encode_documents(
        model,
        texts,
        model_name=model_name,
        batch_size=batch_size,
        show_progress_bar=show_progress_bar,
    )
    synchronize_device(actual_device)
    encoding_seconds = time.perf_counter() - encoding_started
    validate_embeddings(vectors, len(chunks))
    end_to_end_seconds = time.perf_counter() - benchmark_started

    encode_query(model, DEFAULT_QUERY, model_name=model_name)
    query_latencies = []
    for _ in range(query_repetitions):
        synchronize_device(actual_device)
        query_started = time.perf_counter()
        encode_query(model, DEFAULT_QUERY, model_name=model_name)
        synchronize_device(actual_device)
        query_latencies.append((time.perf_counter() - query_started) * 1000)

    max_sequence_length = int(getattr(model, "max_seq_length", 0)) or None
    truncated_indexes = [
        index
        for index, length in enumerate(lengths)
        if max_sequence_length and length > max_sequence_length
    ]
    effective_token_count = sum(
        min(length, max_sequence_length) if max_sequence_length else length
        for length in lengths
    )
    content_types = Counter(chunk["content_type"] for chunk in chunks)
    parameter_count, model_bytes = model_size(model)
    config = MODEL_CONFIGS[model_name]
    first = chunks[0]

    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "company": first.get("company"),
        "ticker": first.get("ticker"),
        "filing_year": first.get("filing_year"),
        "form": first.get("form"),
        "input_path": relative_path(input_path),
        "source_chunks_sha256": sha256_file(input_path),
        "model_name": model_name,
        "model_repository": config["repository"],
        "requested_model_revision": config["revision"],
        "resolved_model_revision": resolved_model_revision(model),
        "device": actual_device,
        "batch_size": batch_size,
        "chunk_count": len(chunks),
        "narrative_chunks": content_types.get("narrative", 0),
        "table_chunks": content_types.get("table", 0),
        "dimension": int(vectors.shape[1]),
        "parameter_count": parameter_count,
        "model_bytes": model_bytes,
        "vector_bytes": int(vectors.nbytes),
        "normalized": bool(
            np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)
        ),
        "max_sequence_length": max_sequence_length,
        "input_token_count": sum(lengths),
        "effective_token_count": effective_token_count,
        "input_token_median": statistics.median(lengths),
        "input_token_p95": percentile(lengths, 0.95),
        "input_token_max": max(lengths),
        "truncated_input_count": len(truncated_indexes),
        "truncated_narrative_count": sum(
            chunks[index]["content_type"] == "narrative"
            for index in truncated_indexes
        ),
        "truncated_table_count": sum(
            chunks[index]["content_type"] == "table"
            for index in truncated_indexes
        ),
        "model_load_seconds": model_load_seconds,
        "preparation_seconds": preparation_seconds,
        "encoding_seconds": encoding_seconds,
        "end_to_end_seconds": end_to_end_seconds,
        "chunks_per_second": len(chunks) / encoding_seconds,
        "tokens_per_second": effective_token_count / encoding_seconds,
        "query_latency_median_ms": statistics.median(query_latencies),
        "query_latency_p95_ms": percentile(query_latencies, 0.95),
        "query_repetitions": query_repetitions,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "sentence_transformers_version": package_version("sentence-transformers"),
        "torch_version": package_version("torch"),
        "numpy_version": np.__version__,
    }
    update_report(Path(report_path), result)
    return result


def result_identity(result: dict) -> tuple:
    return (
        result["input_path"],
        result["source_chunks_sha256"],
        result["model_name"],
    )


def upsert_result(results: list[dict], new_result: dict) -> list[dict]:
    same_input = [
        result
        for result in results
        if result["input_path"] == new_result["input_path"]
    ]
    if same_input and any(
        result["source_chunks_sha256"] != new_result["source_chunks_sha256"]
        for result in same_input
    ):
        results = [
            result
            for result in results
            if result["input_path"] != new_result["input_path"]
        ]
    identity = result_identity(new_result)
    updated = [result for result in results if result_identity(result) != identity]
    updated.append(new_result)
    return updated


def load_report_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    report = path.read_text(encoding="utf-8")
    start = report.find(REPORT_DATA_START)
    if start < 0:
        raise ValueError(f"Existing report has no benchmark data block: {path}")
    start += len(REPORT_DATA_START)
    end = report.find(REPORT_DATA_END, start)
    if end < 0:
        raise ValueError(f"Existing report has an incomplete benchmark data block: {path}")
    data = json.loads(report[start:end])
    if not isinstance(data, list):
        raise ValueError(f"Benchmark data must be a list: {path}")
    return data


def format_seconds(value: float) -> str:
    return f"{value:.2f} s"


def format_bytes(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} MiB"


def format_count(value: float) -> str:
    return f"{value:,.0f}"


def model_order(result: dict) -> tuple:
    aliases = list(MODEL_CONFIGS)
    return (
        result["input_path"],
        aliases.index(result["model_name"]),
    )


def build_report(results: list[dict]) -> str:
    ordered = sorted(results, key=model_order)
    lines = [
        "# Embedding Report",
        "",
        "## Method",
        "",
        "- One invocation benchmarks one model; models are never loaded together.",
        "- Document vectors are normalized and computed with the same model-specific prefixes used by the embedding pipeline.",
        "- `Encoding time` measures document embedding only. `Total time` also includes reading chunks, loading the model, preparing inputs, and validation.",
        "- Query latency is measured after one warm-up call and reported over the configured repetitions.",
        "- Effective tokens cap each input at the model's maximum sequence length and therefore approximate tokens actually processed.",
        "- Load and total times depend on hardware and whether model files are already cached. Encoding throughput is the cleaner compute comparison.",
        "- This report measures runtime, capacity, and truncation—not retrieval relevance. Compare recall and MRR separately.",
        "",
    ]

    grouped = {}
    for result in ordered:
        key = (
            result["input_path"],
            result["source_chunks_sha256"],
        )
        grouped.setdefault(key, []).append(result)

    for (input_path, source_hash), group in grouped.items():
        first = group[0]
        title = " ".join(
            str(value)
            for value in (first.get("ticker"), first.get("filing_year"), first.get("form"))
            if value
        )
        lines.extend(
            [
                f"## {title}",
                "",
                f"- Company: {first.get('company') or 'unknown'}",
                f"- Input: `{input_path}`",
                f"- Source hash: `{source_hash}`",
                f"- Chunks: {first['chunk_count']:,} ({first['narrative_chunks']:,} narrative, {first['table_chunks']:,} tables)",
                "",
                "### Performance",
                "",
                "| Model | Repository | Device | Batch | Dimension | Parameters | Model memory | Vector memory | Load | Prepare | Encode | Total | Chunks/s | Tokens/s | Query median |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for result in group:
            lines.append(
                f"| {result['model_name']} | `{result['model_repository']}` | "
                f"{result['device']} | {result['batch_size']} | {result['dimension']} | "
                f"{result['parameter_count'] / 1_000_000:.1f}M | "
                f"{format_bytes(result['model_bytes'])} | "
                f"{format_bytes(result['vector_bytes'])} | "
                f"{format_seconds(result['model_load_seconds'])} | "
                f"{format_seconds(result['preparation_seconds'])} | "
                f"{format_seconds(result['encoding_seconds'])} | "
                f"{format_seconds(result['end_to_end_seconds'])} | "
                f"{result['chunks_per_second']:.1f} | "
                f"{format_count(result['tokens_per_second'])} | "
                f"{result['query_latency_median_ms']:.1f} ms |"
            )

        lines.extend(
            [
                "",
                "### Input Lengths and Truncation",
                "",
                "| Model | Max sequence | Median tokens | P95 tokens | Max tokens | Input tokens | Effective tokens | Truncated | Narrative | Tables | Normalized |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for result in group:
            truncation_rate = result["truncated_input_count"] / result["chunk_count"]
            lines.append(
                f"| {result['model_name']} | {result['max_sequence_length']} | "
                f"{result['input_token_median']:g} | {result['input_token_p95']} | "
                f"{result['input_token_max']} | {result['input_token_count']:,} | "
                f"{result['effective_token_count']:,} | "
                f"{result['truncated_input_count']} ({truncation_rate:.1%}) | "
                f"{result['truncated_narrative_count']} | "
                f"{result['truncated_table_count']} | "
                f"{'yes' if result['normalized'] else 'no'} |"
            )

        lines.extend(
            [
                "",
                "### Environment",
                "",
                "| Model | Run (UTC) | Python | PyTorch | Sentence Transformers | NumPy | Platform |",
                "|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for result in group:
            lines.append(
                f"| {result['model_name']} | {result['run_at']} | "
                f"{result['python_version']} | {result['torch_version']} | "
                f"{result['sentence_transformers_version']} | "
                f"{result['numpy_version']} | {result['platform']} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Reproduce",
            "",
            "Run one model at a time; rerunning the same model replaces its row:",
            "",
            "```bash",
            "python -m src.embeddings.benchmark_embeddings --model-name minilm --device cpu",
            "python -m src.embeddings.benchmark_embeddings --model-name mpnet --device cpu",
            "python -m src.embeddings.benchmark_embeddings --model-name bgebase --device cpu",
            "python -m src.embeddings.benchmark_embeddings --model-name nomic --device cpu",
            "```",
            "",
            REPORT_DATA_START.rstrip("\n"),
            json.dumps(ordered, indent=2),
            REPORT_DATA_END.lstrip("\n"),
            "",
        ]
    )
    return "\n".join(lines)


def write_report(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as output_file:
            temporary_path = Path(output_file.name)
            output_file.write(build_report(results))
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def update_report(path: Path, result: dict) -> None:
    results = upsert_result(load_report_results(path), result)
    write_report(path, results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark one embedding model and update EMBEDDING_REPORT.md."
    )
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument(
        "--model-name",
        choices=tuple(MODEL_CONFIGS),
        default=DEFAULT_MODEL_NAME,
    )
    parser.add_argument("--device")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--query-repetitions", type=int, default=5)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--no-progress", action="store_true")
    arguments = parser.parse_args()

    result = benchmark_embeddings(
        arguments.input,
        model_name=arguments.model_name,
        device=arguments.device,
        batch_size=arguments.batch_size,
        query_repetitions=arguments.query_repetitions,
        report_path=arguments.report,
        show_progress_bar=not arguments.no_progress,
    )
    print(
        f"Benchmarked {result['model_name']} in "
        f"{result['end_to_end_seconds']:.2f} seconds"
    )
    print(f"Updated {arguments.report}")


if __name__ == "__main__":
    main()
