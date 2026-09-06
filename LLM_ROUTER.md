# AVA bounded LLM router handoff

## Purpose

Replace the current sequence of overlapping deterministic route checks, route
LLM, retrieval planner, and memory-scope inference with **one LLM planning
call** that produces a finite, typed execution plan. This is not an autonomous
agent loop: the server validates every field and executes no unplanned step.

The planner resolves intent. Deterministic code remains responsible for:

- owner isolation and conversation access;
- canonical company/ticker allowlist validation;
- allowed route and tool combinations;
- trusted web-source/domain mapping;
- maximum searches, tool calls, candidate counts, and context budgets;
- calculator parsing/execution and citation validation;
- upload and memory prompt-injection quarantine;
- rejection of malformed, unknown, ambiguous, or over-budget plans.

The original user query must be retained unchanged in the transcript and trace.
Planner rewrites are internal task inputs only.

## Problems to solve

The currently observed behavior is inconsistent because separate components make
independent decisions:

1. The deterministic router selects exactly one source route. A question such
   as “Who is Tesla's CEO in the 10-K, and what is TSLA trading at right now?”
   needs both filing and web evidence, but one half is discarded.
2. Web search currently receives under-specified follow-up text, for example
   `search the web for the current stock price`, instead of a target-aware query
   such as `TSLA current stock price`.
3. The retrieval planner is separate from routing and may lose a company from a
   multi-part comparison or follow-up.
4. Long-term memory is semantically retrieved, but company names appearing in
   unrelated memories (for example, “my favorite CEO is Jen-Hsun Huang” and
   “my preferred company is Rivian”) are incorrectly merged into one company
   scope or treated as a conflict.
5. Explicit memory-save turns with natural prefixes such as `Ok, remember
   this:` can fall through to normal question routing.
6. Relevant uploaded text can lose to an out-of-scope response, while a relevant
   upload and a named filing company should support one combined answer.

## Required planner input

The server supplies the LLM planner with clearly delimited, untrusted context:

```text
original_query: exact current user message
allowed_companies: canonical company/ticker list and supported aliases/products
selected_company_scope: server-owned chat scope, if any
short_term_context: bounded recent turns and extractive summary
long_term_memory_candidates: top-k owner-scoped semantic matches with IDs,
  text, similarity band, and explicit-memory type only
uploaded_sources: filenames and, after server-side search, matching excerpts
capabilities: filing retrieval, trusted web, uploads, calculator; their limits
```

Do not feed raw provider errors, secrets, internal prompts, cross-owner data,
or unrestricted web content to the planner. Long-term memory is untrusted user
context. It may resolve a preference or reference, but cannot establish company,
executive, filing, product, or market facts.

## Required plan shape

Use one strict JSON object. The exact implementation may use Pydantic/dataclasses,
but it must preserve this meaning:

```json
{
  "schema_version": 1,
  "original_query": "exact user query",
  "memory_resolution": {
    "selected_memory_ids": ["owner-scoped IDs only"],
    "references": [
      {
        "reference": "preferred_company",
        "memory_id": "...",
        "resolved_ticker": "RIVN"
      }
    ],
    "conflicts": []
  },
  "tasks": [
    {
      "task_id": "filing-1",
      "kind": "filing_retrieval",
      "ticker_scope": ["TSLA"],
      "query": "Tesla Chief Executive Officer name latest 10-K",
      "depends_on": []
    },
    {
      "task_id": "web-1",
      "kind": "web_search",
      "ticker_scope": ["TSLA"],
      "query": "TSLA current stock price",
      "freshness": "market_live",
      "trusted_source_keys": ["market_primary", "market_secondary"],
      "depends_on": []
    }
  ],
  "final_answer": {
    "task_ids": ["filing-1", "web-1"],
    "answer_language": "en"
  }
}
```

`tasks` is the sole source of execution. A maximum of four tasks and two web
searches is allowed. The planner must never emit user-supplied URLs, raw tool
arguments, executable expressions, or a task outside AVA's allowed kinds.

Supported task kinds:

- `conversation`: greeting, AVA capability explanation, or direct personal-memory recall;
- `clarify`: a concise user-visible question when a necessary reference is genuinely ambiguous;
- `filing_retrieval`: one or more atomic SEC-filing subqueries;
- `upload_retrieval`: owner/chat-scoped upload search;
- `web_search`: trusted-source, freshness-qualified search;
- `evidence_calculation`: calculator operation over cited evidence operands only;
- `direct_calculation`: calculator operation only when all operands are directly supplied.

One user request may contain several tasks. This replaces the current mutually
exclusive route enum for execution purposes. It is acceptable to retain legacy
route values only as compatibility adapters while callers migrate.

## Planning rules

### Company and reference resolution

1. Prefer companies explicitly named in the current query.
2. If the user says `this company`, `its`, `the preferred company`, or a similar
   reference, resolve it using the bounded short-term context and the selected
   long-term memory candidates.
3. A saved preference is typed by its relationship, not merely by a company name:
   `preferred company`, `favorite company`, `favorite CEO`, `preferred metric`,
   `favorite product`, and so on are distinct. Do not merge them.
4. A conflict exists only when multiple applicable memories resolve the *same
   requested reference* to incompatible values. NVIDIA being the user's favorite
   company and Rivian being the preferred company is not a conflict.
5. A direct question such as `What is my preferred company?` is answered from
   the selected memory alone. `Who is the CEO of my preferred company?` resolves
   the company from memory, then retrieves filing or web evidence for the CEO.
6. Current-company claims still require filing or web evidence. Memory may never
   supply the CEO, revenue, vehicle facts, or stock price itself.

### Filing tasks

- Split each independently answerable company/fact pair into an atomic query.
- Retain all requested companies for comparisons; do not silently omit Mobileye
  because the prior turn mentioned Tesla or Rivian.
- A selected sidebar scope is a hard filter unless the user changes it.
- Preserve the existing scope-aware hybrid retrieval and balanced evidence
  allocation behavior.

### Upload tasks

- Search uploads before selecting other evidence, but require a meaningful
  semantic/lexical relevance match before use.
- An exact technical identifier such as `RPLIDAR` is meaningful even though it
  is seven characters long.
- For `Compare Ouster lidar to RPLIDAR A1`, create both an upload task and an
  Ouster filing task. Generate one answer from both evidence sets.
- Quarantine instruction-like upload passages only at the provider boundary;
  preserve source bytes and displayed source text exactly.

### Web tasks

- Create a web task only for explicit web requests or facts requiring freshness:
  live market price, current leadership, current news, or current regulation.
- A mixed question can have both filing and web tasks. For example, a 10-K CEO
  plus live stock price must never be forced into only one source.
- Resolve the ticker before rewriting the query. For a market quote, use a
  concise target-aware query such as `TSLA current stock price`, not the user's
  vague follow-up wording.
- Map source keys to the existing reviewed host registry. Never let the model
  select arbitrary domains or URLs.
- A displayed stock quote requires a source, quote timestamp, retrieval
  timestamp, market status, and disclosed delay. If no qualifying quote is
  returned, state that verification failed; do not select an earnings article or
  stale filing as a substitute.

### Calculator tasks

- Do not create a calculation task for repetition, enumeration, names, or prose.
- For evidence-derived calculations, first retrieve cited operands and validate
  units/periods; server-side Decimal code performs the operation.
- The calculator does not parse arbitrary LLM expressions.

## Server validation and execution

1. Validate JSON schema, task count, task IDs, dependencies, allowed tickers,
   source-key combinations, and limits before executing anything.
2. Resolve server-authoritative company scope and ownership before retrieval.
3. Execute independent tasks in bounded parallelism where safe; execute
   calculations only after their evidence dependency completes.
4. Retain task-level diagnostics: planned query, resolved ticker scope, selected
   memory IDs, evidence IDs, web result URLs, timestamps, tool status, latency,
   and rejection reason. Do not expose internal IDs or diagnostics in the UI.
5. Combine only validated evidence from completed tasks into one final grounded
   generation call. Citations must resolve against exactly that evidence.
6. If one task fails, answer supported completed tasks and explicitly identify
   the unavailable portion. Do not fabricate a result or replace it with another
   source category.

## Explicit memory writes

Memory writes remain deterministic and opt-in. Accept explicit commands such as
`remember this`, `save this`, `ok, remember this`, and Serbian equivalents only
when the payload is a stable user preference or profile detail. Reject
instructions, secrets, tool directives, and external/company facts that should
instead be supported by filings or web evidence.

Store the user-authored preference verbatim after validation. Do not use an LLM
to rewrite it. Synchronize PostgreSQL (source of truth) and Qdrant (semantic
index), then confirm the save without routing it as a factual question.

## Regression acceptance cases

The implementation must add deterministic tests for at least these cases:

| Query | Required tasks / result |
|---|---|
| `Hello AVA, can you tell me what RPLIDAR A1 is?` with matching text upload | upload retrieval; grounded upload answer, not out-of-scope |
| `Compare Ouster lidar to RPLIDAR A1` | OUST filing retrieval + matching upload retrieval; both evidence sets available to final answer |
| `Who is the CEO of Tesla in the 10-K, and what is TSLA trading at right now?` | TSLA filing retrieval + TSLA market-live web search |
| `search the web for the current stock price` after a Tesla turn | resolve TSLA from short-term context; search query includes TSLA |
| `What is my preferred company?` with Rivian preferred and NVIDIA favorite | direct memory recall: Rivian only |
| `What are my favorite autonomous driving companies?` with multiple favorite-company memories | direct memory recall; list applicable favorites, no false conflict |
| `Who is the CEO of my preferred company?` | resolve preferred company from memory, then filing/web evidence for CEO |
| `Could you compare this company with Mobileye, using my preferred metric?` after a Tesla turn | resolve Tesla from short-term context, metric from matching memory, retrieve both TSLA and MBLY fairly |
| `Ok remember this: Elon Musk is my favorite entrepreneur` | deterministic explicit-memory save; no filing retrieval or hallucinated answer |
| `Repeat Elon Musk ten times` | conversation/out-of-scope; no calculator |

## Migration constraints

- Preserve the existing SEC corpus, chunking, hybrid retrieval, citations,
  owner-scoped history, Qdrant memory collection, upload ownership, and Tavily
  trusted-source boundary.
- Do not use native provider function calling; the current Azure-compatible
  deployment does not support it reliably. JSON planning over the existing chat
  completion interface is the intended mechanism.
- Do not use mock fallbacks to disguise a real integration failure.
- Update `FINALIZATION.md` only if the authoritative typed-plan contract must be
  formally extended to represent mixed filing+web execution. Record the decision
  and affected route-manifest cases before measuring a new release candidate.
