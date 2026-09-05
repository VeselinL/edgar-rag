"""Execute AVA's frozen Phase 7 conversation-history acceptance manifest."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Protocol, Sequence
from uuid import uuid4

from src.conversations.context import ConversationContextBuilder
from src.conversations.memory import InMemoryMemoryStore
from src.conversations.models import MemoryItem
from src.conversations.repository import InMemoryConversationRepository
from src.conversations.service import ConversationService
from src.generation.rag import DEFAULT_LLM_MODEL, GenerationService, make_llm_client
from src.resolution.companies import default_company_resolver


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = (
    PROJECT_ROOT / "data/evaluation/ava_p0/v1/conversation_history_v1.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data/evaluation/ava_p0/v1/runs/phase-7-conversation-history.json"
)
PLANNER_CATEGORIES = {"follow_up", "topic_switch", "summary_recall"}
STATE_CATEGORIES = {
    "deletion",
    "tenant_isolation",
    "conversation_isolation",
}


class RetrievalPlanner(Protocol):
    def plan_retrieval(
        self,
        original_query: str,
        deterministic_resolution: Any = None,
        conversation_context: str = "",
    ) -> dict[str, Any]: ...


def load_history_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError("Unsupported conversation-history manifest schema.")
    identifiers = [case.get("id") for case in cases]
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise ValueError("Every conversation-history case needs a non-empty ID.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Conversation-history case IDs must be unique.")
    for case in cases:
        category = case.get("category")
        if category not in PLANNER_CATEGORIES | STATE_CATEGORIES:
            raise ValueError(f"Unsupported history category: {category!r}.")
        if category in PLANNER_CATEGORIES:
            turns = case.get("turns")
            expected = case.get("expected_tickers_by_turn")
            if (
                not isinstance(turns, list)
                or not turns
                or not all(isinstance(turn, str) and turn.strip() for turn in turns)
                or not isinstance(expected, list)
                or len(turns) != len(expected)
                or not all(
                    isinstance(tickers, list)
                    and all(isinstance(ticker, str) for ticker in tickers)
                    for tickers in expected
                )
            ):
                raise ValueError(f"History case {case['id']} has invalid turns or labels.")
    return cases


def format_scope_evaluation_context(previous_turns: Sequence[str]) -> str:
    """Match runtime history shape without generating or leaking answer labels."""
    lines = ["Recent conversation turns (not filing evidence):"]
    for query in previous_turns:
        lines.append(f"User: {query}")
        lines.append("Assistant: [Answer omitted from company-scope evaluation.]")
    return "\n".join(lines) if previous_turns else ""


def _turn_gate(
    case: dict[str, Any], turn_index: int, deterministic_tickers: Sequence[str]
) -> str:
    expected = case["expected_tickers_by_turn"][turn_index]
    if turn_index == 0:
        return "standalone"
    if list(deterministic_tickers) == expected:
        prior = case["expected_tickers_by_turn"][turn_index - 1]
        return "topic_switch" if prior != expected else "standalone"
    return case["category"]


def _accuracy(records: Sequence[dict[str, Any]], key: str) -> float:
    return (
        sum(bool(record[key]) for record in records) / len(records)
        if records
        else 1.0
    )


def _validated_tickers(
    deterministic: Any, plan: dict[str, Any]
) -> list[str]:
    resolution = default_company_resolver.apply_planner_resolution(
        deterministic,
        plan.get("company_mentions", []),
        plan["resolved_tickers"],
    )
    return list(resolution.resolved_tickers)


def _planner_outcome(
    planner: RetrievalPlanner,
    query: str,
    deterministic: Any,
    context: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    plan: dict[str, Any] | None = None
    try:
        plan = planner.plan_retrieval(query, deterministic, context)
        return {
            "tickers": _validated_tickers(deterministic, plan),
            "latency_ms": (time.perf_counter() - started) * 1_000,
            "normalizations": list(plan.get("_normalizations", [])),
            "error": None,
        }
    except Exception as error:  # provider audits must retain later case results
        return {
            "tickers": [],
            "latency_ms": (time.perf_counter() - started) * 1_000,
            "normalizations": (
                list(plan.get("_normalizations", [])) if plan is not None else []
            ),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }


def evaluate_planner_history(
    cases: Sequence[dict[str, Any]], planner: RetrievalPlanner
) -> dict[str, Any]:
    """Compare query-only and history-aware planner scope turn by turn."""
    records: list[dict[str, Any]] = []
    for case in cases:
        if case["category"] not in PLANNER_CATEGORIES:
            continue
        previous_turns: list[str] = []
        for turn_index, query in enumerate(case["turns"]):
            expected = case["expected_tickers_by_turn"][turn_index]
            deterministic = default_company_resolver.resolve(query)
            query_only = _planner_outcome(planner, query, deterministic)
            context = format_scope_evaluation_context(previous_turns)
            if context:
                contextual = _planner_outcome(
                    planner, query, deterministic, context
                )
            else:
                contextual = query_only
            baseline_tickers = query_only["tickers"]
            contextual_tickers = contextual["tickers"]
            records.append(
                {
                    "case_id": case["id"],
                    "turn_index": turn_index,
                    "gate": _turn_gate(
                        case, turn_index, deterministic.resolved_tickers
                    ),
                    "query": query,
                    "expected_tickers": expected,
                    "deterministic_tickers": list(deterministic.resolved_tickers),
                    "query_only_tickers": baseline_tickers,
                    "contextual_tickers": contextual_tickers,
                    "query_only_pass": (
                        query_only["error"] is None and baseline_tickers == expected
                    ),
                    "contextual_pass": (
                        contextual["error"] is None
                        and contextual_tickers == expected
                    ),
                    "query_only_latency_ms": query_only["latency_ms"],
                    "contextual_latency_ms": contextual["latency_ms"],
                    "query_only_normalizations": query_only["normalizations"],
                    "contextual_normalizations": contextual["normalizations"],
                    "query_only_error": query_only["error"],
                    "contextual_error": contextual["error"],
                    "context_turn_count": len(previous_turns),
                }
            )
            previous_turns.append(query)

    by_gate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_gate[record["gate"]].append(record)
    history = [
        record
        for record in records
        if record["gate"] in {"follow_up", "summary_recall"}
    ]
    standalone = [record for record in records if record["gate"] == "standalone"]
    topic_switch = [record for record in records if record["gate"] == "topic_switch"]
    history_baseline = _accuracy(history, "query_only_pass")
    history_contextual = _accuracy(history, "contextual_pass")
    standalone_baseline = _accuracy(standalone, "query_only_pass")
    standalone_contextual = _accuracy(standalone, "contextual_pass")
    topic_switch_contextual = _accuracy(topic_switch, "contextual_pass")
    summary = {
        "turn_count": len(records),
        "query_only_accuracy": _accuracy(records, "query_only_pass"),
        "contextual_accuracy": _accuracy(records, "contextual_pass"),
        "history_dependent_count": len(history),
        "history_query_only_accuracy": history_baseline,
        "history_contextual_accuracy": history_contextual,
        "history_accuracy_delta": history_contextual - history_baseline,
        "standalone_count": len(standalone),
        "standalone_query_only_accuracy": standalone_baseline,
        "standalone_contextual_accuracy": standalone_contextual,
        "standalone_regression_count": sum(
            record["query_only_pass"] and not record["contextual_pass"]
            for record in standalone
        ),
        "topic_switch_count": len(topic_switch),
        "topic_switch_contextual_accuracy": topic_switch_contextual,
        "planner_error_count": sum(
            record["query_only_error"] is not None
            or record["contextual_error"] is not None
            for record in records
        ),
        "by_gate": {
            gate: {
                "count": len(values),
                "query_only_accuracy": _accuracy(values, "query_only_pass"),
                "contextual_accuracy": _accuracy(values, "contextual_pass"),
            }
            for gate, values in sorted(by_gate.items())
        },
    }
    summary["gate_pass"] = bool(
        history
        and history_contextual == 1.0
        and history_contextual > history_baseline
        and standalone_contextual >= standalone_baseline
        and summary["standalone_regression_count"] == 0
        and topic_switch_contextual == 1.0
    )
    return {"summary": summary, "records": records}


def _service_pair() -> tuple[
    InMemoryConversationRepository,
    InMemoryMemoryStore,
    ConversationService,
]:
    repository = InMemoryConversationRepository()
    memory = InMemoryMemoryStore()
    service = ConversationService(
        repository,
        tenant_id="tenant-a",
        user_id="user-a",
        context_builder=ConversationContextBuilder(repository),
        memory_store=memory,
        long_term_score_threshold=0.0,
    )
    return repository, memory, service


def _complete(service: ConversationService, conversation_id: str, text: str) -> None:
    turn_id = str(uuid4())
    service.begin_turn(conversation_id, turn_id, text, str(uuid4()))
    service.complete_turn(
        conversation_id,
        turn_id,
        "Scope evaluation answer.",
        {"sources": [], "source_status": "none_cited"},
        [],
    )


def _in_memory_relational_rows(
    repository: InMemoryConversationRepository,
) -> int:
    """Count every authoritative row in the deterministic evaluation double."""
    return (
        len(repository._conversations)
        + sum(len(messages) for messages in repository._messages.values())
        + len(repository._summaries)
    )


def evaluate_state_cases(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Execute deletion and isolation labels without provider dependencies."""
    records: list[dict[str, Any]] = []
    for case in cases:
        category = case["category"]
        if category == "deletion":
            repository, memory, service = _service_pair()
            first = service.create(memory_enabled=True)
            _complete(service, first.id, "Tesla durable context")
            if case["action"] == "delete_all_user_conversations":
                second = service.create(memory_enabled=True)
                _complete(service, second.id, "Ford durable context")
                service.delete_all()
            elif case["action"] == "delete_conversation":
                service.delete(first.id)
            else:
                raise ValueError(f"Unsupported deletion action: {case['action']!r}.")
            relational_rows = _in_memory_relational_rows(repository)
            memory_points = len(memory.items)
            passed = (
                relational_rows == case["expected_relational_rows"]
                and memory_points == case["expected_memory_points"]
            )
            records.append(
                {
                    "case_id": case["id"],
                    "category": category,
                    "action": case["action"],
                    "relational_rows": relational_rows,
                    "memory_points": memory_points,
                    "pass": passed,
                }
            )
            continue

        if category not in {"tenant_isolation", "conversation_isolation"}:
            continue
        repository, memory, owner = _service_pair()
        seeded = owner.create(memory_enabled=True)
        seed_content = "PRIVATE_AURORA_SEED"
        _complete(owner, seeded.id, seed_content)
        memory.upsert_summary(
            MemoryItem(
                id=f"seed:{seeded.id}",
                tenant_id="tenant-a",
                user_id="user-a",
                conversation_id=seeded.id,
                source_id="seed-source",
                memory_type="conversation_summary",
                content=seed_content,
            )
        )
        if category == "tenant_isolation":
            query_service = ConversationService(
                repository,
                tenant_id="tenant-a",
                user_id="user-b",
                memory_store=memory,
                long_term_score_threshold=0.0,
            )
        else:
            query_service = owner
        query_conversation = query_service.create()
        context = query_service.prepare_context(
            query_conversation.id, str(uuid4()), seed_content
        ).prompt_text()
        observed = seed_content in context
        # Phase 6 makes normal-chat long-term memory available across an
        # owner's chats. Tenant isolation remains strict; same-owner memory is
        # an intended reference-resolution aid, never factual evidence.
        expected = category == "conversation_isolation"
        records.append(
            {
                "case_id": case["id"],
                "category": category,
                "expected_seed_content_in_context": expected,
                "observed_seed_content_in_context": observed,
                "pass": observed is expected,
            }
        )
    return {
        "summary": {
            "case_count": len(records),
            "pass_count": sum(record["pass"] for record in records),
            "gate_pass": bool(records) and all(record["pass"] for record in records),
        },
        "records": records,
    }


def evaluate_conversation_history(
    cases: Sequence[dict[str, Any]], planner: RetrievalPlanner
) -> dict[str, Any]:
    planner_result = evaluate_planner_history(cases, planner)
    state_result = evaluate_state_cases(cases)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "planner": planner_result,
        "state": state_result,
        "gate_pass": (
            planner_result["summary"]["gate_pass"]
            and state_result["summary"]["gate_pass"]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the frozen Phase 7 conversation-history acceptance gate."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        raise FileExistsError(f"Refusing to overwrite {args.output}; pass --overwrite.")
    cases = load_history_manifest(args.manifest)
    planner = GenerationService(make_llm_client(), model=args.model)
    result = evaluate_conversation_history(cases, planner)
    result["model"] = args.model
    result["manifest"] = str(args.manifest.relative_to(PROJECT_ROOT))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "gate_pass": result["gate_pass"],
        "planner": result["planner"]["summary"],
        "state": result["state"]["summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
