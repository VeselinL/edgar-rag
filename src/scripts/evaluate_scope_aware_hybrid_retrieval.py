"""Evaluate scope-aware BGE + BM25 reciprocal-rank-fusion retrieval.

The scope policy is deliberately query-only: company mentions are matched against
known aliases, while all retrieval filters use immutable chunk ticker metadata.
"""

from __future__ import annotations

import argparse
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
from src.filings.corpus import COMPANY_ALIASES
from src.retrieval.scope_aware import (
    COMPARISON_CUES,
    DEFAULT_FINAL_EVIDENCE_K,
    DEFAULT_MIN_CHUNKS_PER_SUBQUERY,
    DEFAULT_MULTI_SUBQUERY_BONUS,
    DEFAULT_SUBQUERY_RETRIEVAL_K,
    ScopeAwareRetriever,
    detect_companies,
    detect_scope,
    retrieve_generation_context as shared_retrieve_generation_context,
    scope_aware_hybrid_retrieve as shared_scope_aware_hybrid_retrieve,
)
from src.resolution.companies import CompanyResolution, ENUMERATION_CUES
from src.scripts.evaluate_bge_bm25_fusion import (
    DEFAULT_CHUNKS_DIRECTORY,
    DEFAULT_EMBEDDINGS_DIRECTORY,
    DEFAULT_MODEL_NAME,
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_TEST_QUERIES_PATH,
    FILINGS,
    K_VALUES,
    MAX_K,
    build_bm25_index,
    evaluate_query,
    load_corpus,
    load_test_queries,
    print_evaluation_summary,
    write_results,
    write_summary,
)


DEFAULT_RRF_K = 60
DEFAULT_ANCHORED_COMPANY_K = 3
DEFAULT_ENUMERATION_CANDIDATE_K = 30
ENUMERATION_MIN_RELATIVE_RRF_SCORE = 0.60
SUBQUERY_RETRIEVAL_K = DEFAULT_SUBQUERY_RETRIEVAL_K
FINAL_CONTEXT_K = DEFAULT_FINAL_EVIDENCE_K
MIN_CHUNKS_PER_SUBQUERY = DEFAULT_MIN_CHUNKS_PER_SUBQUERY
MULTI_SUBQUERY_BONUS = DEFAULT_MULTI_SUBQUERY_BONUS

def dense_candidate_indices(
    query: str,
    model: SentenceTransformer,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    candidate_k: int,
    allowed_indices: np.ndarray | None = None,
) -> tuple[list[int], dict[int, float]]:
    query_embedding = model.encode(query_prefix + query, normalize_embeddings=True)
    scores = normalized_embeddings @ query_embedding
    pool = np.arange(len(scores)) if allowed_indices is None else allowed_indices
    top_count = min(candidate_k, len(pool))
    ranked = pool[np.argsort(scores[pool])[-top_count:][::-1]]
    indices = [int(index) for index in ranked]
    return indices, {index: float(scores[index]) for index in indices}


def bm25_candidate_indices(
    query: str,
    retriever: bm25s.BM25,
    corpus_size: int,
    candidate_k: int,
    allowed_indices: np.ndarray | None = None,
) -> tuple[list[int], dict[int, float]]:
    # The existing global index is unchanged. A scoped request filters its ranking.
    retrieval_k = corpus_size if allowed_indices is not None else candidate_k
    indices, scores = retriever.retrieve(bm25s.tokenize(query), k=retrieval_k)
    pairs = list(zip(indices[0].astype(int).tolist(), scores[0].tolist()))
    if allowed_indices is not None:
        allowed = set(allowed_indices.tolist())
        pairs = [(index, score) for index, score in pairs if index in allowed]
    pairs = pairs[:candidate_k]
    return [index for index, _ in pairs], {index: float(score) for index, score in pairs}


def hybrid_retrieve(
    query: str,
    model: SentenceTransformer,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int,
    candidate_k: int,
    allowed_tickers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Run the same dense + BM25 + RRF pipeline, optionally metadata-filtered."""
    allowed_indices = None
    if allowed_tickers is not None:
        allowed_indices = np.asarray(
            [index for index, chunk in enumerate(all_chunks) if chunk.get("ticker") in allowed_tickers]
        )
        if not len(allowed_indices):
            return []

    dense_indices, dense_scores = dense_candidate_indices(
        query, model, query_prefix, normalized_embeddings, candidate_k, allowed_indices
    )
    bm25_indices, bm25_scores = bm25_candidate_indices(
        query, bm25_retriever, len(all_chunks), candidate_k, allowed_indices
    )
    rrf_scores: dict[int, float] = {}
    source_ranks: dict[int, dict[str, int]] = {}
    for source, indices in (("dense", dense_indices), ("bm25", bm25_indices)):
        for rank, index in enumerate(indices, start=1):
            rrf_scores[index] = rrf_scores.get(index, 0.0) + 1.0 / (rrf_k + rank)
            source_ranks.setdefault(index, {})[source] = rank

    ranked_indices = sorted(rrf_scores, key=lambda index: (-rrf_scores[index], index))
    return [
        {
            "chunk_id": all_chunks[index]["chunk_id"],
            "ticker": all_chunks[index].get("ticker"),
            "content_type": all_chunks[index].get("content_type"),
            "index": index,
            "rrf_score": rrf_scores[index],
            "dense_rank": source_ranks[index].get("dense"),
            "dense_score": dense_scores.get(index),
            "bm25_rank": source_ranks[index].get("bm25"),
            "bm25_score": bm25_scores.get(index),
        }
        for index in ranked_indices
    ]


def merge_anchored_global_results(
    global_results: list[dict[str, Any]],
    anchored_results: dict[str, list[dict[str, Any]]],
    top_k: int,
    anchored_company_k: int,
) -> list[dict[str, Any]]:
    """Keep global order, then append new top-RRF evidence for each named ticker."""
    selected = [dict(result, retrieval_source="global", retrieval_scope="anchored_global") for result in global_results[:top_k]]
    selected_ids = {result["chunk_id"] for result in selected}
    for ticker in sorted(anchored_results):
        for scoped_rank, result in enumerate(anchored_results[ticker][:anchored_company_k], start=1):
            if result["chunk_id"] in selected_ids:
                for existing in selected:
                    if existing["chunk_id"] == result["chunk_id"]:
                        existing["retrieval_source"] = "global_and_anchored_company"
                        existing["anchored_company_ticker"] = ticker
                        existing["anchored_company_rank"] = scoped_rank
                        existing["anchored_company_rrf_score"] = result["rrf_score"]
                        break
                continue
            selected.append(
                dict(
                    result,
                    retrieval_source="anchored_company",
                    retrieval_scope="anchored_global",
                    anchored_company_ticker=ticker,
                    anchored_company_rank=scoped_rank,
                    anchored_company_rrf_score=result["rrf_score"],
                )
            )
            selected_ids.add(result["chunk_id"])
    return selected


def select_enumeration_results(
    global_results: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """Select strong per-company evidence, then preserve global RRF relevance.

    A ticker qualifies only when its best candidate is supported by both retrieval
    methods and its RRF score is at least a fixed fraction of the global best.
    This avoids assigning slots to every company while exposing competitive
    evidence that would otherwise be crowded out by a dominant ticker.
    """
    if not global_results or top_k <= 0:
        return []

    best_score = global_results[0]["rrf_score"]
    minimum_score = best_score * ENUMERATION_MIN_RELATIVE_RRF_SCORE
    best_by_ticker: dict[str, tuple[int, dict[str, Any]]] = {}
    for global_rank, result in enumerate(global_results, start=1):
        ticker = result.get("ticker")
        if ticker and ticker not in best_by_ticker:
            best_by_ticker[ticker] = (global_rank, result)

    qualified_representatives = [
        (global_rank, result)
        for global_rank, result in best_by_ticker.values()
        if result["rrf_score"] >= minimum_score
        and result["dense_rank"] is not None
        and result["bm25_rank"] is not None
    ]
    selected_ids = {result["chunk_id"] for _, result in qualified_representatives}
    # Keep the selector safe when the corpus has more companies than the output
    # budget: strongest representatives win when there are too many.
    if len(selected_ids) > top_k:
        selected_ids = {
            result["chunk_id"]
            for _, result in sorted(
                qualified_representatives, key=lambda item: (-item[1]["rrf_score"], item[0])
            )[:top_k]
        }

    selected: list[dict[str, Any]] = []
    for global_rank, result in enumerate(global_results, start=1):
        if result["chunk_id"] in selected_ids:
            selected.append(
                dict(
                    result,
                    retrieval_source="global_enumeration_representative",
                    retrieval_scope="enumeration",
                    enumeration_global_rank=global_rank,
                    enumeration_company_representative=True,
                )
            )
    for global_rank, result in enumerate(global_results, start=1):
        if len(selected) >= top_k:
            break
        if result["chunk_id"] in selected_ids:
            continue
        selected.append(
            dict(
                result,
                retrieval_source="global_enumeration",
                retrieval_scope="enumeration",
                enumeration_global_rank=global_rank,
                enumeration_company_representative=False,
            )
        )
        selected_ids.add(result["chunk_id"])
    return sorted(selected, key=lambda result: result["enumeration_global_rank"])


def scope_aware_hybrid_retrieve(
    query: str,
    model: SentenceTransformer,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int,
    candidate_k: int = MAX_K,
    top_k: int = MAX_K,
    anchored_company_k: int = DEFAULT_ANCHORED_COMPANY_K,
    resolved_scope: tuple[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Compatibility wrapper around the shared production retrieval policy."""
    return shared_scope_aware_hybrid_retrieve(
        query,
        model,
        query_prefix,
        normalized_embeddings,
        bm25_retriever,
        all_chunks,
        rrf_k,
        candidate_k,
        top_k,
        anchored_company_k,
        resolved_scope,
    )


def retrieve_generation_context(
    original_query: str,
    subqueries: list[str],
    model: SentenceTransformer,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int,
    candidate_k: int = MAX_K,
    anchored_company_k: int = DEFAULT_ANCHORED_COMPANY_K,
    subquery_retrieval_k: int = SUBQUERY_RETRIEVAL_K,
    final_context_k: int = FINAL_CONTEXT_K,
    min_chunks_per_subquery: int = MIN_CHUNKS_PER_SUBQUERY,
    multi_subquery_bonus: float = MULTI_SUBQUERY_BONUS,
) -> dict[str, Any]:
    """Compatibility wrapper around the shared notebook/API context selector."""
    return shared_retrieve_generation_context(
        original_query=original_query,
        subqueries=subqueries,
        model=model,
        query_prefix=query_prefix,
        normalized_embeddings=normalized_embeddings,
        bm25_retriever=bm25_retriever,
        all_chunks=all_chunks,
        rrf_k=rrf_k,
        candidate_k=candidate_k,
        anchored_company_k=anchored_company_k,
        subquery_retrieval_k=subquery_retrieval_k,
        final_context_k=final_context_k,
        min_chunks_per_subquery=min_chunks_per_subquery,
        multi_subquery_bonus=multi_subquery_bonus,
    )


def evaluate_scope_aware_retrieval(
    retriever: ScopeAwareRetriever,
    query: str,
    subqueries: list[str] | None = None,
    company_resolution: CompanyResolution | None = None,
    subquery_targets: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Return the diagnostics compared with the API before source normalization."""
    outcome = retriever.retrieve(
        query, subqueries, company_resolution, subquery_targets
    )
    return {
        "detected_companies": list(outcome.detected_companies),
        "comparison": outcome.comparison,
        "retrieval_scopes": list(outcome.retrieval_scopes),
        "subqueries": list(outcome.subqueries),
        "coverage_by_subquery": list(outcome.coverage_by_subquery),
        "selected_evidence_companies": list(outcome.selected_evidence_companies),
        "final_evidence_count": len(outcome.evidence),
        "chunk_ids": list(outcome.chunk_ids),
        "policy_name": outcome.policy_name,
        "candidate_counts_by_company": dict(outcome.candidate_counts_by_company),
        "candidate_counts_by_company_subquery": dict(
            outcome.candidate_counts_by_company_subquery
        ),
        "selected_counts_by_company": dict(outcome.selected_counts_by_company),
        "target_counts_by_company": dict(outcome.target_counts_by_company),
        "quota_satisfied": outcome.quota_satisfied,
        "context_input_tokens": outcome.context_input_tokens,
        "context_input_limit": outcome.context_input_limit,
    }


def evaluate_scope_aware_hybrid(
    test_cases: list[dict[str, Any]],
    model: SentenceTransformer,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int,
    candidate_k: int,
    anchored_company_k: int,
) -> tuple[pd.DataFrame, float]:
    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in all_chunks}
    missing_gold_ids = {
        chunk_id for case in test_cases for chunk_id in case["expected_chunk_ids"]
        if chunk_id not in chunks_by_id
    }
    if missing_gold_ids:
        raise ValueError("Ground-truth chunk IDs missing from current corpus:\n" + "\n".join(sorted(missing_gold_ids)))

    rows = []
    total_start = time.perf_counter()
    for number, case in enumerate(test_cases, start=1):
        start = time.perf_counter()
        retrieved, scope, detected_companies = scope_aware_hybrid_retrieve(
            case["question"], model, query_prefix, normalized_embeddings, bm25_retriever,
            all_chunks, rrf_k, candidate_k, MAX_K, anchored_company_k,
        )
        row = evaluate_query(case, retrieved, chunks_by_id)
        row.update({
            "latency_seconds": time.perf_counter() - start,
            "scope": scope,
            "detected_companies": detected_companies,
            "retrieved_chunk_ids": [result["chunk_id"] for result in retrieved],
            "retrieved_results": retrieved,
        })
        rows.append(row)
        if number % 25 == 0 or number == len(test_cases):
            print(f"Evaluated {number}/{len(test_cases)} queries")
    return pd.DataFrame(rows), time.perf_counter() - total_start


def default_output_path(output_directory: Path, model_name: str) -> Path:
    directory = output_directory / "scope_aware_hybrid_retrieval" / model_name
    versions = [
        int(match.group(1)) for path in directory.glob("v*")
        if path.is_dir() and (match := re.fullmatch(r"v(\d+)", path.name))
    ]
    return directory / f"v{max(versions, default=0) + 1}" / "evaluation.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate scope-aware BGE + BM25 RRF retrieval.")
    parser.add_argument("--embeddings-directory", type=Path, default=DEFAULT_EMBEDDINGS_DIRECTORY)
    parser.add_argument("--chunks-directory", type=Path, default=DEFAULT_CHUNKS_DIRECTORY)
    parser.add_argument("--test-queries", type=Path, default=DEFAULT_TEST_QUERIES_PATH)
    parser.add_argument("--output", type=Path, help="Detailed JSONL results path.")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--model-name", choices=tuple(MODEL_CONFIGS), default=DEFAULT_MODEL_NAME)
    parser.add_argument("--device")
    parser.add_argument("--rrf-k", type=int, default=DEFAULT_RRF_K)
    parser.add_argument("--candidate-k", type=int, default=MAX_K)
    parser.add_argument("--anchored-company-k", type=int, default=DEFAULT_ANCHORED_COMPANY_K)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()
    if arguments.rrf_k < 0 or arguments.candidate_k < MAX_K or arguments.anchored_company_k < 0:
        raise ValueError("rrf-k and anchored-company-k must be non-negative; candidate-k must be at least MAX_K.")

    config = MODEL_CONFIGS[arguments.model_name]
    all_embeddings, all_chunks = load_corpus(arguments.embeddings_directory, arguments.chunks_directory, arguments.model_name)
    normalized_embeddings = all_embeddings / np.clip(np.linalg.norm(all_embeddings, axis=1, keepdims=True), 1e-12, None)
    print(f"Total chunks: {len(all_chunks)}\nEmbedding matrix: {all_embeddings.shape}")
    index_start = time.perf_counter()
    bm25_retriever = build_bm25_index(all_chunks)
    print(f"BM25 indexing: {time.perf_counter() - index_start:.3f}s")
    model = SentenceTransformer(config["repository"], device=arguments.device)
    dataframe, total_time = evaluate_scope_aware_hybrid(
        load_test_queries(arguments.test_queries), model, config["query_prefix"], normalized_embeddings,
        bm25_retriever, all_chunks, arguments.rrf_k, arguments.candidate_k, arguments.anchored_company_k,
    )
    print_evaluation_summary(dataframe, total_time)

    output_path = arguments.output or default_output_path(arguments.output_directory, arguments.model_name)
    metadata = {
        "run_number": None if arguments.output else int(output_path.parent.name[1:]),
        "timestamp": datetime.now().astimezone().isoformat(),
        "embedding_model": config["repository"], "reranker_model": None,
        "retrieval_method": "scope_aware_hybrid_retrieve",
        "retrieval_parameters": {
            "top_k": MAX_K, "dense_candidate_k": arguments.candidate_k,
            "bm25_candidate_k": arguments.candidate_k, "fusion": "reciprocal_rank_fusion",
            "rrf_k": arguments.rrf_k, "anchored_company_k": arguments.anchored_company_k,
            "query_prefix": config["query_prefix"], "company_aliases": COMPANY_ALIASES,
            "comparison_cues": list(COMPARISON_CUES), "enumeration_cues": list(ENUMERATION_CUES),
            "enumeration_candidate_k": max(arguments.candidate_k, DEFAULT_ENUMERATION_CANDIDATE_K),
            "enumeration_min_relative_rrf_score": ENUMERATION_MIN_RELATIVE_RRF_SCORE,
        },
        "evaluation_dataset": str(arguments.test_queries), "device": str(model.device),
    }
    write_results(output_path, dataframe, metadata, arguments.overwrite)
    summary_path = write_summary(output_path, dataframe, total_time, metadata, arguments.overwrite)
    print(f"\nSaved detailed results to: {output_path}\nSaved summary to: {summary_path}")


if __name__ == "__main__":
    main()
