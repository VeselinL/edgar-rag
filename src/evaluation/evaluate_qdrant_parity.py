"""Evaluate Qdrant dense and final hybrid-selection parity against local artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

from src.backend.pipeline import PROJECT_ROOT, build_bm25_index, load_corpus
from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.indexing.qdrant_index import (
    DEFAULT_ALIAS,
    DEFAULT_QDRANT_URL,
    alias_target,
    make_client,
)
from src.resolution.companies import default_company_resolver
from src.retrieval.dense import (
    SHADOW_MIN_ID_OVERLAP_RATIO,
    SHADOW_PARITY_TOP_K,
    LocalArtifactRetriever,
    QdrantRetriever,
)
from src.retrieval.scope_aware import ScopeAwareRetriever


DENSE_CASES = (
    ("Tesla revenue", {"TSLA"}),
    ("Ford risk factors", {"F"}),
    ("Mobileye EyeQ products", {"MBLY"}),
    ("NVIDIA automotive revenue", {"NVDA"}),
    ("Qualcomm automotive segment", {"QCOM"}),
    ("Ouster lidar revenue", {"OUST"}),
    ("Alphabet Waymo business", {"GOOGL"}),
    ("GM Cruise restructuring", {"GM"}),
    ("Aptiv advanced safety", {"APTV"}),
    ("Rivian vehicles", {"RIVN"}),
    ("common autonomous vehicle risks", None),
)

FINAL_CASES = (
    {
        "query": "What are Tesla's principal risk factors?",
        "subqueries": ["Tesla principal risk factors"],
        "targets": [["TSLA"]],
    },
    {
        "query": "Compare Tesla and Ford revenue.",
        "subqueries": ["Tesla revenue", "Ford revenue"],
        "targets": [["TSLA"], ["F"]],
    },
    {
        "query": "What autonomous-driving risks are common across these filings?",
        "subqueries": ["common autonomous-driving risks"],
        "targets": [[]],
    },
)


def _dense_case(
    local: LocalArtifactRetriever,
    qdrant: QdrantRetriever,
    query: str,
    tickers: set[str] | None,
    candidate_k: int,
) -> dict[str, Any]:
    local_ids = [item.chunk_id for item in local.search(query, candidate_k, tickers)]
    qdrant_ids = [item.chunk_id for item in qdrant.search(query, candidate_k, tickers)]
    overlap = len(set(local_ids) & set(qdrant_ids))
    denominator = max(len(local_ids), len(qdrant_ids), 1)
    overlap_ratio = overlap / denominator
    parity_depth = min(SHADOW_PARITY_TOP_K, len(local_ids), len(qdrant_ids))
    top_order_matches = local_ids[:parity_depth] == qdrant_ids[:parity_depth]
    return {
        "query": query,
        "tickers": sorted(tickers) if tickers else [],
        "candidate_k": candidate_k,
        "local_ids": local_ids,
        "qdrant_ids": qdrant_ids,
        "exact_id_order": local_ids == qdrant_ids,
        "top_order_depth": parity_depth,
        "top_order_matches": top_order_matches,
        "id_overlap_count": overlap,
        "id_overlap_ratio": round(overlap_ratio, 6),
        "accepted": (
            top_order_matches and overlap_ratio >= SHADOW_MIN_ID_OVERLAP_RATIO
        ),
    }


def evaluate(
    *,
    url: str = DEFAULT_QDRANT_URL,
    api_key: str | None = None,
    alias: str = DEFAULT_ALIAS,
    candidate_k: int = 50,
    device: str = "cpu",
) -> dict[str, Any]:
    embeddings, chunks = load_corpus()
    normalized = embeddings / np.clip(
        np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-12, None
    )
    config = MODEL_CONFIGS["bgebase"]
    model = SentenceTransformer(config["repository"], device=device)
    client = make_client(url=url, api_key=api_key)
    target = alias_target(client, alias)
    if target is None:
        raise RuntimeError(f"Qdrant alias {alias!r} does not exist.")
    local_dense = LocalArtifactRetriever(
        model=model,
        query_prefix=config["query_prefix"],
        normalized_embeddings=normalized,
        all_chunks=chunks,
    )
    qdrant_dense = QdrantRetriever(
        client=client,
        collection_name=alias,
        model=model,
        query_prefix=config["query_prefix"],
        all_chunks=chunks,
    )
    dense_results = [
        _dense_case(local_dense, qdrant_dense, query, tickers, candidate_k)
        for query, tickers in DENSE_CASES
    ]

    bm25 = build_bm25_index(chunks)
    common = {
        "model": model,
        "query_prefix": config["query_prefix"],
        "normalized_embeddings": normalized,
        "bm25_retriever": bm25,
        "all_chunks": chunks,
    }
    local_hybrid = ScopeAwareRetriever(**common, dense_retriever=local_dense)
    qdrant_hybrid = ScopeAwareRetriever(**common, dense_retriever=qdrant_dense)
    final_results = []
    for case in FINAL_CASES:
        resolution = default_company_resolver.resolve(case["query"])
        local_outcome = local_hybrid.retrieve(
            case["query"], case["subqueries"], resolution, case["targets"]
        )
        qdrant_outcome = qdrant_hybrid.retrieve(
            case["query"], case["subqueries"], resolution, case["targets"]
        )
        final_results.append(
            {
                **case,
                "scope": local_outcome.scope,
                "local_ids": list(local_outcome.chunk_ids),
                "qdrant_ids": list(qdrant_outcome.chunk_ids),
                "exact_id_order": local_outcome.chunk_ids == qdrant_outcome.chunk_ids,
                "accepted": local_outcome.chunk_ids == qdrant_outcome.chunk_ids,
            }
        )
    accepted = all(item["accepted"] for item in dense_results + final_results)
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "qdrant_url": url,
        "read_alias": alias,
        "physical_collection": target,
        "candidate_k": candidate_k,
        "dense_acceptance": {
            "top_order_depth": SHADOW_PARITY_TOP_K,
            "minimum_id_overlap_ratio": SHADOW_MIN_ID_OVERLAP_RATIO,
        },
        "dense_cases": dense_results,
        "final_selection_cases": final_results,
        "accepted": accepted,
    }


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_QDRANT_URL)
    parser.add_argument("--api-key")
    parser.add_argument("--alias", default=DEFAULT_ALIAS)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "data" / "evaluation" / "qdrant_parity_v1.json"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = parse_arguments(argv)
    report = evaluate(
        url=arguments.url,
        api_key=arguments.api_key,
        alias=arguments.alias,
        candidate_k=arguments.candidate_k,
        device=arguments.device,
    )
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "output": str(output)}, indent=2))
    if not report["accepted"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
