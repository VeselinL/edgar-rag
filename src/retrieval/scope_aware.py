"""Shared scope-aware BGE/BM25/RRF retrieval and context selection.

This module extracts the query-only scope policy from the scope-aware evaluator
and the planned multi-subquery context selection demonstrated in
``notebooks/hybrid_rag_generation.ipynb``. It has no FastAPI dependency so
evaluation, notebooks, and other Python callers use the same behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol, Sequence

import bm25s
import numpy as np

from src.embeddings.embed_chunks import table_embedding_text
from src.filings.corpus import COMPANY_ALIASES
from src.resolution.companies import (
    COMPARISON_CUES,
    ENUMERATION_CUES,
    CompanyResolution,
    default_company_resolver,
    normalize_company_text,
)


DEFAULT_RRF_K = 100
DEFAULT_CANDIDATE_K = 50
DEFAULT_SUBQUERY_RETRIEVAL_K = 10
DEFAULT_FINAL_EVIDENCE_K = 10
DEFAULT_MIN_CHUNKS_PER_SUBQUERY = 2
DEFAULT_MULTI_SUBQUERY_BONUS = 0.01
DEFAULT_ANCHORED_COMPANY_K = 3
DEFAULT_ENUMERATION_CANDIDATE_K = 30
ENUMERATION_MIN_RELATIVE_RRF_SCORE = 0.60

class QueryEmbedder(Protocol):
    def encode(self, sentence: str, *, normalize_embeddings: bool) -> np.ndarray: ...


class PairReranker(Protocol):
    def predict(
        self,
        sentences: Sequence[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
    ) -> Sequence[float]: ...


@dataclass(frozen=True)
class RetrievalOutcome:
    """Final evidence and inspectable scope decisions for one query."""

    query: str
    scope: str
    detected_companies: tuple[str, ...]
    comparison: bool
    retrieval_scopes: tuple[str, ...]
    subqueries: tuple[str, ...]
    coverage_by_subquery: tuple[int, ...]
    candidates: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]

    @property
    def selected_evidence_companies(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(result["chunk"].get("ticker") for result in self.evidence))

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(result["chunk"]["chunk_id"] for result in self.evidence)


def detect_companies(query: str) -> list[str]:
    """Compatibility wrapper around the shared deterministic resolver."""
    return list(default_company_resolver.resolve(query).resolved_tickers)


def contains_comparison_cue(query: str) -> bool:
    normalized_query = f" {normalize_company_text(query)} "
    return any(cue in normalized_query for cue in COMPARISON_CUES)


def is_enumeration_query(query: str) -> bool:
    return any(re.search(cue, query, flags=re.IGNORECASE) for cue in ENUMERATION_CUES)


def detect_scope(
    query: str, resolution: CompanyResolution | None = None
) -> tuple[str, list[str]]:
    """Return scope from the one shared company-resolution result."""
    result = resolution or default_company_resolver.resolve(query)
    return result.scope, list(result.resolved_tickers)


def resolve_comparison_targets(scope: str, companies: Sequence[str]) -> tuple[str, ...]:
    """Return targets whose evidence must survive final selection."""
    if scope in {"explicit_subset", "anchored_global"}:
        return tuple(companies)
    return ()


def dense_candidate_indices(
    query: str,
    model: QueryEmbedder,
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
    # Preserve the evaluated global-index behaviour: rank globally, then filter
    # by immutable ticker metadata for a scoped request.
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
    model: QueryEmbedder,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int,
    candidate_k: int,
    allowed_tickers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Run normalized dense + BM25 + RRF, optionally ticker-filtered."""
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


def deduplicate_results(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        chunk_id = result["chunk_id"]
        if chunk_id not in seen:
            unique.append(result)
            seen.add(chunk_id)
    return unique


def _round_robin_company_results(
    scoped_results: dict[str, list[dict[str, Any]]],
    top_k: int,
    scope: str,
) -> list[dict[str, Any]]:
    """Allocate the fixed budget across targets before relevance-based fill."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    tickers = list(scoped_results)
    position = 0
    while len(selected) < top_k and tickers:
        progressed = False
        for ticker in tickers:
            candidates = scoped_results[ticker]
            if position >= len(candidates):
                continue
            result = candidates[position]
            if result["chunk_id"] not in seen:
                selected.append(
                    dict(
                        result,
                        retrieval_source="comparison_company",
                        retrieval_scope=scope,
                        comparison_target=ticker,
                        comparison_company_rank=position + 1,
                    )
                )
                seen.add(result["chunk_id"])
                progressed = True
                if len(selected) == top_k:
                    break
        if not progressed:
            break
        position += 1
    return selected


def merge_anchored_global_results(
    global_results: list[dict[str, Any]],
    anchored_results: dict[str, list[dict[str, Any]]],
    top_k: int,
    anchored_company_k: int,
) -> list[dict[str, Any]]:
    selected = [
        dict(result, retrieval_source="global", retrieval_scope="anchored_global")
        for result in global_results[:top_k]
    ]
    selected_ids = {result["chunk_id"] for result in selected}
    for ticker in anchored_results:
        for scoped_rank, result in enumerate(anchored_results[ticker][:anchored_company_k], start=1):
            if result["chunk_id"] in selected_ids:
                for existing in selected:
                    if existing["chunk_id"] == result["chunk_id"]:
                        existing.update(
                            retrieval_source="global_and_anchored_company",
                            anchored_company_ticker=ticker,
                            anchored_company_rank=scoped_rank,
                            anchored_company_rrf_score=result["rrf_score"],
                        )
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
    global_results: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    if not global_results or top_k <= 0:
        return []
    minimum_score = global_results[0]["rrf_score"] * ENUMERATION_MIN_RELATIVE_RRF_SCORE
    best_by_ticker: dict[str, tuple[int, dict[str, Any]]] = {}
    for global_rank, result in enumerate(global_results, start=1):
        ticker = result.get("ticker")
        if ticker and ticker not in best_by_ticker:
            best_by_ticker[ticker] = (global_rank, result)
    representatives = [
        (global_rank, result)
        for global_rank, result in best_by_ticker.values()
        if result["rrf_score"] >= minimum_score
        and result["dense_rank"] is not None
        and result["bm25_rank"] is not None
    ]
    representative_ids = {
        result["chunk_id"]
        for _, result in sorted(
            representatives, key=lambda item: (-item[1]["rrf_score"], item[0])
        )[:top_k]
    }
    selected = []
    for global_rank, result in enumerate(global_results, start=1):
        if result["chunk_id"] in representative_ids:
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
        if result["chunk_id"] in {item["chunk_id"] for item in selected}:
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
    return sorted(selected, key=lambda result: result["enumeration_global_rank"])


def scope_aware_hybrid_retrieve(
    query: str,
    model: QueryEmbedder,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int = DEFAULT_RRF_K,
    candidate_k: int = DEFAULT_CANDIDATE_K,
    top_k: int = DEFAULT_FINAL_EVIDENCE_K,
    anchored_company_k: int = DEFAULT_ANCHORED_COMPANY_K,
    resolved_scope: tuple[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Apply the common query scope around the evaluated hybrid primitive."""
    scope, companies = resolved_scope or detect_scope(query)
    if scope == "single_company":
        results = hybrid_retrieve(
            query, model, query_prefix, normalized_embeddings, bm25_retriever,
            all_chunks, rrf_k, candidate_k, allowed_tickers={companies[0]},
        )[:top_k]
        return [dict(item, retrieval_source="scoped", retrieval_scope=scope) for item in results], scope, companies

    if scope == "explicit_subset":
        results = hybrid_retrieve(
            query,
            model,
            query_prefix,
            normalized_embeddings,
            bm25_retriever,
            all_chunks,
            rrf_k,
            candidate_k,
            allowed_tickers=set(companies),
        )[:top_k]
        return [
            dict(item, retrieval_source="scoped", retrieval_scope=scope)
            for item in results
        ], scope, companies

    if scope == "enumeration":
        global_results = hybrid_retrieve(
            query, model, query_prefix, normalized_embeddings, bm25_retriever,
            all_chunks, rrf_k, max(candidate_k, DEFAULT_ENUMERATION_CANDIDATE_K),
        )
        return select_enumeration_results(global_results, top_k), scope, companies

    global_results = hybrid_retrieve(
        query, model, query_prefix, normalized_embeddings, bm25_retriever,
        all_chunks, rrf_k, candidate_k,
    )
    if scope == "global":
        return [
            dict(item, retrieval_source="global", retrieval_scope=scope)
            for item in global_results[:top_k]
        ], scope, companies

    anchored_results = {
        ticker: hybrid_retrieve(
            query, model, query_prefix, normalized_embeddings, bm25_retriever,
            all_chunks, rrf_k, candidate_k, allowed_tickers={ticker},
        )
        for ticker in companies
    }
    return (
        merge_anchored_global_results(
            global_results, anchored_results, top_k, anchored_company_k
        ),
        scope,
        companies,
    )


def retrieve_generation_context(
    original_query: str,
    subqueries: list[str],
    model: QueryEmbedder,
    query_prefix: str,
    normalized_embeddings: np.ndarray,
    bm25_retriever: bm25s.BM25,
    all_chunks: list[dict[str, Any]],
    rrf_k: int = DEFAULT_RRF_K,
    candidate_k: int = DEFAULT_CANDIDATE_K,
    anchored_company_k: int = DEFAULT_ANCHORED_COMPANY_K,
    subquery_retrieval_k: int = DEFAULT_SUBQUERY_RETRIEVAL_K,
    final_context_k: int = DEFAULT_FINAL_EVIDENCE_K,
    min_chunks_per_subquery: int = DEFAULT_MIN_CHUNKS_PER_SUBQUERY,
    multi_subquery_bonus: float = DEFAULT_MULTI_SUBQUERY_BONUS,
    company_resolution: CompanyResolution | None = None,
) -> dict[str, Any]:
    """Retrieve per subquery, merge provenance, and select a compact context.

    This is the production copy of the current main-branch notebook algorithm.
    It retrieves ten candidates per subquery by default, reserves two distinct
    evidence slots per subquery in rounds, then fills the ten-chunk context by
    RRF relevance with the notebook's small cross-subquery bonus.
    """
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
    if len(subqueries) * min_chunks_per_subquery > final_context_k:
        raise ValueError(
            "The final context budget cannot preserve the minimum evidence for every subquery."
        )
    if multi_subquery_bonus < 0:
        raise ValueError("multi_subquery_bonus must be non-negative.")

    original_scope = detect_scope(original_query, company_resolution)
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
                result.get("ticker")
                for result in results
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
        candidate["subquery_count"] = len(
            {match["subquery_index"] for match in candidate["subquery_matches"]}
        )
        candidate["selection_score"] = (
            candidate["best_rrf_score"]
            + multi_subquery_bonus * (candidate["subquery_count"] - 1)
        )
        candidate["selected"] = False
        candidate["selection_reason"] = None

    selected_ids: list[str] = []
    selected_id_set: set[str] = set()

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

    remaining = sorted(
        (
            candidate
            for chunk_id, candidate in merged_by_id.items()
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


def get_rerank_text(chunk: dict[str, Any]) -> str:
    """Build the current query/chunk cross-encoder input."""
    prefix = (
        f"Company: {chunk.get('company', '')}\n"
        f"Ticker: {chunk.get('ticker', '')}\n"
        f"Section: {chunk.get('section', '')}\n"
        f"Content type: {chunk.get('content_type', '')}\n"
    )
    if chunk.get("content_type") == "table":
        return prefix + "\n" + table_embedding_text(chunk) + "\n\nFull table:\n" + chunk.get("text", "")
    return prefix + "\n" + chunk.get("text", "")


def rerank_results(
    query: str,
    candidates: Sequence[dict[str, Any]],
    all_chunks: Sequence[dict[str, Any]],
    reranker: PairReranker | None,
    batch_size: int = 32,
) -> list[dict[str, Any]]:
    """Attach chunks and apply the existing cross-encoder pair scoring boundary."""
    hydrated = [dict(candidate, chunk=all_chunks[candidate["index"]]) for candidate in candidates]
    if not reranker or not hydrated:
        return [dict(item, rerank=rank, reranker_score=None) for rank, item in enumerate(hydrated, 1)]
    pairs = [(query, get_rerank_text(result["chunk"])) for result in hydrated]
    scores = reranker.predict(pairs, batch_size=batch_size, show_progress_bar=False)
    for result, score in zip(hydrated, scores):
        result["reranker_score"] = float(score)
    hydrated.sort(key=lambda item: item["reranker_score"], reverse=True)
    for rank, result in enumerate(hydrated, start=1):
        result["rerank"] = rank
    return hydrated


def select_final_evidence(
    reranked: Sequence[dict[str, Any]],
    budget: int,
    comparison_targets: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Select unique evidence while reserving one available slot per target."""
    if budget <= 0:
        return []
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for ticker in comparison_targets:
        representative = next(
            (item for item in reranked if item["chunk"].get("ticker") == ticker), None
        )
        if representative and representative["chunk"]["chunk_id"] not in selected_ids:
            selected.append(representative)
            selected_ids.add(representative["chunk"]["chunk_id"])
            if len(selected) == budget:
                return selected
    for item in reranked:
        chunk_id = item["chunk"]["chunk_id"]
        if chunk_id in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(chunk_id)
        if len(selected) == budget:
            break
    # Present evidence in reranker order after enforcing coverage.
    rerank_order = {item["chunk"]["chunk_id"]: position for position, item in enumerate(reranked)}
    return sorted(selected, key=lambda item: rerank_order[item["chunk"]["chunk_id"]])


class ScopeAwareRetriever:
    """Production/evaluation entry point over already-loaded corpus artifacts."""

    def __init__(
        self,
        *,
        model: QueryEmbedder,
        query_prefix: str,
        normalized_embeddings: np.ndarray,
        bm25_retriever: bm25s.BM25,
        all_chunks: list[dict[str, Any]],
        rrf_k: int = DEFAULT_RRF_K,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        final_evidence_k: int = DEFAULT_FINAL_EVIDENCE_K,
        anchored_company_k: int = DEFAULT_ANCHORED_COMPANY_K,
        subquery_retrieval_k: int = DEFAULT_SUBQUERY_RETRIEVAL_K,
        min_chunks_per_subquery: int = DEFAULT_MIN_CHUNKS_PER_SUBQUERY,
        multi_subquery_bonus: float = DEFAULT_MULTI_SUBQUERY_BONUS,
    ) -> None:
        self.model = model
        self.query_prefix = query_prefix
        self.normalized_embeddings = normalized_embeddings
        self.bm25_retriever = bm25_retriever
        self.all_chunks = all_chunks
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self.final_evidence_k = final_evidence_k
        self.anchored_company_k = anchored_company_k
        self.subquery_retrieval_k = subquery_retrieval_k
        self.min_chunks_per_subquery = min_chunks_per_subquery
        self.multi_subquery_bonus = multi_subquery_bonus

    def retrieve(
        self,
        query: str,
        subqueries: Sequence[str] | None = None,
        company_resolution: CompanyResolution | None = None,
    ) -> RetrievalOutcome:
        planned_subqueries = list(subqueries or [query])
        diagnostics = retrieve_generation_context(
            original_query=query,
            subqueries=planned_subqueries,
            model=self.model,
            query_prefix=self.query_prefix,
            normalized_embeddings=self.normalized_embeddings,
            bm25_retriever=self.bm25_retriever,
            all_chunks=self.all_chunks,
            rrf_k=self.rrf_k,
            candidate_k=self.candidate_k,
            anchored_company_k=self.anchored_company_k,
            subquery_retrieval_k=self.subquery_retrieval_k,
            final_context_k=self.final_evidence_k,
            min_chunks_per_subquery=self.min_chunks_per_subquery,
            multi_subquery_bonus=self.multi_subquery_bonus,
            company_resolution=company_resolution,
        )
        candidates = diagnostics["candidates"]
        for candidate in candidates:
            candidate["chunk"] = self.all_chunks[candidate["index"]]
        evidence = diagnostics["selected"]
        scope = diagnostics["original_scope"]
        companies = diagnostics["original_companies"]
        targets = resolve_comparison_targets(scope, companies)
        return RetrievalOutcome(
            query=query,
            scope=scope,
            detected_companies=tuple(companies),
            comparison=bool(targets) or scope == "enumeration",
            retrieval_scopes=tuple(
                dict.fromkeys(
                    match["retrieval_scope"]
                    for item in candidates
                    for match in item["subquery_matches"]
                )
            ),
            subqueries=tuple(planned_subqueries),
            coverage_by_subquery=tuple(diagnostics["coverage_by_subquery"]),
            candidates=tuple(candidates),
            evidence=tuple(evidence),
        )
