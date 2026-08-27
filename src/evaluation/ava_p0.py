"""Freeze and compare AVA's pre-P0 local retrieval/citation baseline.

The evaluator deliberately calls the same :class:`ScopeAwareRetriever` and
citation resolver used by FastAPI.  Fixed subqueries keep the fixture independent
of a provider response, so ranking changes can be attributed to retrieval and
selection rather than planner drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.filings.corpus import ACTIVE_FILINGS
from src.generation.rag import citation_ids, format_context, resolve_cited_evidence
from src.resolution.companies import confidence_band, default_company_resolver
from src.retrieval.scope_aware import ScopeAwareRetriever, hybrid_retrieve


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABEL_DIRECTORY = PROJECT_ROOT / "data" / "evaluation" / "ava_p0" / "v1"
DEFAULT_OUTPUT_DIRECTORY = DEFAULT_LABEL_DIRECTORY / "baseline"
BASELINE_SCHEMA_VERSION = 1
BASELINE_POLICY = "scope-aware-npz-bm25-rrf-v1"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def load_cases(label_directory: Path, name: str) -> list[dict[str, Any]]:
    payload = _read_json(label_directory / name)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError(f"Unsupported or missing case schema in {name}.")
    identifiers = [case.get("id") for case in payload["cases"]]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
        raise ValueError(f"Every case in {name} needs a non-empty string id.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"Duplicate case ids in {name}.")
    return payload["cases"]


def percentile(values: Sequence[float], probability: float) -> float:
    """Return a dependency-free linearly interpolated percentile."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def evaluate_resolution(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    records = []
    latencies = []
    for case in cases:
        started = time.perf_counter()
        resolution = default_company_resolver.resolve(case["query"])
        latency_ms = (time.perf_counter() - started) * 1_000
        latencies.append(latency_ms)
        expected = case["expected_tickers"]
        detected = list(resolution.resolved_tickers)
        detected_correctly = detected == expected
        scope_correctly = resolution.scope == case["expected_scope"]
        detected_needs_clarification = resolution.needs_clarification
        clarification_correctly = (
            detected_needs_clarification == case["expected_needs_clarification"]
        )
        records.append(
            {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "expected_tickers": expected,
                "detected_tickers": detected,
                "expected_scope": case["expected_scope"],
                "detected_scope": resolution.scope,
                "expected_needs_clarification": case["expected_needs_clarification"],
                "detected_needs_clarification": detected_needs_clarification,
                "detection_pass": detected_correctly,
                "scope_pass": scope_correctly,
                "clarification_pass": clarification_correctly,
                "methods": list(resolution.methods),
                "confidence_bands": [
                    confidence_band(mention.confidence)
                    for mention in resolution.mentions
                ],
                "mentions": [mention.__dict__ for mention in resolution.mentions],
                "unresolved_mentions": [
                    mention.__dict__ for mention in resolution.unresolved_mentions
                ],
                "first_failure_stage": (
                    None
                    if detected_correctly and scope_correctly and clarification_correctly
                    else "detection"
                ),
                "latency_ms": latency_ms,
            }
        )
    passed = sum(
        record["detection_pass"]
        and record["scope_pass"]
        and record["clarification_pass"]
        for record in records
    )
    exact = [record for record in records if record["category"].startswith("exact")]
    typo = [record for record in records if record["category"].startswith("typo")]
    return {
        "summary": {
            "case_count": len(records),
            "pass_count": passed,
            "accuracy": passed / len(records) if records else 0.0,
            "exact_accuracy": (
                sum(
                    r["detection_pass"] and r["scope_pass"] and r["clarification_pass"]
                    for r in exact
                ) / len(exact)
                if exact else 0.0
            ),
            "typo_accuracy": (
                sum(
                    r["detection_pass"] and r["scope_pass"] and r["clarification_pass"]
                    for r in typo
                ) / len(typo)
                if typo else 0.0
            ),
            "latency_ms": _latency_summary(latencies),
        },
        "records": records,
    }


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values) if values else 0.0,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values, default=0.0),
    }


def _gold_ids(case: dict[str, Any]) -> list[str]:
    return [
        chunk_id
        for ticker in case["expected_tickers"]
        for chunk_id in case["gold_by_ticker"].get(ticker, [])
    ]


def _company_counts(ids: Iterable[str], chunks_by_id: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = Counter(chunks_by_id[chunk_id]["ticker"] for chunk_id in ids)
    return dict(sorted(counts.items()))


def evaluate_retrieval(
    cases: Sequence[dict[str, Any]],
    retriever: ScopeAwareRetriever,
) -> dict[str, Any]:
    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in retriever.all_chunks}
    missing = sorted(
        {
            chunk_id
            for case in cases
            for chunk_id in _gold_ids(case)
            if chunk_id not in chunks_by_id
        }
    )
    if missing:
        raise ValueError("Gold chunk ids are absent from the corpus: " + ", ".join(missing))

    records = []
    latencies = []
    for case in cases:
        started = time.perf_counter()
        outcome = retriever.retrieve(case["query"], case["subqueries"])
        latency_ms = (time.perf_counter() - started) * 1_000
        latencies.append(latency_ms)
        candidate_ids = [candidate["chunk_id"] for candidate in outcome.candidates]
        selected_ids = list(outcome.chunk_ids)
        formatted_context = format_context(outcome.evidence)
        context_token_proxy = _context_token_proxy(formatted_context, retriever.model)
        gold_ids = _gold_ids(case)
        candidate_gold = [chunk_id for chunk_id in gold_ids if chunk_id in candidate_ids]
        selected_gold = [chunk_id for chunk_id in gold_ids if chunk_id in selected_ids]
        detection_pass = list(outcome.detected_companies) == case["expected_tickers"]
        if not detection_pass:
            failure = "detection"
        elif len(candidate_gold) < len(gold_ids):
            failure = "candidate_retrieval"
        elif len(selected_gold) < len(gold_ids):
            failure = "final_selection"
        else:
            failure = None

        rank_probe = _evaluate_rank_probe(case, retriever)
        records.append(
            {
                "id": case["id"],
                "query": case["query"],
                "subqueries": list(outcome.subqueries),
                "expected_tickers": case["expected_tickers"],
                "detected_tickers": list(outcome.detected_companies),
                "scope": outcome.scope,
                "comparison": outcome.comparison,
                "retrieval_scopes": list(outcome.retrieval_scopes),
                "gold_ids": gold_ids,
                "candidate_ids": candidate_ids,
                "candidate_ids_by_company": _ids_by_company(candidate_ids, chunks_by_id),
                "selected_ids": selected_ids,
                "selected_company_counts": _company_counts(selected_ids, chunks_by_id),
                "final_evidence_budget_chunks": retriever.final_evidence_k,
                "final_context_character_count": len(formatted_context),
                "final_context_bge_token_proxy": context_token_proxy,
                "coverage_by_subquery": list(outcome.coverage_by_subquery),
                "candidate_gold_recall": len(candidate_gold) / len(gold_ids),
                "final_gold_recall": len(selected_gold) / len(gold_ids),
                "candidate_gold_ids": candidate_gold,
                "selected_gold_ids": selected_gold,
                "rank_probe": rank_probe,
                "first_failure_stage": failure,
                "latency_ms": latency_ms,
            }
        )
    return {
        "summary": {
            "case_count": len(records),
            "mean_candidate_gold_recall": statistics.fmean(
                record["candidate_gold_recall"] for record in records
            ),
            "mean_final_gold_recall": statistics.fmean(
                record["final_gold_recall"] for record in records
            ),
            "failure_stage_counts": dict(
                sorted(Counter(record["first_failure_stage"] or "pass" for record in records).items())
            ),
            "final_evidence_budget_chunks": retriever.final_evidence_k,
            "context_bge_token_proxy": {
                "mean": statistics.fmean(
                    record["final_context_bge_token_proxy"] for record in records
                ),
                "max": max(record["final_context_bge_token_proxy"] for record in records),
            },
            "latency_ms": _latency_summary(latencies),
        },
        "records": records,
    }


def _context_token_proxy(context: str, model: Any) -> int:
    """Measure full formatted context with the available pinned BGE tokenizer.

    This is intentionally named a proxy: the current gateway does not publish a
    tokenizer for its generation deployment. Phase 3 must replace/configure an
    actual generation-token counter before enforcing a context limit.
    """
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        return len(context.split())
    tokenize = getattr(tokenizer, "tokenize", None)
    if callable(tokenize):
        return len(tokenize(context))
    return len(tokenizer.encode(context, add_special_tokens=False))


def _ids_by_company(
    ids: Iterable[str], chunks_by_id: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for chunk_id in ids:
        grouped.setdefault(chunks_by_id[chunk_id]["ticker"], []).append(chunk_id)
    return grouped


def _evaluate_rank_probe(
    case: dict[str, Any], retriever: ScopeAwareRetriever
) -> dict[str, Any] | None:
    probe = case.get("rank_probe")
    if not probe:
        return None
    query = probe["query"]
    ticker = probe["ticker"]
    global_results = hybrid_retrieve(
        query,
        retriever.model,
        retriever.query_prefix,
        retriever.normalized_embeddings,
        retriever.bm25_retriever,
        retriever.all_chunks,
        retriever.rrf_k,
        retriever.candidate_k,
    )
    scoped_results = hybrid_retrieve(
        query,
        retriever.model,
        retriever.query_prefix,
        retriever.normalized_embeddings,
        retriever.bm25_retriever,
        retriever.all_chunks,
        retriever.rrf_k,
        retriever.candidate_k,
        {ticker},
    )
    global_ranks = {item["chunk_id"]: rank for rank, item in enumerate(global_results, 1)}
    scoped_ranks = {item["chunk_id"]: rank for rank, item in enumerate(scoped_results, 1)}
    ranks = {
        chunk_id: {
            "global_rank": global_ranks.get(chunk_id),
            "company_rank": scoped_ranks.get(chunk_id),
        }
        for chunk_id in probe["expected_chunk_ids"]
    }
    minimum = probe.get("expected_global_rank_min", 1)
    maximum = probe.get("expected_global_rank_max", retriever.candidate_k)
    company_maximum = probe.get("expected_company_rank_max", retriever.candidate_k)
    return {
        "ticker": ticker,
        "ids": ranks,
        "rank_expectation_pass": all(
            value["global_rank"] is not None
            and minimum <= value["global_rank"] <= maximum
            and value["company_rank"] is not None
            and value["company_rank"] <= company_maximum
            for value in ranks.values()
        ),
    }


def evaluate_citations(
    cases: Sequence[dict[str, Any]], chunks_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    records = []
    latencies = []
    for case in cases:
        evidence_ids = case["final_evidence_ids"]
        missing = [chunk_id for chunk_id in evidence_ids if chunk_id not in chunks_by_id]
        if missing:
            raise ValueError(f"Citation case {case['id']} has missing ids: {missing}")
        evidence = [{"chunk": chunks_by_id[chunk_id]} for chunk_id in evidence_ids]
        started = time.perf_counter()
        parsed = citation_ids(case["answer"])
        resolution = resolve_cited_evidence(case["answer"], evidence)
        latency_ms = (time.perf_counter() - started) * 1_000
        latencies.append(latency_ms)
        visible = [item["chunk"]["chunk_id"] for item in resolution.evidence]
        expected = case["expected_visible_ids"]
        passed = visible == expected
        records.append(
            {
                "id": case["id"],
                "category": case["category"],
                "parsed_ids": parsed,
                "resolved_visible_ids": visible,
                "expected_visible_ids": expected,
                "used_fallback": False,
                "diagnostic_reason": resolution.diagnostic_reason,
                "source_display_exact": passed,
                "first_failure_stage": None if passed else "citation",
                "latency_ms": latency_ms,
            }
        )
    return {
        "summary": {
            "case_count": len(records),
            "source_display_exact_count": sum(r["source_display_exact"] for r in records),
            "source_display_exactness": (
                sum(r["source_display_exact"] for r in records) / len(records)
                if records else 0.0
            ),
            "fallback_case_count": 0,
            "latency_ms": _latency_summary(latencies),
        },
        "records": records,
    }


def corpus_fingerprint(chunks: Sequence[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk["chunk_id"].encode())
        digest.update(b"\0")
        digest.update(chunk.get("source_processed_sha256", "").encode())
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def validate_image_manifest(label_directory: Path) -> dict[str, Any]:
    payload = _read_json(label_directory / "image_nodes_v1.json")
    nodes = payload.get("nodes", [])
    expected = {(item["ticker"], item["ordinal"], item["src"]) for item in nodes}
    actual: set[tuple[str, int, str]] = set()
    # lxml is already a preprocessing dependency. Import lazily so pure unit
    # tests for metrics do not require HTML parsing.
    from lxml import html

    for path in sorted((PROJECT_ROOT / "data" / "raw").glob("*/*.html")):
        root = html.fromstring(path.read_bytes())
        for ordinal, node in enumerate(root.xpath("//img"), 1):
            actual.add((path.parent.name, ordinal, node.get("src") or ""))
    if actual != expected:
        raise ValueError(
            "Image labels differ from immutable raw HTML: "
            f"missing={sorted(actual - expected)}, stale={sorted(expected - actual)}"
        )
    counts = Counter(item["label"] for item in nodes)
    return {
        "node_count": len(nodes),
        "filing_count": len({item["ticker"] for item in nodes}),
        "label_counts": dict(sorted(counts.items())),
        "raw_html_match": True,
    }


def build_retriever(device: str) -> ScopeAwareRetriever:
    # Runtime imports are lazy to keep dataset/citation tests lightweight.
    from sentence_transformers import SentenceTransformer
    from src.backend.pipeline import build_bm25_index, load_corpus

    embeddings, chunks = load_corpus(PROJECT_ROOT)
    normalized = embeddings / np.clip(
        np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-12, None
    )
    config = MODEL_CONFIGS["bgebase"]
    return ScopeAwareRetriever(
        model=SentenceTransformer(config["repository"], device=device),
        query_prefix=config["query_prefix"],
        normalized_embeddings=normalized,
        bm25_retriever=build_bm25_index(chunks),
        all_chunks=chunks,
    )


def _write_json(path: Path, value: dict[str, Any], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite {path}; pass --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=2, ensure_ascii=False)
        file.write("\n")


def run_baseline(label_directory: Path, device: str) -> dict[str, Any]:
    resolution = evaluate_resolution(load_cases(label_directory, "company_resolution_v1.json"))
    retriever = build_retriever(device)
    retrieval = evaluate_retrieval(
        load_cases(label_directory, "retrieval_selection_v1.json"), retriever
    )
    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in retriever.all_chunks}
    citations = evaluate_citations(
        load_cases(label_directory, "citation_display_v1.json"), chunks_by_id
    )
    history_cases = load_cases(label_directory, "conversation_history_v1.json")
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": BASELINE_POLICY,
        "corpus": {
            "tickers": list(ACTIVE_FILINGS),
            "chunk_count": len(retriever.all_chunks),
            "fingerprint": corpus_fingerprint(retriever.all_chunks),
            "embedding_model": MODEL_CONFIGS["bgebase"]["repository"],
        },
        "resolution": resolution,
        "retrieval": retrieval,
        "citations": citations,
        "images": validate_image_manifest(label_directory),
        "conversation_history": {
            "case_count": len(history_cases),
            "status": "labels_frozen_not_yet_implemented",
        },
    }


def parity_fixture(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy": baseline["policy"],
        "corpus": baseline["corpus"],
        "cases": [
            {
                key: record[key]
                for key in (
                    "id",
                    "detected_tickers",
                    "scope",
                    "comparison",
                    "retrieval_scopes",
                    "subqueries",
                    "candidate_ids_by_company",
                    "selected_ids",
                    "selected_company_counts",
                    "coverage_by_subquery",
                )
            }
            for record in baseline["retrieval"]["records"]
        ],
    }


def compare_baselines(
    frozen: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Compare quality, balance, budget, latency, and exact selected IDs."""
    metric_paths = {
        "resolution_accuracy": ("resolution", "summary", "accuracy"),
        "candidate_gold_recall": (
            "retrieval", "summary", "mean_candidate_gold_recall"
        ),
        "final_gold_recall": ("retrieval", "summary", "mean_final_gold_recall"),
        "source_display_exactness": (
            "citations", "summary", "source_display_exactness"
        ),
        "retrieval_p50_latency_ms": (
            "retrieval", "summary", "latency_ms", "p50"
        ),
        "mean_context_bge_token_proxy": (
            "retrieval", "summary", "context_bge_token_proxy", "mean"
        ),
    }

    def nested(document: dict[str, Any], path: Sequence[str]) -> Any:
        value: Any = document
        for key in path:
            value = value[key]
        return value

    metrics = {}
    for name, path in metric_paths.items():
        before = nested(frozen, path)
        after = nested(current, path)
        metrics[name] = {"before": before, "after": after, "delta": after - before}

    frozen_records = {r["id"]: r for r in frozen["retrieval"]["records"]}
    current_records = {r["id"]: r for r in current["retrieval"]["records"]}
    case_ids = sorted(set(frozen_records) | set(current_records))
    case_changes = []
    for case_id in case_ids:
        before = frozen_records.get(case_id)
        after = current_records.get(case_id)
        if before is None or after is None:
            case_changes.append(
                {"id": case_id, "change": "added" if before is None else "removed"}
            )
            continue
        if (
            before["candidate_ids"] != after["candidate_ids"]
            or before["selected_ids"] != after["selected_ids"]
            or before["selected_company_counts"] != after["selected_company_counts"]
        ):
            case_changes.append(
                {
                    "id": case_id,
                    "change": "ranking_or_selection_changed",
                    "candidate_ids_before": before["candidate_ids"],
                    "candidate_ids_after": after["candidate_ids"],
                    "selected_ids_before": before["selected_ids"],
                    "selected_ids_after": after["selected_ids"],
                    "company_counts_before": before["selected_company_counts"],
                    "company_counts_after": after["selected_company_counts"],
                }
            )
    return {
        "schema_version": 1,
        "frozen_policy": frozen["policy"],
        "current_policy": current["policy"],
        "corpus_fingerprint_match": (
            frozen["corpus"]["fingerprint"] == current["corpus"]["fingerprint"]
        ),
        "metrics": metrics,
        "retrieval_case_changes": case_changes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run AVA's frozen P0 detection/retrieval/selection/citation baseline."
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABEL_DIRECTORY)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--compare-to",
        type=Path,
        help="Frozen baseline_summary.json to compare with this run.",
    )
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    baseline = run_baseline(arguments.labels, arguments.device)
    _write_json(arguments.output_directory / "baseline_summary.json", baseline, arguments.overwrite)
    _write_json(
        arguments.output_directory / "parity_fixture.json",
        parity_fixture(baseline),
        arguments.overwrite,
    )
    if arguments.compare_to:
        _write_json(
            arguments.output_directory / "comparison.json",
            compare_baselines(_read_json(arguments.compare_to), baseline),
            arguments.overwrite,
        )
    print(json.dumps({
        "output_directory": str(arguments.output_directory),
        "resolution_accuracy": baseline["resolution"]["summary"]["accuracy"],
        "candidate_gold_recall": baseline["retrieval"]["summary"]["mean_candidate_gold_recall"],
        "final_gold_recall": baseline["retrieval"]["summary"]["mean_final_gold_recall"],
        "source_display_exactness": baseline["citations"]["summary"]["source_display_exactness"],
    }, indent=2))


if __name__ == "__main__":
    main()
