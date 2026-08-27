# AVA observability

AVA emits one backend-only structured `ava_request_completed` record for every
real pipeline request, including successful, clarified, policy-failed, errored,
and cancelled streams. The browser receives only its opaque `X-Request-ID`; it
never receives retrieval ranks, prompts, provider errors, or the record itself.

The schema is implemented by `src/observability/request_trace.py`. It records the
corpus/index version, original query, resolver decision, retrieval subqueries,
candidate provenance, quota and token allocation, final evidence, answer,
citations, displayed-source status, stage and complete latency, time to first
token, provider token usage when supplied, cancellation, and a redacted error
class. Empty image and memory fields intentionally reserve those future
boundaries without claiming those features exist.

Logs can contain user questions and model answers and therefore require an
access-controlled operational sink. They must not be copied into browser
analytics or treated as conversation history. `AVA_OBSERVABILITY_RETENTION_DAYS`
defaults to 30 and describes the required retention policy for the external log
sink; the stateless application does not persist or delete log records itself.
Production deployment must configure the collector to enforce that independent
retention period and restrict access.

`src.observability.summarize_request_records` calculates p50/p95 stage, first
token, and complete latency; provider tokens; error/cancellation rates; and
observed concurrency from exported records. Qdrant latency remains `null` until
the Phase 5 shadow retriever supplies that stage.

Generation and citation quality run independently from retrieval:

```bash
PYTHONPATH=. .venv/bin/python -m src.evaluation.generation_quality \
  --output /tmp/ava-generation-reference.json

PYTHONPATH=. .venv/bin/python -m src.evaluation.generation_quality \
  --answers provider --judge-provider \
  --output /tmp/ava-generation-provider.json
```

The reference mode validates the reviewed metric and citation contracts without
provider drift. Provider mode generates fresh answers from fixed final evidence;
the optional judge audits every atomic factual claim, but never changes sources
or runtime answers. Save the model, corpus version, raw per-case results, and
timestamp with any release decision.
