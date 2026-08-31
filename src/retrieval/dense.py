"""Interchangeable local and Qdrant dense-search implementations."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Protocol, Sequence

import numpy as np
from qdrant_client import QdrantClient, models

from src.indexing.qdrant_index import DENSE_VECTOR_NAME


SHADOW_PARITY_TOP_K = 10
SHADOW_MIN_ID_OVERLAP_RATIO = 0.98


class QueryEmbedder(Protocol):
    def encode(self, sentence: str, *, normalize_embeddings: bool) -> np.ndarray: ...


@dataclass(frozen=True)
class DenseResult:
    index: int
    chunk_id: str
    score: float


class DenseRetriever(Protocol):
    identity: str

    def search(
        self,
        query: str,
        candidate_k: int,
        allowed_tickers: set[str] | None = None,
    ) -> list[DenseResult]: ...

    def health_check(self) -> dict[str, Any]: ...


class LocalArtifactRetriever:
    """Exact normalized matrix search over frozen local NPZ artifacts."""

    identity = "local-npz-exact"

    def __init__(
        self,
        *,
        model: QueryEmbedder,
        query_prefix: str,
        normalized_embeddings: np.ndarray,
        all_chunks: Sequence[dict[str, Any]],
    ) -> None:
        self.model = model
        self.query_prefix = query_prefix
        self.normalized_embeddings = normalized_embeddings
        self.all_chunks = all_chunks

    def search(
        self,
        query: str,
        candidate_k: int,
        allowed_tickers: set[str] | None = None,
    ) -> list[DenseResult]:
        query_embedding = self.model.encode(
            self.query_prefix + query, normalize_embeddings=True
        )
        scores = self.normalized_embeddings @ query_embedding
        if allowed_tickers is None:
            pool = np.arange(len(scores))
        else:
            pool = np.asarray(
                [
                    index
                    for index, chunk in enumerate(self.all_chunks)
                    if chunk.get("ticker") in allowed_tickers
                ],
                dtype=int,
            )
        if not len(pool):
            return []
        top_count = min(candidate_k, len(pool))
        ranked = pool[np.argsort(scores[pool])[-top_count:][::-1]]
        return [
            DenseResult(
                index=int(index),
                chunk_id=self.all_chunks[int(index)]["chunk_id"],
                score=float(scores[int(index)]),
            )
            for index in ranked
        ]

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "identity": self.identity,
            "chunk_count": len(self.all_chunks),
        }


class QdrantRetriever:
    """Exact named-vector search with payload filters and local hydration IDs."""

    identity = "qdrant-dense-exact"

    def __init__(
        self,
        *,
        client: QdrantClient,
        collection_name: str,
        model: QueryEmbedder,
        query_prefix: str,
        all_chunks: Sequence[dict[str, Any]],
        vector_name: str = DENSE_VECTOR_NAME,
        exact: bool = True,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.model = model
        self.query_prefix = query_prefix
        self.vector_name = vector_name
        self.exact = exact
        self.all_chunks = all_chunks
        self.index_by_id = {
            chunk["chunk_id"]: index for index, chunk in enumerate(all_chunks)
        }
        self._state = threading.local()
        if len(self.index_by_id) != len(all_chunks):
            raise ValueError("Chunk IDs must be unique for Qdrant hydration.")

    def begin_request(self) -> None:
        self._state.records = []

    def consume_report(self) -> tuple[dict[str, Any], ...]:
        records = tuple(getattr(self._state, "records", []))
        self._state.records = []
        return records

    def _record(self, value: dict[str, Any]) -> None:
        records = getattr(self._state, "records", None)
        if records is None:
            records = []
            self._state.records = records
        records.append(value)

    def _filter(self, allowed_tickers: set[str] | None) -> models.Filter | None:
        if allowed_tickers is None:
            return None
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="ticker",
                    match=models.MatchAny(any=sorted(allowed_tickers)),
                )
            ]
        )

    def search(
        self,
        query: str,
        candidate_k: int,
        allowed_tickers: set[str] | None = None,
    ) -> list[DenseResult]:
        if candidate_k <= 0 or allowed_tickers == set():
            return []
        query_embedding = self.model.encode(
            self.query_prefix + query, normalize_embeddings=True
        )
        started = time.perf_counter()
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=np.asarray(query_embedding, dtype=np.float32).tolist(),
            using=self.vector_name,
            query_filter=self._filter(allowed_tickers),
            search_params=models.SearchParams(exact=self.exact),
            limit=candidate_k,
            with_payload=["chunk_id", "ticker"],
            with_vectors=False,
        )
        latency_ms = round((time.perf_counter() - started) * 1_000, 3)
        results: list[DenseResult] = []
        for point in response.points:
            payload = point.payload or {}
            chunk_id = payload.get("chunk_id")
            if not isinstance(chunk_id, str) or chunk_id not in self.index_by_id:
                raise RuntimeError("Qdrant returned an unknown or missing chunk_id.")
            ticker = payload.get("ticker")
            if allowed_tickers is not None and ticker not in allowed_tickers:
                raise RuntimeError(f"Qdrant ticker filter leaked {ticker!r}.")
            results.append(
                DenseResult(
                    index=self.index_by_id[chunk_id],
                    chunk_id=chunk_id,
                    score=float(point.score),
                )
            )
        self._record(
            {
                "query": query,
                "allowed_tickers": sorted(allowed_tickers) if allowed_tickers else [],
                "candidate_k": candidate_k,
                "qdrant_ids": [item.chunk_id for item in results],
                "qdrant_count": len(results),
                "qdrant_latency_ms": latency_ms,
            }
        )
        return results

    def health_check(self) -> dict[str, Any]:
        info = self.client.get_collection(self.collection_name)
        count = int(
            self.client.count(collection_name=self.collection_name, exact=True).count
        )
        return {
            "status": "ok",
            "identity": self.identity,
            "collection": self.collection_name,
            "point_count": count,
            "collection_status": str(info.status),
            "exact": self.exact,
        }


class ShadowDenseRetriever:
    """Return local results while recording strict Qdrant parity per search."""

    identity = "local-npz-with-qdrant-shadow"

    def __init__(
        self, *, primary: LocalArtifactRetriever, shadow: QdrantRetriever
    ) -> None:
        self.primary = primary
        self.shadow = shadow
        self._state = threading.local()

    def begin_request(self) -> None:
        self._state.records = []
        self.shadow.begin_request()

    def consume_report(self) -> tuple[dict[str, Any], ...]:
        records = tuple(getattr(self._state, "records", []))
        self._state.records = []
        self.shadow.consume_report()
        return records

    def _record(self, value: dict[str, Any]) -> None:
        records = getattr(self._state, "records", None)
        if records is None:
            records = []
            self._state.records = records
        records.append(value)

    def search(
        self,
        query: str,
        candidate_k: int,
        allowed_tickers: set[str] | None = None,
    ) -> list[DenseResult]:
        primary = self.primary.search(query, candidate_k, allowed_tickers)
        started = time.perf_counter()
        shadow = self.shadow.search(query, candidate_k, allowed_tickers)
        latency_ms = round((time.perf_counter() - started) * 1_000, 3)
        primary_ids = [item.chunk_id for item in primary]
        shadow_ids = [item.chunk_id for item in shadow]
        overlap_count = len(set(primary_ids) & set(shadow_ids))
        denominator = max(len(primary_ids), len(shadow_ids), 1)
        overlap_ratio = overlap_count / denominator
        parity_depth = min(SHADOW_PARITY_TOP_K, len(primary_ids), len(shadow_ids))
        top_order_matches = primary_ids[:parity_depth] == shadow_ids[:parity_depth]
        record = {
            "query": query,
            "allowed_tickers": sorted(allowed_tickers) if allowed_tickers else [],
            "candidate_k": candidate_k,
            "local_ids": primary_ids,
            "qdrant_ids": shadow_ids,
            "exact_id_order": primary_ids == shadow_ids,
            "top_order_depth": parity_depth,
            "top_order_matches": top_order_matches,
            "id_overlap_count": overlap_count,
            "id_overlap_ratio": round(overlap_ratio, 6),
            "parity_accepted": (
                top_order_matches and overlap_ratio >= SHADOW_MIN_ID_OVERLAP_RATIO
            ),
            "local_count": len(primary_ids),
            "qdrant_count": len(shadow_ids),
            "qdrant_latency_ms": latency_ms,
        }
        self._record(record)
        return primary

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "identity": self.identity,
            "primary": self.primary.health_check(),
            "shadow": self.shadow.health_check(),
        }
