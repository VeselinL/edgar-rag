"""Strict semantic-retrieval evaluator for versioned filing artifacts."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.embeddings.embed_chunks import (
    DEFAULT_MODEL_NAME,
    load_chunks,
    load_model,
    prepare_query_text,
    sha256_file,
    validate_embedding_artifacts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as output_file:
            temporary = Path(output_file.name)
            json.dump(value, output_file, indent=2, ensure_ascii=False)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def metric_summary(results: list[dict]) -> dict:
    if not results:
        return {
            "question_count": 0,
            "mean_recall_at_k": 0.0,
            "mean_reciprocal_rank_at_k": 0.0,
            "hit_rate_at_k": 0.0,
        }
    return {
        "question_count": len(results),
        "mean_recall_at_k": statistics.mean(
            result["recall_at_k"] for result in results
        ),
        "mean_reciprocal_rank_at_k": statistics.mean(
            result["reciprocal_rank_at_k"] for result in results
        ),
        "hit_rate_at_k": statistics.mean(
            result["reciprocal_rank_at_k"] > 0 for result in results
        ),
    }


def grouped_metrics(results: list[dict], field: str) -> dict:
    groups = defaultdict(list)
    for result in results:
        groups[result[field]].append(result)
    return {
        value: metric_summary(group)
        for value, group in sorted(groups.items())
    }


def validate_dataset(dataset: dict, chunks_path: Path, chunks: list[dict]) -> None:
    if dataset.get("schema_version") != 2:
        raise ValueError("Evaluation dataset schema_version must be 2")
    table_chunks = [
        chunk for chunk in chunks if chunk.get("content_type") == "table"
    ]
    if not table_chunks:
        raise ValueError("Evaluation chunk artifact contains no logical tables")
    table_versions = {
        chunk.get("table_schema_version") for chunk in table_chunks
    }
    heuristic_versions = {
        chunk.get("table_heuristics_version") for chunk in table_chunks
    }
    config_hashes = {chunk.get("chunking_config_sha256") for chunk in chunks}
    if (
        None in table_versions
        or len(table_versions) != 1
        or None in heuristic_versions
        or len(heuristic_versions) != 1
        or None in config_hashes
        or len(config_hashes) != 1
    ):
        raise ValueError("Evaluation chunks contain mixed table release metadata")
    required = {
        "source_chunks_sha256": sha256_file(chunks_path),
        "chunk_schema_version": 3,
        "table_schema_version": next(iter(table_versions)),
        "table_heuristics_version": next(iter(heuristic_versions)),
        "chunking_config_sha256": next(iter(config_hashes)),
        "record_count": len(dataset.get("records") or []),
    }
    for field, expected in required.items():
        if dataset.get(field) != expected:
            raise ValueError(
                f"Evaluation dataset {field} mismatch: "
                f"{dataset.get(field)!r} != {expected!r}"
            )
    if dataset.get("record_count") != 60:
        raise ValueError("Mobileye evaluation dataset must preserve all 60 records")
    chunk_ids = {chunk["chunk_id"] for chunk in chunks}
    for record in dataset["records"]:
        if record.get("review_state", {}).get("status") != "approved":
            raise ValueError(f"Evaluation record {record.get('id')} is not approved")
        relevant = record.get("new_relevant_chunk_ids") or []
        if not relevant or len(relevant) != len(set(relevant)):
            raise ValueError(
                f"Evaluation record {record.get('id')} has invalid gold chunks"
            )
        if not set(relevant) <= chunk_ids:
            raise ValueError(
                f"Evaluation record {record.get('id')} references unknown chunks"
            )


def evaluate(
    dataset_path: Path,
    chunks_path: Path,
    embedding_path: Path,
    embedding_manifest_path: Path,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    device: str | None = None,
    batch_size: int = 32,
    top_k: int = 10,
    baseline_id: str = "mobileye-bgebase-table-v2-chunk-v3.20260813-r2",
) -> dict:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    integrity = validate_embedding_artifacts(
        chunks_path, embedding_path, embedding_manifest_path
    )
    chunks = load_chunks(chunks_path)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    validate_dataset(dataset, chunks_path, chunks)
    manifest = json.loads(embedding_manifest_path.read_text(encoding="utf-8"))
    if manifest.get("model_name") != model_name:
        raise ValueError("Evaluator model differs from embedding manifest")
    with np.load(embedding_path, allow_pickle=False) as saved:
        vectors = np.asarray(saved["embeddings"], dtype=np.float32)
        vector_ids = saved["chunk_ids"].tolist()
    if vector_ids != [chunk["chunk_id"] for chunk in chunks]:
        raise ValueError("Embedding order differs from source chunks")

    model = load_model(model_name, device)
    queries = [record["query"] for record in dataset["records"]]
    query_inputs = [prepare_query_text(query, model_name) for query in queries]
    started = time.perf_counter()
    query_vectors = np.asarray(
        model.encode(
            query_inputs,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ),
        dtype=np.float32,
    )
    encoding_seconds = time.perf_counter() - started
    search_started = time.perf_counter()
    similarities = query_vectors @ vectors.T
    ranked_indexes = np.argsort(-similarities, axis=1)[:, :top_k]
    search_seconds = time.perf_counter() - search_started

    results = []
    for record, indexes, scores in zip(
        dataset["records"], ranked_indexes, similarities
    ):
        retrieved = [vector_ids[index] for index in indexes]
        relevant = record["new_relevant_chunk_ids"]
        relevant_set = set(relevant)
        hits = [chunk_id for chunk_id in retrieved if chunk_id in relevant_set]
        first_rank = next(
            (
                rank
                for rank, chunk_id in enumerate(retrieved, start=1)
                if chunk_id in relevant_set
            ),
            None,
        )
        results.append(
            {
                "id": record["id"],
                "evaluation_set": record["evaluation_set"],
                "category": record["category"],
                "evidence_type": record["evidence_type"],
                "query": record["query"],
                "relevant_chunk_ids": relevant,
                "retrieved_chunk_ids": retrieved,
                "retrieved_scores": [float(scores[index]) for index in indexes],
                "hit_chunk_ids": hits,
                "recall_at_k": len(hits) / len(relevant),
                "first_relevant_rank": first_rank,
                "reciprocal_rank_at_k": 1 / first_rank if first_rank else 0.0,
            }
        )

    return {
        "schema_version": 1,
        "baseline_id": baseline_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "historical_comparison": {
            "status": "historical_pre-migration_not_recomputed",
            "source": "notebooks/compare_embeddings.ipynb historical output",
            "mean_recall_at_10": 0.721,
            "single_narrative_recall_at_10": 0.833,
            "single_table_recall_at_10": 0.625,
            "multi_chunk_recall_at_10": 0.562,
        },
        "dataset_path": str(dataset_path.resolve()),
        "dataset_sha256": sha256_file(dataset_path),
        "chunks_path": str(chunks_path.resolve()),
        "chunks_sha256": sha256_file(chunks_path),
        "embedding_path": str(embedding_path.resolve()),
        "embedding_manifest_path": str(embedding_manifest_path.resolve()),
        "embedding_manifest_sha256": sha256_file(embedding_manifest_path),
        "embedding_integrity": integrity,
        "model_name": model_name,
        "top_k": top_k,
        "question_count": len(results),
        "metrics": {
            "overall": metric_summary(results),
            "by_evaluation_set": grouped_metrics(results, "evaluation_set"),
            "by_category": grouped_metrics(results, "category"),
            "by_evidence_type": grouped_metrics(results, "evidence_type"),
        },
        "latency": {
            "query_encoding_seconds": encoding_seconds,
            "vector_search_seconds": search_seconds,
            "mean_query_encoding_ms": encoding_seconds * 1000 / len(results),
            "mean_vector_search_ms": search_seconds * 1000 / len(results),
        },
        "regressions_for_review": [
            result
            for result in results
            if result["recall_at_k"] < 1.0
        ],
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate strict semantic retrieval over Mobileye gold v2."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "evaluation"
        / "mobileye_retrieval_gold_v2.json",
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "chunks"
        / "MBLY"
        / "2025-10-K.chunks.jsonl",
    )
    parser.add_argument(
        "--embeddings",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "embeddings"
        / "MBLY"
        / "2025-10-K.bgebase.embeddings.npz",
    )
    parser.add_argument("--embedding-manifest", type=Path)
    parser.add_argument("--model-name", default="bgebase")
    parser.add_argument("--device")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--baseline-id",
        default="mobileye-bgebase-table-v2-chunk-v3.20260813-r2",
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    embedding_manifest = arguments.embedding_manifest or arguments.embeddings.with_suffix(
        ".manifest.json"
    )
    report = evaluate(
        arguments.dataset,
        arguments.chunks,
        arguments.embeddings,
        embedding_manifest,
        model_name=arguments.model_name,
        device=arguments.device,
        batch_size=arguments.batch_size,
        top_k=arguments.top_k,
        baseline_id=arguments.baseline_id,
    )
    write_json_atomic(arguments.output, report)
    print(f"Wrote retrieval baseline to {arguments.output}")
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
