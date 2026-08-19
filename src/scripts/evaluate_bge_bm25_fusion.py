"""Evaluate reciprocal-rank fusion of BGE and BM25 retrieval."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import bm25s
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

from src.embeddings.embed_chunks import MODEL_CONFIGS


DEFAULT_MODEL_NAME = "bgebase"
DEFAULT_EMBEDDINGS_DIRECTORY = Path("data/embeddings")
DEFAULT_CHUNKS_DIRECTORY = Path("data/chunks")
DEFAULT_TEST_QUERIES_PATH = Path("data/evaluation/test_queries.jsonl")
DEFAULT_OUTPUT_DIRECTORY = Path("data/evaluation")
K_VALUES = [1, 3, 5, 10, 15, 20, 30]
MAX_K = max(K_VALUES)
DEFAULT_RRF_K = 60

FILINGS = {
    "AUR": "2025-10-K", "TSLA": "2025-10-K", "MBLY": "2025-10-K",
    "GOOGL": "2025-10-K", "GM": "2025-10-K", "F": "2025-10-K",
    "NVDA": "2026-10-K", "QCOM": "2025-10-K", "APTV": "2025-10-K",
    "OUST": "2025-10-K",
}


def load_corpus(
    embeddings_directory: Path, chunks_directory: Path, model_name: str
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    embeddings_by_company = []
    all_chunks = []
    for ticker, filing_name in FILINGS.items():
        paths = list((embeddings_directory / ticker).glob(f"{filing_name}.{model_name}*.npz"))
        if len(paths) != 1:
            raise ValueError(f"Expected one {model_name} embedding file for {ticker}/{filing_name}, found: {paths}")

        embeddings = np.load(paths[0])["embeddings"]
        chunk_path = chunks_directory / ticker / f"{filing_name}.chunks.jsonl"
        with chunk_path.open("r", encoding="utf-8") as file:
            chunks = [json.loads(line) for line in file if line.strip()]
        if len(embeddings) != len(chunks):
            raise ValueError(f"{ticker}: {len(embeddings)} embeddings != {len(chunks)} chunks")
        embeddings_by_company.append(embeddings)
        all_chunks.extend(chunks)

    all_embeddings = np.vstack(embeddings_by_company)
    if len(all_embeddings) != len(all_chunks):
        raise ValueError("Embedding and chunk corpus sizes differ.")
    return all_embeddings, all_chunks


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


def dense_candidate_indices(
    query: str, model: SentenceTransformer, query_prefix: str, normalized_embeddings: np.ndarray
) -> list[int]:
    query_embedding = model.encode(query_prefix + query, normalize_embeddings=True)
    return [int(index) for index in np.argsort(normalized_embeddings @ query_embedding)[-MAX_K:][::-1]]


def bm25_candidate_indices(query: str, retriever: bm25s.BM25) -> list[int]:
    indices, _ = retriever.retrieve(bm25s.tokenize(query), k=MAX_K)
    return [int(index) for index in indices[0]]


def fused_retrieve(
    query: str,
    model: SentenceTransformer,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int,
) -> tuple[list[dict[str, Any]], float]:
    start = time.perf_counter()
    dense_indices = dense_candidate_indices(query, model, query_prefix, normalized_embeddings)
    bm25_indices = bm25_candidate_indices(query, bm25_retriever)

    rrf_scores: dict[int, float] = {}
    source_ranks: dict[int, dict[str, int]] = {}
    for source, indices in (("dense", dense_indices), ("bm25", bm25_indices)):
        for rank, index in enumerate(indices, start=1):
            rrf_scores[index] = rrf_scores.get(index, 0.0) + 1.0 / (rrf_k + rank)
            source_ranks.setdefault(index, {})[source] = rank

    top_indices = sorted(rrf_scores, key=lambda index: (-rrf_scores[index], index))[:MAX_K]
    results = [
        {
            "rank": rank,
            "chunk_id": all_chunks[index]["chunk_id"],
            "ticker": all_chunks[index].get("ticker"),
            "content_type": all_chunks[index].get("content_type"),
            "score": rrf_scores[index],
            "index": index,
            "dense_rank": source_ranks[index].get("dense"),
            "bm25_rank": source_ranks[index].get("bm25"),
        }
        for rank, index in enumerate(top_indices, start=1)
    ]
    return results, time.perf_counter() - start


def evaluate_query(
    test_case: dict[str, Any],
    retrieved: list[dict[str, Any]],
    chunks_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected_ids = set(test_case["expected_chunk_ids"])
    retrieved_ids = [result["chunk_id"] for result in retrieved]
    expected_ranks = {chunk_id: retrieved_ids.index(chunk_id) + 1 if chunk_id in retrieved_ids else None for chunk_id in expected_ids}
    row = {
        "company": test_case["company"], "ticker": test_case["ticker"],
        "question_type": test_case["question_type"], "question": test_case["question"],
        "expected_chunk_ids": list(expected_ids), "expected_chunk_count": len(expected_ids),
        "expected_content_types": sorted(
            {chunks_by_id[chunk_id].get("content_type", "unknown") for chunk_id in expected_ids}
        ),
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


def evaluate_fusion(
    test_cases: list[dict[str, Any]], model: SentenceTransformer, query_prefix: str,
    normalized_embeddings: np.ndarray, bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]], rrf_k: int,
) -> tuple[pd.DataFrame, float]:
    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in all_chunks}
    corpus_chunk_ids = set(chunks_by_id)
    missing_gold_ids = {chunk_id for case in test_cases for chunk_id in case["expected_chunk_ids"] if chunk_id not in corpus_chunk_ids}
    if missing_gold_ids:
        raise ValueError("Ground-truth chunk IDs missing from current corpus:\n" + "\n".join(sorted(missing_gold_ids)))

    rows = []
    total_start = time.perf_counter()
    for index, case in enumerate(test_cases, start=1):
        retrieved, latency = fused_retrieve(case["question"], model, query_prefix, normalized_embeddings, bm25_retriever, all_chunks, rrf_k)
        row = evaluate_query(case, retrieved, chunks_by_id)
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
    overall = dataframe[metrics].mean()
    print("\nOVERALL BGE + BM25 RRF RETRIEVAL")
    for k in K_VALUES:
        print(f"@{k:<2} | Recall={overall[f'recall@{k}']:.4f} | Hit={overall[f'hit@{k}']:.4f} | Complete={overall[f'complete@{k}']:.4f} | MRR={overall[f'mrr@{k}']:.4f}")
    print(f"\nQueries: {len(dataframe)}\nTotal:  {total_time:.3f}s\nMean:   {dataframe['latency_seconds'].mean():.4f}s\nMedian: {dataframe['latency_seconds'].median():.4f}s\nP95:    {dataframe['latency_seconds'].quantile(0.95):.4f}s")
    reporting_dataframe = dataframe.explode("expected_content_types").rename(
        columns={"expected_content_types": "expected_chunk_type"}
    )
    for title, groups in (
        ("BY QUESTION TYPE", ["question_type"]),
        ("BY COMPANY", ["ticker", "company"]),
        ("BY EXPECTED CHUNK TYPE", ["expected_chunk_type"]),
    ):
        source = reporting_dataframe if groups == ["expected_chunk_type"] else dataframe
        grouped = source.groupby(groups)[metrics].mean().round(4).reset_index()
        columns = groups + metrics
        widths = {column: max(len(column), max(len(f"{value:.4f}") if column in metrics else len(str(value)) for value in grouped[column])) for column in columns}

        print(f"\n{title}")
        print(" | ".join(f"{column:<{widths[column]}}" for column in columns))
        print("-+-".join("-" * widths[column] for column in columns))
        for _, row in grouped.iterrows():
            print(" | ".join(f"{row[column]:>{widths[column]}.4f}" if column in metrics else f"{str(row[column]):<{widths[column]}}" for column in columns))


def default_output_path(output_directory: Path, model_name: str) -> Path:
    directory = output_directory / "fusion_retrieval" / model_name
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
    with output_path.open("w" if overwrite else "x", encoding="utf-8") as file:
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
    by_question_type = dataframe.groupby("question_type")[metrics].mean().to_dict(orient="index")
    by_company = dataframe.groupby(["ticker", "company"])[metrics].mean().reset_index()
    company_metrics = {
        row["ticker"]: {
            "company": row["company"],
            **{metric: float(row[metric]) for metric in metrics},
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
            "p95_seconds": float(dataframe["latency_seconds"].quantile(0.95)),
        },
        "overall": {metric: float(overall[metric]) for metric in metrics},
        "by_question_type": {
            question_type: {metric: float(value) for metric, value in values.items()}
            for question_type, values in by_question_type.items()
        },
        "by_company": company_metrics,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w" if overwrite else "x", encoding="utf-8") as file:
        json.dump(summary, file, indent=2, ensure_ascii=False)
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate reciprocal-rank fusion of BGE and BM25 retrieval.")
    parser.add_argument("--embeddings-directory", type=Path, default=DEFAULT_EMBEDDINGS_DIRECTORY)
    parser.add_argument("--chunks-directory", type=Path, default=DEFAULT_CHUNKS_DIRECTORY)
    parser.add_argument("--test-queries", type=Path, default=DEFAULT_TEST_QUERIES_PATH)
    parser.add_argument("--output", type=Path, help="Detailed JSONL results path.")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--model-name", choices=tuple(MODEL_CONFIGS), default=DEFAULT_MODEL_NAME)
    parser.add_argument("--device")
    parser.add_argument("--rrf-k", type=int, default=DEFAULT_RRF_K)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()
    if arguments.rrf_k < 0:
        raise ValueError("--rrf-k must be non-negative")

    config = MODEL_CONFIGS[arguments.model_name]
    all_embeddings, all_chunks = load_corpus(arguments.embeddings_directory, arguments.chunks_directory, arguments.model_name)
    normalized_embeddings = all_embeddings / np.clip(np.linalg.norm(all_embeddings, axis=1, keepdims=True), 1e-12, None)
    print(f"Total chunks: {len(all_chunks)}")
    print(f"Embedding matrix: {all_embeddings.shape}")
    index_start = time.perf_counter()
    bm25_retriever = build_bm25_index(all_chunks)
    print(f"BM25 indexing: {time.perf_counter() - index_start:.3f}s")
    model = SentenceTransformer(config["repository"], device=arguments.device)
    dataframe, total_time = evaluate_fusion(load_test_queries(arguments.test_queries), model, config["query_prefix"], normalized_embeddings, bm25_retriever, all_chunks, arguments.rrf_k)
    print_evaluation_summary(dataframe, total_time)

    output_path = arguments.output or default_output_path(arguments.output_directory, arguments.model_name)
    metadata = {
        "run_number": None if arguments.output else int(output_path.parent.name[1:]),
        "timestamp": datetime.now().astimezone().isoformat(), "embedding_model": config["repository"],
        "reranker_model": None, "retrieval_method": fused_retrieve.__name__,
        "retrieval_parameters": {"top_k": MAX_K, "dense_candidate_k": MAX_K, "bm25_candidate_k": MAX_K, "fusion": "reciprocal_rank_fusion", "rrf_k": arguments.rrf_k, "query_prefix": config["query_prefix"]},
        "evaluation_dataset": str(arguments.test_queries), "device": str(model.device),
    }
    write_results(output_path, dataframe, metadata, arguments.overwrite)
    summary_path = write_summary(output_path, dataframe, total_time, metadata, arguments.overwrite)
    print(f"\nSaved detailed results to: {output_path}")
    print(f"Saved summary to: {summary_path}")


if __name__ == "__main__":
    main()
