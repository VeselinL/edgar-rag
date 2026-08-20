"""Shared scope-aware BGE/BM25/RRF retrieval and reranking.

This module extracts the query-only scope policy from the scope-aware evaluator
and the generation/reranking boundaries demonstrated in the notebooks. It has no
FastAPI dependency so evaluation, notebooks, and other Python callers use the
same behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol, Sequence

import bm25s
import numpy as np

from src.embeddings.embed_chunks import table_embedding_text


DEFAULT_RRF_K = 100
DEFAULT_CANDIDATE_K = 50
DEFAULT_FINAL_EVIDENCE_K = 12
DEFAULT_ANCHORED_COMPANY_K = 3
DEFAULT_ENUMERATION_CANDIDATE_K = 30
ENUMERATION_MIN_RELATIVE_RRF_SCORE = 0.60

COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "AUR": ("aurora innovation", "aurora", "aurora driver"),
    "TSLA": ("tesla",),
    "MBLY": ("mobileye", "eyeq", "mobileye drive"),
    "GOOGL": ("alphabet", "google", "waymo"),
    "GM": ("general motors", "gm"),
    "F": ("ford motor company", "ford"),
    "NVDA": ("nvidia",),
    "QCOM": ("qualcomm", "snapdragon digital chassis", "snapdragon"),
    "APTV": ("aptiv",),
    "OUST": ("ouster",),
}

# These are the evaluator's existing global/comparison cues. Keep matching
# query-only and deterministic; the frontend must never reproduce this list.
COMPARISON_CUES = (
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
    candidates: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]

    @property
    def selected_evidence_companies(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(result["chunk"].get("ticker") for result in self.evidence))

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(result["chunk"]["chunk_id"] for result in self.evidence)


def detect_companies(query: str) -> list[str]:
    """Detect supported names, aliases, and explicit tickers in corpus order."""
    normalized_query = query.casefold()
    detected: set[str] = set()
    for ticker, aliases in COMPANY_ALIASES.items():
        if any(
            re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized_query)
            for alias in aliases
        ):
            detected.add(ticker)

    # Avoid treating the common article/letter "F" as a Ford ticker. Ford is
    # still recognized by its company aliases.
    tokens = {token.upper() for token in re.findall(r"\b[A-Za-z]{2,5}\b", query)}
    detected.update(ticker for ticker in COMPANY_ALIASES if ticker != "F" and ticker in tokens)
    return [ticker for ticker in COMPANY_ALIASES if ticker in detected]


def contains_comparison_cue(query: str) -> bool:
    normalized_query = query.casefold()
    return any(cue in normalized_query for cue in COMPARISON_CUES)


def is_enumeration_query(query: str) -> bool:
    return any(re.search(cue, query, flags=re.IGNORECASE) for cue in ENUMERATION_CUES)


def detect_scope(query: str) -> tuple[str, list[str]]:
    """Classify query scope without using retrieval results or an LLM."""
    companies = detect_companies(query)
    if is_enumeration_query(query):
        return "enumeration", companies
    if contains_comparison_cue(query):
        return ("anchored_global" if companies else "global"), companies
    if len(companies) == 1:
        return "single_company", companies
    if len(companies) > 1:
        return "explicit_subset", companies
    return "global", companies


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
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Apply the common query scope around the evaluated hybrid primitive."""
    scope, companies = detect_scope(query)
    if scope == "single_company":
        results = hybrid_retrieve(
            query, model, query_prefix, normalized_embeddings, bm25_retriever,
            all_chunks, rrf_k, candidate_k, allowed_tickers={companies[0]},
        )[:top_k]
        return [dict(item, retrieval_source="scoped", retrieval_scope=scope) for item in results], scope, companies

    if scope == "explicit_subset":
        per_company = {
            ticker: hybrid_retrieve(
                query, model, query_prefix, normalized_embeddings, bm25_retriever,
                all_chunks, rrf_k, candidate_k, allowed_tickers={ticker},
            )
            for ticker in companies
        }
        return _round_robin_company_results(per_company, top_k, scope), scope, companies

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
        reranker: PairReranker | None,
        rrf_k: int = DEFAULT_RRF_K,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        final_evidence_k: int = DEFAULT_FINAL_EVIDENCE_K,
        anchored_company_k: int = DEFAULT_ANCHORED_COMPANY_K,
    ) -> None:
        self.model = model
        self.query_prefix = query_prefix
        self.normalized_embeddings = normalized_embeddings
        self.bm25_retriever = bm25_retriever
        self.all_chunks = all_chunks
        self.reranker = reranker
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self.final_evidence_k = final_evidence_k
        self.anchored_company_k = anchored_company_k

    def retrieve(self, query: str) -> RetrievalOutcome:
        scope, companies = detect_scope(query)
        # Keep a wider pool for reranking while retaining scope-aware allocation.
        candidate_budget = max(self.candidate_k, self.final_evidence_k)
        candidates, retrieved_scope, retrieved_companies = scope_aware_hybrid_retrieve(
            query,
            self.model,
            self.query_prefix,
            self.normalized_embeddings,
            self.bm25_retriever,
            self.all_chunks,
            self.rrf_k,
            self.candidate_k,
            candidate_budget,
            self.anchored_company_k,
        )
        if (scope, companies) != (retrieved_scope, retrieved_companies):
            raise RuntimeError("Scope detection changed during retrieval.")
        candidates = deduplicate_results(candidates)
        reranked = rerank_results(
            query, candidates, self.all_chunks, self.reranker
        )
        targets = resolve_comparison_targets(scope, companies)
        evidence = select_final_evidence(reranked, self.final_evidence_k, targets)
        return RetrievalOutcome(
            query=query,
            scope=scope,
            detected_companies=tuple(companies),
            comparison=bool(targets) or scope == "enumeration",
            retrieval_scopes=tuple(
                dict.fromkeys(item.get("retrieval_scope", scope) for item in candidates)
            ),
            candidates=tuple(candidates),
            evidence=tuple(evidence),
        )
