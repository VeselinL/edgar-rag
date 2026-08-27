"""Aggregate AVA request records without coupling to a metrics backend."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
import statistics
from typing import Any, Sequence


def percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] * (upper - position) + ordered[upper] * (position - lower))


def _latency(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "mean": statistics.fmean(values) if values else None,
        "max": max(values) if values else None,
    }


def summarize_request_records(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compute the Phase 4 operations metrics from versioned request records."""
    stages = sorted({name for record in records for name in record["stage_latency_ms"]})
    intervals = []
    for record in records:
        started = datetime.fromisoformat(record["started_at"])
        intervals.append((started, 1))
        intervals.append((started + timedelta(milliseconds=record["complete_latency_ms"]), -1))
    active = maximum = 0
    for _, delta in sorted(intervals, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return {
        "request_count": len(records),
        "stage_latency_ms": {
            stage: _latency([
                record["stage_latency_ms"][stage]
                for record in records if stage in record["stage_latency_ms"]
            ])
            for stage in stages
        },
        "time_to_first_token_ms": _latency([
            record["time_to_first_token_ms"]
            for record in records if record["time_to_first_token_ms"] is not None
        ]),
        "complete_latency_ms": _latency([record["complete_latency_ms"] for record in records]),
        "error_rate": sum(record["safe_error_class"] is not None for record in records) / len(records) if records else 0.0,
        "cancellation_rate": sum(record["cancelled"] for record in records) / len(records) if records else 0.0,
        "observed_max_concurrency": maximum,
        "provider_total_tokens": sum(record["provider_usage"].get("total_tokens", 0) for record in records),
        "qdrant_latency_ms": None,
    }
