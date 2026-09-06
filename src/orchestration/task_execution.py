"""Execute a validated finite task graph using the existing evidence services."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import json
import re
import time
from urllib.parse import urlsplit

from src.backend.sources import normalize_sources
from src.generation.citations import CitationVisibilityFilter, resolve_cited_evidence, visible_answer_text
from src.generation.service import count_generation_input_tokens, quarantine_uploaded_instructions
from src.orchestration.handlers import PipelineEvent
from src.orchestration.models import Freshness
from src.orchestration.planner import parse_task_plan
from src.retrieval.evidence_policy import EvidenceBudgetPolicy
from src.tools.web_search import allowed_domains_for


def qualified_market_quote(result, tickers):
    """Conservatively accept only explicitly labelled, timestamped quote excerpts."""
    text = result.excerpt
    if not all(re.search(rf"\b{re.escape(t)}\b", result.title + " " + text, re.I) for t in tickers):
        return False
    stamp = re.search(r"(?:quote timestamp|as of)\s*:\s*(\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d))", text, re.I)
    status = re.search(r"market status\s*:\s*(open|closed|pre-market|after-hours)\b", text, re.I)
    delay = re.search(r"(?:disclosed delay|delay)\s*:\s*(real.time|\d+\s*(?:minutes?|min))\b", text, re.I)
    price = re.search(r"(?:price|quote)\s*:\s*(?:USD\s*|\$)\d+(?:\.\d+)?\b", text, re.I)
    if not all((stamp, status, delay, price)):
        return False
    try:
        quote_at = datetime.fromisoformat(stamp[1].replace("Z", "+00:00"))
        retrieved_at = datetime.fromisoformat(result.retrieved_at.replace("Z", "+00:00"))
        age = (retrieved_at - quote_at).total_seconds()
    except (ValueError, TypeError):
        return False
    return 0 <= age <= (4 * 86400 if status[1].lower() == "closed" else 3600)


def upload_evidence(results):
    return [{"chunk": {"chunk_id": r.source_id, "content_type": "upload",
        "document_id": r.document_id, "filename": r.filename, "media_type": r.media_type,
        "page_number": r.page_number, "text": r.text}} for r in results]


class TaskExecutionMixin:
    async def _stream_task_plan(self, query, disconnected, trace, context, documents, scope, generator):
        from src.orchestration.executor import uploaded_evidence_matches_query

        candidates, uploaded = [], []
        if documents is not None and trace.conversation_id is not None:
            try:
                with trace.stage("uploaded_document_search"):
                    listed = await asyncio.to_thread(documents.list, trace.conversation_id)
                    uploaded = [{"filename": d.filename} for d in listed]
                    if listed:
                        candidates = (await asyncio.to_thread(documents.search, trace.conversation_id, query, limit=10))[:10]
                    for result in candidates:
                        if uploaded_evidence_matches_query(query, [result]):
                            uploaded.append({"filename": result.filename,
                                "excerpt": quarantine_uploaded_instructions(result.text)[:4000]})
            except Exception:
                trace.tool_executions.append({"tool": "upload_presearch", "status": "failed",
                                              "rejection_reason": "upload_search_unavailable"})
        if await disconnected():
            return
        try:
            with trace.stage("planning"):
                proposed = await asyncio.to_thread(generator.plan_tasks, query, context, uploaded,
                    tuple(scope or ()), self.max_web_searches, self.max_tool_executions)
                plan = parse_task_plan(proposed.model_dump_json(), original_query=query,
                    memory_candidates=getattr(context, "long_term_memories", ()),
                    selected_company_scope=tuple(scope or ()), max_web_searches=self.max_web_searches,
                    max_tool_executions=self.max_tool_executions)
        except ValueError:
            trace.route = {"status": "rejected", "rejection_reason": "invalid_task_plan"}
            async for event in self._task_answer_events("I couldn't validate that request. Please clarify the company, source, or calculation.", [], trace):
                yield event
            return
        trace.route = plan.model_dump(mode="json")
        generator = generator.for_task_execution(plan.final_answer.answer_language)
        trace.long_term_memory_ids = plan.memory_resolution.selected_memory_ids
        selected_context = replace(context, long_term_memories=tuple(
            replace(m, content=quarantine_uploaded_instructions(m.content))
            for m in context.long_term_memories if m.id in trace.long_term_memory_ids
        )) if context is not None else None
        if selected_context and trace.long_term_memory_ids and all(t.kind == "conversation" for t in plan.tasks):
            selected_context = replace(selected_context, summary="", recent_messages=())
        prompt_context = selected_context.prompt_text() if selected_context else ""
        # Only the planner-selected personal context reaches final generation.
        results, failures, calculations = {}, {}, {}
        filing_evidence = []
        records = {t.task_id: {"task_id": t.task_id, "kind": t.kind, "planned_query": t.query,
            "resolved_ticker_scope": t.ticker_scope, "selected_memory_ids": trace.long_term_memory_ids,
            "status": "completed" if t.kind in {"conversation", "clarify"} else "pending",
            "evidence_ids": []} for t in plan.tasks}
        trace.tool_executions.extend(records.values())

        async def execute(tasks, action):
            started = time.perf_counter()
            try:
                await action()
                for task in tasks:
                    records[task.task_id]["status"] = "failed" if task.task_id in failures else "completed"
            except Exception:
                for task in tasks:
                    failures[task.task_id] = "verification failed" if task.freshness == Freshness.MARKET_LIVE else "evidence or tool unavailable"
                    records[task.task_id].update(status="failed", rejection_reason=failures[task.task_id])
            finally:
                for task in tasks:
                    record = records[task.task_id]
                    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    record["evidence_ids"] = [e["chunk"]["chunk_id"] for e in results.get(task.task_id, [])]

        filing_tasks = [t for t in plan.tasks if t.kind == "filing_retrieval"]

        async def filings():
            tickers = tuple(dict.fromkeys(ticker for t in filing_tasks for ticker in t.ticker_scope))
            base = self.company_resolver.resolve(query)
            resolution = replace(base, mentions=(), unresolved_mentions=(),
                explicit_scope_tickers=(), planner_scope_tickers=tickers, needs_clarification=False,
                scope="single_company" if len(tickers) == 1 else "explicit_subset" if tickers else base.scope)
            queries = [self.company_resolver.retrieval_query(t.query, t.ticker_scope) for t in filing_tasks]
            trace.retrieval_subqueries = queries
            trace.resolver = resolution.as_dict()
            # One shared call retains cross-subquery fairness, RRF and deduplication.
            outcome = await asyncio.to_thread(self.retriever.retrieve, query, queries, resolution,
                [t.ticker_scope for t in filing_tasks], conversation_context=prompt_context)
            filing_evidence.extend(outcome.evidence)
            trace.selection = {"selected_ids": list(outcome.chunk_ids),
                "quota_satisfied": getattr(outcome, "quota_satisfied", None)}
            for t in filing_tasks:
                results[t.task_id] = [e for e in outcome.evidence if not t.ticker_scope or e["chunk"].get("ticker") in t.ticker_scope]
                if not results[t.task_id]:
                    failures[t.task_id] = "filing evidence unavailable"

        async def retrieve_task(task):
            if task.kind == "upload_retrieval":
                if documents is None or trace.conversation_id is None:
                    raise ValueError("No owner-scoped upload service.")
                matches = [r for r in candidates if uploaded_evidence_matches_query(task.query, [r])]
                if not matches:
                    raise ValueError("No relevant upload evidence.")
                results[task.task_id] = upload_evidence(matches)
            elif task.kind == "web_search":
                if not self.web_search_enabled:
                    raise ValueError("Web disabled.")
                response = await asyncio.to_thread(self.web_search.search, task.query,
                    max_results=self.web_search_max_results, source_keys=tuple(task.trusted_source_keys),
                    tickers=tuple(task.ticker_scope))
                domains = allowed_domains_for(tuple(task.trusted_source_keys), tuple(task.ticker_scope))
                accepted = []
                for index, r in enumerate(response.results[:self.web_search_max_results]):
                    url = urlsplit(r.url)
                    host = url.hostname or ""
                    if url.scheme != "https" or url.username or url.password or not any(host == d or host.endswith("." + d) for d in domains):
                        continue
                    if task.freshness == Freshness.MARKET_LIVE and not qualified_market_quote(r, task.ticker_scope):
                        continue
                    source_id = f"web-{plan.tasks.index(task) * self.web_search_max_results + index + 1}"
                    accepted.append({"chunk": {"chunk_id": source_id, "content_type": "web",
                        "title": r.title, "publisher": r.publisher, "retrieved_at": r.retrieved_at,
                        "source_url": r.url, "text": r.excerpt}})
                records[task.task_id]["web_results"] = [{"url": r.url, "retrieved_at": r.retrieved_at} for r in response.results[:self.web_search_max_results]]
                if not accepted:
                    raise ValueError("No qualifying trusted evidence.")
                results[task.task_id] = accepted
            elif task.kind == "direct_calculation":
                if not self.calculator_enabled:
                    raise ValueError("Calculator disabled.")
                value = self.calculator.calculate_query(query)
                calculations[task.task_id] = value.render()
                records[task.task_id]["calculation"] = value.as_dict()

        jobs = [execute(filing_tasks, filings)] if filing_tasks else []
        jobs.extend(execute([t], lambda t=t: retrieve_task(t)) for t in plan.tasks
                    if t.kind in {"upload_retrieval", "web_search", "direct_calculation"})
        # Maximum four jobs, all read-only and owner-scoped; no unplanned work.
        await asyncio.gather(*jobs)
        if await disconnected():
            return
        for task in plan.tasks:
            if task.kind != "evidence_calculation":
                continue

            async def calculate():
                if not self.calculator_enabled or any(d in failures for d in task.depends_on):
                    raise ValueError("Calculation dependency unavailable.")
                evidence = list({e["chunk"]["chunk_id"]: e for dep in task.depends_on for e in results.get(dep, [])}.values())
                operands = await asyncio.to_thread(generator.plan_evidence_calculation, task.query,
                    evidence, task.operation, require_periods=True)
                if not operands.ready:
                    raise ValueError("Operands unavailable.")
                value = self.calculator.calculate_operation(operands.operation,
                    [o.value for o in operands.operands], unit=operands.result_unit,
                    decimal_places=operands.decimal_places, input_text=query)
                calculations[task.task_id] = value.render() + " " + " ".join(
                    f"[{source}]" for o in operands.operands for source in o.source_ids)
                records[task.task_id]["calculation"] = value.as_dict()

            await execute([task], calculate)

        if plan.tasks[0].kind == "clarify":
            async for event in self._task_answer_events(plan.tasks[0].query, [], trace):
                yield event
            return
        evidence = list({e["chunk"]["chunk_id"]: e for e in filing_evidence + [
            e for task_id in plan.final_answer.task_ids for e in results.get(task_id, [])]}.values())
        policy = getattr(self.retriever, "evidence_policy", None) or EvidenceBudgetPolicy()

        def execution_context():
            return "\nServer task outcomes (not source evidence):\n" + json.dumps({
                "tasks": [{"kind": t.kind, "query": t.query, "status": "unavailable" if t.task_id in failures else "completed"}
                          for t in plan.tasks], "calculator_results": calculations}, ensure_ascii=False)

        def input_tokens():
            return count_generation_input_tokens(query, evidence, conversation_context=prompt_context + execution_context(),
                                                 system_prompt=generator.system_prompt)

        if input_tokens() > policy.input_token_limit:
            # Preserve whole filing chunks and their balanced allocation. Drop whole
            # supplemental tasks before allowing a mixed context to exceed its budget.
            for task in reversed(plan.tasks):
                if task.kind not in {"web_search", "upload_retrieval"}:
                    continue
                dropped = {e["chunk"]["chunk_id"] for e in results.get(task.task_id, [])}
                evidence = [e for e in evidence if e["chunk"]["chunk_id"] not in dropped]
                failures[task.task_id] = "context budget exceeded"
                records[task.task_id].update(status="failed", rejection_reason=failures[task.task_id])
                for calculation in (candidate for candidate in plan.tasks if candidate.kind == "evidence_calculation" and task.task_id in candidate.depends_on):
                    calculations.pop(calculation.task_id, None)
                    failures[calculation.task_id] = "calculation evidence unavailable"
                    records[calculation.task_id].update(status="failed", rejection_reason=failures[calculation.task_id])
                if input_tokens() <= policy.input_token_limit:
                    break
            if input_tokens() > policy.input_token_limit:
                async for event in self._task_answer_events("The available evidence exceeds the context budget. Please narrow the question.", [], trace):
                    yield event
                return
        prompt_context += execution_context()
        trace.final_generation_evidence_ids = [e["chunk"]["chunk_id"] for e in evidence]
        trace.selected_asset_ids = list(dict.fromkeys(e["chunk"]["document_id"] for e in evidence if e["chunk"].get("content_type") == "upload"))
        failure_text = " ".join(f"The requested {t.kind.replace('_', ' ')} portion {'/'.join(t.ticker_scope)} is unavailable: {failures[t.task_id]}."
                                for t in plan.tasks if t.task_id in failures)
        if not evidence and not any(t.kind == "conversation" for t in plan.tasks):
            answer = " ".join(calculations.values()) or failure_text or "The available documents do not provide enough evidence."
            async for event in self._task_answer_events(answer, [], trace):
                yield event
            return
        if self.llm_streaming:
            stream = generator.stream_answer_with_metadata(query, evidence, conversation_context=prompt_context)
            iterator = iter(stream)
            fragments, sentinel = [], object()
            citation_filter = CitationVisibilityFilter(trace.final_generation_evidence_ids)
            try:
                while True:
                    fragment = await asyncio.to_thread(next, iterator, sentinel)
                    if fragment is sentinel:
                        break
                    if await disconnected():
                        return
                    if fragment:
                        fragments.append(fragment)
                        visible = citation_filter.feed(fragment)
                        if visible:
                            trace.mark_first_token()
                            yield PipelineEvent("delta", {"text": visible})
                tail = citation_filter.finish()
                if tail:
                    yield PipelineEvent("delta", {"text": tail})
            finally:
                close_iterator = getattr(iterator, "close", None)
                if callable(close_iterator):
                    close_iterator()
                stream.close()
                trace.provider_usage = dict(getattr(stream, "usage", {}))
            answer = "".join(fragments)
            if failure_text:
                yield PipelineEvent("delta", {"text": "\n\n" + failure_text})
            async for event in self._task_answer_events(answer + ("\n\n" + failure_text if failure_text else ""), evidence, trace, emit_text=False):
                yield event
        else:
            result = await asyncio.to_thread(generator.answer_with_metadata, query, evidence,
                conversation_context=prompt_context)
            trace.provider_usage = result.usage
            async for event in self._task_answer_events(result.text + ("\n\n" + failure_text if failure_text else ""), evidence, trace):
                yield event

    async def _task_answer_events(self, answer, evidence, trace, *, emit_text=True):
        trace.generated_answer = answer
        resolved = resolve_cited_evidence(answer, evidence)
        sources, malformed = normalize_sources(list(resolved.evidence))
        trace.generated_citation_ids = list(resolved.parsed_ids)
        trace.resolved_used_ids = list(resolved.resolved_ids)
        trace.rejected_citation_ids = list(resolved.rejected_ids)
        trace.source_status = "cited_with_unrenderable_items" if sources and malformed else "cited" if sources else "none_cited"
        if emit_text:
            trace.mark_first_token()
            yield PipelineEvent("delta", {"text": visible_answer_text(answer, [e["chunk"]["chunk_id"] for e in evidence])})
        yield PipelineEvent("sources", {"sources": sources, "source_status": trace.source_status,
            "malformed_source_count": malformed}, internal={"used_source_ids": list(resolved.resolved_ids)})
        yield PipelineEvent("done", {})
