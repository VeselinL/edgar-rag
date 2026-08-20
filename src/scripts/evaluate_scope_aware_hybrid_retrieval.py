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
SUBQUERY_RETRIEVAL_K = 10
FINAL_CONTEXT_K = 10
MIN_CHUNKS_PER_SUBQUERY = 2
MULTI_SUBQUERY_BONUS = 0.01

COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "AUR": ("aurora innovation", "aurora", "aurora driver"),
    "TSLA": ("tesla",),
    "MBLY": ("mobileye","eyeq", "mobileye drive"),
    "GOOGL": ("alphabet", "google", "waymo"),
    "GM": ("general motors", "gm"),
    "F": ("ford motor company", "ford"),
    "NVDA": ("nvidia",),
    "QCOM": ("qualcomm","snapdragon digital chassis", "snapdragon"),
    "APTV": ("aptiv",),
    "OUST": ("ouster",),
}

GLOBAL_CUES = (
    "other companies",
    "other strategies",
    "the others",
    "competitors",
    "the rest",
    "across the companies",
    "across the industry",
    "which companies",
    "who is most",
    "who is more",
)

ENUMERATION_CUES = (
    r"\bwhich companies\b",
    r"\bwhat companies\b",
    r"\bwhich firms\b",
    r"\bwhat firms\b",
    r"\bwho (?:offers|operates|develops|provides|uses|builds|sells)\b",
)


def detect_companies(query: str) -> list[str]:
    normalized_query = query.casefold()

    detected = set()

    for ticker, aliases in COMPANY_ALIASES.items():
        if any(
            re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized_query)
            for alias in aliases
        ):
            detected.add(ticker)

    # Explicit ticker mentions
    tokens = set(re.findall(r"\b[A-Za-z]{2,5}\b", query))

    for ticker in COMPANY_ALIASES:
        if ticker != "F" and ticker.upper() in {token.upper() for token in tokens}:
            detected.add(ticker)

    return [
        ticker
        for ticker in COMPANY_ALIASES
        if ticker in detected
    ]


def contains_global_cue(query: str) -> bool:
    normalized_query = query.casefold()
    return any(cue in normalized_query for cue in GLOBAL_CUES)


def is_enumeration_query(query: str) -> bool:
    """Identify open company-list questions without an LLM call."""
    return any(re.search(cue, query, flags=re.IGNORECASE) for cue in ENUMERATION_CUES)


def detect_scope(query: str) -> tuple[str, list[str]]:
    """Classify query scope without using retrieval results or an LLM."""
    companies = detect_companies(query)
    if is_enumeration_query(query):
        return "enumeration", companies
    if contains_global_cue(query):
        return ("anchored_global" if companies else "global"), companies
    if len(companies) == 1:
        return "single_company", companies
    if len(companies) > 1:
        return "explicit_subset", companies
    return "global", companies


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
    # The baseline corpus has ten companies, but keep the selector safe if that
    # invariant changes: strongest representatives win when there are too many.
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
    """Apply scope policy around a common hybrid retrieval primitive."""
    scope, companies = resolved_scope or detect_scope(query)
    if scope == "single_company":
        results = hybrid_retrieve(query, model, query_prefix, normalized_embeddings, bm25_retriever, all_chunks, rrf_k, candidate_k, {companies[0]})[:top_k]
        return [dict(result, retrieval_source="scoped", retrieval_scope=scope) for result in results], scope, companies
    if scope == "explicit_subset":
        results = hybrid_retrieve(query, model, query_prefix, normalized_embeddings, bm25_retriever, all_chunks, rrf_k, candidate_k, set(companies))[:top_k]
        return [dict(result, retrieval_source="scoped", retrieval_scope=scope) for result in results], scope, companies
    if scope == "enumeration":
        enumeration_candidate_k = max(candidate_k, DEFAULT_ENUMERATION_CANDIDATE_K)
        global_results = hybrid_retrieve(
            query, model, query_prefix, normalized_embeddings, bm25_retriever,
            all_chunks, rrf_k, enumeration_candidate_k,
        )
        return select_enumeration_results(global_results, top_k), scope, companies
    global_results = hybrid_retrieve(query, model, query_prefix, normalized_embeddings, bm25_retriever, all_chunks, rrf_k, candidate_k)
    if scope == "global":
        return [dict(result, retrieval_source="global", retrieval_scope=scope) for result in global_results[:top_k]], scope, companies
    anchored_results = {
        ticker: hybrid_retrieve(query, model, query_prefix, normalized_embeddings, bm25_retriever, all_chunks, rrf_k, candidate_k, {ticker})
        for ticker in companies
    }
    return merge_anchored_global_results(global_results, anchored_results, top_k, anchored_company_k), scope, companies


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
    """Retrieve per subquery, merge provenance, and select a compact context."""
    if not subqueries:
        raise ValueError("At least one subquery is required.")
    if subquery_retrieval_k <= 0 or final_context_k <= 0 or min_chunks_per_subquery <= 0:
        raise ValueError(
            "subquery_retrieval_k, final_context_k, and min_chunks_per_subquery must be positive."
        )
    if candidate_k < subquery_retrieval_k:
        raise ValueError("candidate_k must be at least subquery_retrieval_k.")
    if min_chunks_per_subquery > subquery_retrieval_k:
        raise ValueError("min_chunks_per_subquery cannot exceed subquery_retrieval_k.")
    if multi_subquery_bonus < 0:
        raise ValueError("multi_subquery_bonus must be non-negative.")

    original_scope = detect_scope(original_query)
    inherited_scope = original_scope if original_scope[0] == "single_company" else None
    merged_by_id: dict[str, dict[str, Any]] = {}
    candidates_by_subquery: list[list[str]] = []
    first_seen = 0

    for subquery_index, subquery in enumerate(subqueries):
        results, scope, companies = scope_aware_hybrid_retrieve(
            subquery,
            model,
            query_prefix,
            normalized_embeddings,
            bm25_retriever,
            all_chunks,
            rrf_k,
            candidate_k,
            subquery_retrieval_k,
            anchored_company_k,
            resolved_scope=inherited_scope,
        )
        results = results[:subquery_retrieval_k]
        if inherited_scope is not None:
            inherited_ticker = inherited_scope[1][0]
            unexpected_tickers = {
                result.get("ticker") for result in results
                if result.get("ticker") != inherited_ticker
            }
            if unexpected_tickers:
                raise RuntimeError(
                    f"Single-company scope leaked candidates outside {inherited_ticker}: "
                    f"{sorted(unexpected_tickers, key=str)}"
                )
        subquery_chunk_ids: list[str] = []
        for subquery_rank, result in enumerate(results, start=1):
            chunk_id = result["chunk_id"]
            subquery_chunk_ids.append(chunk_id)
            match = {
                "subquery": subquery,
                "subquery_index": subquery_index,
                "subquery_rank": subquery_rank,
                "rrf_score": result["rrf_score"],
                "dense_rank": result.get("dense_rank"),
                "bm25_rank": result.get("bm25_rank"),
                "retrieval_scope": scope,
                "detected_companies": companies,
            }
            if chunk_id not in merged_by_id:
                merged_by_id[chunk_id] = {
                    **result,
                    "first_seen_order": first_seen,
                    "subquery_matches": [],
                    "subqueries": [],
                }
                first_seen += 1
            candidate = merged_by_id[chunk_id]
            candidate["subquery_matches"].append(match)
            if subquery not in candidate["subqueries"]:
                candidate["subqueries"].append(subquery)
        candidates_by_subquery.append(subquery_chunk_ids)

    for candidate in merged_by_id.values():
        candidate["best_rrf_score"] = max(
            match["rrf_score"] for match in candidate["subquery_matches"]
        )
        candidate["subquery_count"] = len({
            match["subquery_index"] for match in candidate["subquery_matches"]
        })
        candidate["selection_score"] = (
            candidate["best_rrf_score"]
            + multi_subquery_bonus * (candidate["subquery_count"] - 1)
        )
        candidate["selected"] = False
        candidate["selection_reason"] = None

    selected_ids: list[str] = []
    selected_id_set: set[str] = set()

    # Stage 1: select up to the requested distinct evidence per subquery in
    # rounds, so later subqueries are not starved by earlier ones.
    for _ in range(min_chunks_per_subquery):
        for subquery_chunk_ids in candidates_by_subquery:
            if len(selected_ids) >= final_context_k:
                break
            covered_count = sum(
                chunk_id in selected_id_set for chunk_id in subquery_chunk_ids
            )
            if covered_count >= min_chunks_per_subquery:
                continue
            for chunk_id in subquery_chunk_ids:
                if chunk_id not in selected_id_set:
                    selected_ids.append(chunk_id)
                    selected_id_set.add(chunk_id)
                    merged_by_id[chunk_id]["selection_reason"] = "coverage"
                    break

    # Stage 2: strongest remaining evidence, with a small cross-subquery bonus.
    remaining = sorted(
        (
            candidate for chunk_id, candidate in merged_by_id.items()
            if chunk_id not in selected_id_set
        ),
        key=lambda candidate: (
            -candidate["selection_score"],
            min(match["subquery_rank"] for match in candidate["subquery_matches"]),
            candidate["first_seen_order"],
            candidate["chunk_id"],
        ),
    )
    for candidate in remaining:
        if len(selected_ids) >= final_context_k:
            break
        selected_ids.append(candidate["chunk_id"])
        selected_id_set.add(candidate["chunk_id"])
        candidate["selection_reason"] = "global_score"

    coverage_by_subquery = [
        sum(chunk_id in selected_id_set for chunk_id in subquery_chunk_ids)
        for subquery_chunk_ids in candidates_by_subquery
    ]

    selected = []
    for final_rank, chunk_id in enumerate(selected_ids, start=1):
        candidate = merged_by_id[chunk_id]
        candidate["selected"] = True
        candidate["final_context_rank"] = final_rank
        selected.append(candidate)

    diagnostics = sorted(
        merged_by_id.values(), key=lambda candidate: candidate["first_seen_order"]
    )
    return {
        "original_scope": original_scope[0],
        "original_companies": original_scope[1],
        "inherited_scope": inherited_scope,
        "subqueries": subqueries,
        "subquery_retrieval_k": subquery_retrieval_k,
        "final_context_k": final_context_k,
        "min_chunks_per_subquery": min_chunks_per_subquery,
        "multi_subquery_bonus": multi_subquery_bonus,
        "coverage_by_subquery": coverage_by_subquery,
        "candidates": diagnostics,
        "selected": selected,
        "selected_chunk_ids": selected_ids,
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
            "global_cues": list(GLOBAL_CUES), "enumeration_cues": list(ENUMERATION_CUES),
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
