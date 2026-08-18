"""Evaluate BM25 retrieval over the fixed annual-filing corpus."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import bm25s
import pandas as pd


DEFAULT_CHUNKS_DIRECTORY = Path("data/chunks")
DEFAULT_TEST_QUERIES_PATH = Path("data/evaluation/test_queries.jsonl")
DEFAULT_OUTPUT_DIRECTORY = Path("data/evaluation")
K_VALUES = [1, 3, 5, 10, 30]
MAX_K = max(K_VALUES)

FILINGS = {
    "AUR": "2025-10-K",
    "TSLA": "2025-10-K",
    "MBLY": "2025-10-K",
    "GOOGL": "2025-10-K",
    "GM": "2025-10-K",
    "F": "2025-10-K",
    "NVDA": "2026-10-K",
    "QCOM": "2025-10-K",
    "APTV": "2025-10-K",
    "OUST": "2025-10-K",
}


def load_corpus(chunks_directory: Path) -> list[dict[str, Any]]:
    all_chunks = []
    for ticker, filing_name in FILINGS.items():
        chunk_path = chunks_directory / ticker / f"{filing_name}.chunks.jsonl"
        with chunk_path.open("r", encoding="utf-8") as file:
            all_chunks.extend(json.loads(line) for line in file if line.strip())
    return all_chunks


def load_test_queries(path: Path) -> list[dict[str, Any]]:
    test_cases = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                test_cases.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON on line {line_number}: {error}") from error
    return test_cases


def build_bm25_index(all_chunks: list[dict[str, Any]]) -> bm25s.BM25:
    missing_text_ids = [chunk.get("chunk_id", "<unknown>") for chunk in all_chunks if not chunk.get("text")]
    if missing_text_ids:
        raise ValueError("Chunks missing searchable text:\n" + "\n".join(missing_text_ids))

    retriever = bm25s.BM25()
    retriever.index(bm25s.tokenize([chunk["text"] for chunk in all_chunks]))
    return retriever


def bm25_retrieve(
    query: str,
    retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    top_k: int = MAX_K,
) -> tuple[list[dict[str, Any]], float]:
    start = time.perf_counter()
    indices, scores = retriever.retrieve(bm25s.tokenize(query), k=top_k)
    indices = indices[0]
    scores = scores[0]
    results = [
        {
            "rank": rank,
            "chunk_id": all_chunks[index]["chunk_id"],
            "ticker": all_chunks[index].get("ticker"),
            "content_type": all_chunks[index].get("content_type"),
            "score": float(score),
            "index": int(index),
        }
        for rank, (index, score) in enumerate(zip(indices, scores, strict=True), start=1)
    ]
    return results, time.perf_counter() - start


def evaluate_query(test_case: dict[str, Any], retrieved: list[dict[str, Any]]) -> dict[str, Any]:
    expected_ids = set(test_case["expected_chunk_ids"])
    retrieved_ids = [result["chunk_id"] for result in retrieved]
    expected_ranks = {
        chunk_id: retrieved_ids.index(chunk_id) + 1 if chunk_id in retrieved_ids else None
        for chunk_id in expected_ids
    }
    row = {
        "company": test_case["company"],
        "ticker": test_case["ticker"],
        "question_type": test_case["question_type"],
        "question": test_case["question"],
        "expected_chunk_ids": list(expected_ids),
        "expected_chunk_count": len(expected_ids),
        "expected_ranks": expected_ranks,
    }
    for k in K_VALUES:
        relevant_count = len(expected_ids & set(retrieved_ids[:k]))
        relevant_ranks = [rank for rank in expected_ranks.values() if rank is not None and rank <= k]
        row[f"recall@{k}"] = relevant_count / len(expected_ids)
        row[f"hit@{k}"] = int(relevant_count > 0)
        row[f"complete@{k}"] = int(relevant_count == len(expected_ids))
        row[f"mrr@{k}"] = 1.0 / min(relevant_ranks) if relevant_ranks else 0.0
    return row


def evaluate_bm25_retrieval(
    test_cases: list[dict[str, Any]],
    retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
) -> tuple[pd.DataFrame, float]:
    corpus_chunk_ids = {chunk["chunk_id"] for chunk in all_chunks}
    missing_gold_ids = {
        chunk_id
        for case in test_cases
        for chunk_id in case["expected_chunk_ids"]
        if chunk_id not in corpus_chunk_ids
    }
    if missing_gold_ids:
        raise ValueError("Ground-truth chunk IDs missing from current corpus:\n" + "\n".join(sorted(missing_gold_ids)))

    rows = []
    total_start = time.perf_counter()
    for index, case in enumerate(test_cases, start=1):
        retrieved, latency = bm25_retrieve(case["question"], retriever, all_chunks)
        row = evaluate_query(case, retrieved)
        row["latency_seconds"] = latency
        row["retrieved_chunk_ids"] = [result["chunk_id"] for result in retrieved]
        rows.append(row)
        if index % 25 == 0 or index == len(test_cases):
            print(f"Evaluated {index}/{len(test_cases)} queries")
    return pd.DataFrame(rows), time.perf_counter() - total_start


def metric_columns() -> list[str]:
    return [metric for k in K_VALUES for metric in (f"recall@{k}", f"hit@{k}", f"complete@{k}", f"mrr@{k}")]


def print_evaluation_summary(dataframe: pd.DataFrame, total_time: float) -> None:
    metrics = metric_columns()
    print("\nOVERALL BM25 RETRIEVAL")
    overall = dataframe[metrics].mean()
    for k in K_VALUES:
        print(f"@{k:<2} | Recall={overall[f'recall@{k}']:.4f} | Hit={overall[f'hit@{k}']:.4f} | Complete={overall[f'complete@{k}']:.4f} | MRR={overall[f'mrr@{k}']:.4f}")

    print(f"\nQueries: {len(dataframe)}\nTotal:  {total_time:.3f}s\nMean:   {dataframe['latency_seconds'].mean():.4f}s\nMedian: {dataframe['latency_seconds'].median():.4f}s\nP95:    {dataframe['latency_seconds'].quantile(0.95):.4f}s")
    for title, groups in (("BY QUESTION TYPE", ["question_type"]), ("BY COMPANY", ["ticker", "company"])):
        grouped = dataframe.groupby(groups)[metrics].mean().round(4).reset_index()
        columns = groups + metrics
        widths = {column: max(len(column), max(len(f"{value:.4f}") if column in metrics else len(str(value)) for value in grouped[column])) for column in columns}

        print(f"\n{title}")
        print(" | ".join(f"{column:<{widths[column]}}" for column in columns))
        print("-+-".join("-" * widths[column] for column in columns))
        for _, row in grouped.iterrows():
            print(" | ".join(f"{row[column]:>{widths[column]}.4f}" if column in metrics else f"{str(row[column]):<{widths[column]}}" for column in columns))


def default_output_path(output_directory: Path) -> Path:
    directory = output_directory / "bm25_retrieval"
    versions = [
        int(match.group(1))
        for path in directory.glob("v*")
        if path.is_dir() and (match := re.fullmatch(r"v(\d+)", path.name))
    ]
    return directory / f"v{max(versions, default=0) + 1}" / "evaluation.jsonl"


def write_results(output_path: Path, dataframe: pd.DataFrame, metadata: dict[str, Any], overwrite: bool) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}. Pass --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with output_path.open(mode, encoding="utf-8") as file:
        for record in dataframe.to_dict(orient="records"):
            file.write(json.dumps({**record, **metadata}, ensure_ascii=False) + "\n")


def write_summary(
    output_path: Path,
    dataframe: pd.DataFrame,
    total_time: float,
    metadata: dict[str, Any],
    overwrite: bool,
) -> Path:
    summary_path = output_path.with_suffix(".summary.json")

    if summary_path.exists() and not overwrite:
        raise FileExistsError(
            f"Summary already exists: {summary_path}. "
            "Pass --overwrite to replace it."
        )

    metrics = metric_columns()

    overall = dataframe[metrics].mean()

    by_question_type = (
        dataframe.groupby("question_type")[metrics]
        .mean()
        .to_dict(orient="index")
    )

    by_company = (
        dataframe.groupby(["ticker", "company"])[metrics]
        .mean()
        .reset_index()
    )

    company_metrics = {
        row["ticker"]: {
            "company": row["company"],
            **{
                metric: float(row[metric])
                for metric in metrics
            },
        }
        for _, row in by_company.iterrows()
    }

    summary = {
        **metadata,
        "query_count": len(dataframe),
        "latency": {
            "total_seconds": total_time,
            "mean_seconds": float(dataframe["latency_seconds"].mean()),
            "median_seconds": float(dataframe["latency_seconds"].median()),
            "p95_seconds": float(
                dataframe["latency_seconds"].quantile(0.95)
            ),
        },
        "overall": {
            metric: float(overall[metric])
            for metric in metrics
        },
        "by_question_type": {
            question_type: {
                metric: float(value)
                for metric, value in values.items()
            }
            for question_type, values in by_question_type.items()
        },
        "by_company": company_metrics,
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"

    with summary_path.open(mode, encoding="utf-8") as file:
        json.dump(summary, file, indent=2, ensure_ascii=False)

    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate BM25 retrieval over the fixed annual-filing corpus.")
    parser.add_argument("--chunks-directory", type=Path, default=DEFAULT_CHUNKS_DIRECTORY)
    parser.add_argument("--test-queries", type=Path, default=DEFAULT_TEST_QUERIES_PATH)
    parser.add_argument("--output", type=Path, help="Detailed JSONL results path.")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    all_chunks = load_corpus(arguments.chunks_directory)
    print(f"Total chunks: {len(all_chunks)}")
    index_start = time.perf_counter()
    retriever = build_bm25_index(all_chunks)
    print(f"BM25 indexing: {time.perf_counter() - index_start:.3f}s")
    dataframe, total_time = evaluate_bm25_retrieval(load_test_queries(arguments.test_queries), retriever, all_chunks)
    print_evaluation_summary(dataframe, total_time)

    output_path = arguments.output or default_output_path(arguments.output_directory)
    metadata = {
        "run_number": None if arguments.output else int(output_path.parent.name[1:]),
        "timestamp": datetime.now().astimezone().isoformat(),
        "embedding_model": None,
        "reranker_model": None,
        "retrieval_method": bm25_retrieve.__name__,
        "retrieval_parameters": {"top_k": MAX_K, "tokenizer": "bm25s.tokenize", "bm25_method": "lucene"},
        "evaluation_dataset": str(arguments.test_queries),
        "device": None,
    }

    write_results(
        output_path,
        dataframe,
        metadata,
        arguments.overwrite,
    )

    summary_path = write_summary(
        output_path,
        dataframe,
        total_time,
        metadata,
        arguments.overwrite,
    )

    print(f"\nSaved detailed results to: {output_path}")
    print(f"Saved summary to:          {summary_path}")


if __name__ == "__main__":
    main()
