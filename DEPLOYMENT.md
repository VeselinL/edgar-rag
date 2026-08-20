# AVA Deployment Guide

**Status date:** 20 August 2026
**Product:** AVA — Autonomous Vehicle Analyst

This document defines the local vertical slice and the direction for a later production deployment. It does not select a hosting provider.

## System architecture

```text
Browser
  → React + TypeScript frontend
  → FastAPI API
  → shared LLM-planned scope-aware hybrid retrieval pipeline
  → existing OpenAI-compatible LLM generation
```

The repository name and product identity are separate. Internal code and artifact names may retain the repository's historical name; the browser interface is branded only as **AVA**, short for **Autonomous Vehicle Analyst**.

The frontend owns presentation and current-tab transcript state. It contains no API credentials, model calls, company detection, Comparison Cue detection, subquery planning, retrieval policy, citation resolution, or raw internal chunk handling. It sends the user's original query unchanged.

FastAPI is a thin adapter around independently usable Python modules. The same shared scope-aware entry point is used by the deployed API, `notebooks/hybrid_rag_generation.ipynb`, and `src/scripts/evaluate_scope_aware_hybrid_retrieval.py` (the repository implementation of `evaluate_scope_aware_retrieval`). The notebook is not a runtime dependency. The adapter validates requests, invokes the shared pipeline, streams generation events, resolves citations, and maps internal chunks to frontend-safe source objects.

The first version is stateless. A browser tab may display several messages, but only the current query is sent in each request. Reloading clears the transcript. No conversation memory, account, upload, corpus-management, or persistent-history service is present.

### Source-of-truth reconciliation

Repository inspection before this deployment work found three related but previously separate experiments:

- The current main-branch `notebooks/hybrid_rag_generation.ipynb` supplies the grounded prompt, LLM planner, evidence-allocation pattern, citation syntax, 10 candidates per subquery, minimum two-chunk subquery coverage, and 10-chunk final context budget.
- `src/scripts/evaluate_scope_aware_hybrid_retrieval.py` supplies the authoritative regex aliases/tickers, Comparison Cues, scope labels, dense/BM25 retrieval, RRF, scoped merging, and evaluation output. It is the validation reference.
- `notebooks/test_reranking.ipynb` contains the current BGE cross-encoder experiment, but reranking was not yet integrated into either path.

The production adapter now follows that notebook path directly: the LLM planner creates atomic subqueries; every subquery uses the shared regex scope policy and hybrid dense/BM25 RRF retrieval; candidates are merged by stable chunk ID; coverage selection reserves at least two available chunks for each subquery; a `0.01` bonus rewards evidence retrieved for multiple subqueries; and relevance fills the remaining slots up to 10. A plan requiring more coverage than the fixed budget can provide is rejected instead of silently starving a subquery. The cross-encoder experiment is not part of this active generation path.

## Local development architecture

### Processes and ports

- Vite serves the React development build, normally at `http://localhost:5173`.
- Uvicorn serves FastAPI, normally at `http://localhost:8000`.
- Local CORS allows only the configured development origin, defaulting to `http://localhost:5173`.
- The frontend reads its API origin from `VITE_API_BASE_URL`; the default local value is `http://localhost:8000`.

The frontend and backend run as separate processes so hot reload and streaming behaviour match the eventual deployment boundary.

### Environment variables

Backend secrets live in the repository-root `.env` during local development and are loaded through the existing `python-dotenv` convention. `.env` is ignored and must never be committed. The frontend may receive only public `VITE_*` values; Vite embeds them in the browser bundle.

Real mode uses:

```dotenv
AVA_PIPELINE_MODE=real
AVA_CORS_ORIGINS=http://localhost:5173
AVA_QUERY_MAX_LENGTH=4000
AVA_MODEL_DEVICE=cpu
AVA_LLM_MODEL=<gateway deployment/model name>
AVA_LLM_STREAMING=true
OPENAI_API_KEY=<backend secret>
OPENAI_API_URL=<OpenAI-compatible or Azure gateway base URL>
OPENAI_APP_ID=<optional gateway header>
OPENAI_USER_ID=<optional gateway header>
OPENAI_COMPANY_ID=<optional gateway header>
OPENAI_API_VERSION=<optional gateway header>
```

The repository's current LLM integration uses the OpenAI Python client with `OPENAI_API_KEY`, `OPENAI_API_URL`, and optional gateway headers. Despite Azure-backed deployment naming, the checked-in notebook uses an OpenAI-compatible `base_url`, not `AzureOpenAI`; the adapter preserves that verified call shape.

`AVA_LLM_STREAMING` is strictly `true` or `false`. With `true`, AVA requires a
genuine provider `text/event-stream` response and forwards its fragments. With
`false`, AVA makes a normal completion request and emits the complete grounded
answer as one SSE `delta`. It never splits or delays a buffered answer to imitate
token streaming. The currently configured local Unique route requires `false`.

Mock mode is explicit:

```dotenv
AVA_PIPELINE_MODE=mock
```

Mock mode does not load embeddings, BGE, BM25, or an LLM client. It emits deterministic real-time test events for normal, pre-token-failure, and mid-stream-failure cases. It is for frontend development and automated tests only. The health response exposes the active mode so mock readiness cannot be mistaken for the production pipeline.

### Startup and readiness

In real mode, application startup loads and validates the ten chunk files and aligned BGE-base NPZ artifacts, normalizes the embedding matrix, builds the in-memory BM25 index, then loads the BGE query embedder. Loading occurs once per API process, not once per request. Startup fails closed when artifact counts or required configuration are invalid.

`GET /api/health` distinguishes liveness from readiness:

```json
{
  "status": "ok",
  "mode": "real",
  "pipeline_ready": true,
  "answer_delivery": "buffered"
}
```

An alive process that has not completed loading reports `pipeline_ready: false`; readiness-sensitive infrastructure should not route chat traffic until it becomes true.
`answer_delivery` is `buffered`, `provider_streaming`, or `mock_streaming`, so
operators can distinguish corpus readiness from the configured answer transport.

Start the backend from the repository root:

```bash
.venv/bin/uvicorn src.backend.app:app --reload --port 8000
```

Start the frontend in another terminal:

```bash
cd src/frontend
npm install
npm run dev
```

Use `npm run build` for the static production bundle. In a static deployment, set `VITE_API_BASE_URL` at build time to the public backend origin.

## API request lifecycle

One `POST /api/chat/stream` request performs this lifecycle:

1. The frontend submits `{ "query": "..." }` with the original current query only.
2. FastAPI validates that the query is non-empty and no longer than the configured limit.
3. The backend LLM planner returns the notebook's structured plan containing atomic subqueries and any deterministic operation label; it does not answer the question.
4. For every subquery, shared regex logic detects supported company names, tickers, and aliases, while Comparison Cue logic determines single-company, explicit subset, anchored-global, enumeration, or global scope. A single-company scope detected in the original query is inherited by its subqueries.
5. Every subquery runs normalized BGE dense search and BM25 search; reciprocal-rank fusion combines them and retains up to 10 candidates.
6. Candidates from all subqueries are merged and deduplicated by stable internal chunk ID while preserving each subquery match and rank.
7. Coverage selection takes at least two available chunks per subquery in rounds. Remaining slots are filled by best RRF score plus the notebook's `0.01` multi-subquery bonus.
8. At most 10 unique selected chunks are formatted with internal source IDs and passed to the grounded generation prompt with the original query.
9. With `AVA_LLM_STREAMING=true`, the provider's real streaming iterator yields non-empty text fragments and FastAPI emits each immediately. With `false`, the provider returns one completed answer and FastAPI emits that answer as one `delta` event.
10. Citation IDs found in the generated answer are resolved only against the selected generation evidence.
11. The adapter emits normalized user-facing source objects in one `sources` event. If no valid citation can be resolved, it returns the final evidence supplied to generation and records that fallback in backend diagnostics.
12. One `done` event terminates a successful stream.

Internal chunk IDs remain available for correlation, validation, and logs, but are not the primary source representation shown in the UI. A source is never returned as answer support unless it was in the final generation context.

## Streaming transport

The chat route is a streaming `POST` implemented with FastAPI `StreamingResponse`. It uses Server-Sent Event framing over browser `fetch`, because native `EventSource` cannot carry the JSON POST body.

The response uses standard SSE frames (`event: <type>` followed by `data: <JSON>` and a blank line). Event payloads are structured JSON:

```text
event: delta
data: {"text":"new generated fragment"}

event: sources
data: {"sources":[]}

event: done
data: {}

event: error
data: {"message":"Safe user-facing error message"}
```

Event order for success is one or more `delta` events, one `sources`, then one `done`. Buffered mode always emits exactly one non-empty `delta`. Empty provider fragments are ignored. A failure after the response begins emits one safe `error` event and closes the stream; it does not emit `done`. Validation errors before streaming use a normal JSON HTTP error response.

Required response headers are:

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

Production reverse proxies must disable response buffering for this route. Compression that buffers small chunks should also be disabled. Keep-alive comments may be added only if infrastructure timeouts require them. The backend checks for client disconnects between stages and streamed fragments where practical and closes the provider iterator on cancellation.

## Frontend source contract

Backend-only fields such as chunk IDs, retrieval indices, embedding scores, RRF scores, and reranker scores are excluded from the normal API source representation.

### Narrative source

The repository uses `content_type: "narrative"` internally; the API normalizes it to `"text"`:

```json
{
  "company": "Tesla, Inc.",
  "ticker": "TSLA",
  "filing_year": 2025,
  "section": "Item 1 — Business",
  "content_type": "text",
  "text": "The complete source chunk shown to the user.",
  "source_url": "https://www.sec.gov/Archives/..."
}
```

### Table source

Table chunks already contain validated table-schema-v2 logical data. The adapter maps `logical_column_headers` and `logical_rows` directly; it never asks the browser to reverse Markdown from the chunk's search/display text.

```json
{
  "company": "Mobileye Global Inc.",
  "ticker": "MBLY",
  "filing_year": 2025,
  "section": "Item 8 — Financial Statements",
  "content_type": "table",
  "title": "Revenue by segment",
  "units": "USD millions",
  "headers": ["Segment", "2025", "2024"],
  "rows": [
    ["Mobileye", "1,613", "1,756"],
    ["Other", "41", "37"]
  ],
  "column_units": ["text", "USD millions", "USD millions"],
  "source_url": "https://www.sec.gov/Archives/..."
}
```

`title`, `units`, and `source_url` remain null or absent when the chunk has no trustworthy value. Empty cells are preserved as empty strings. Headers and values are never fabricated. A malformed table that cannot satisfy rectangular structured output is rejected from frontend normalization and logged rather than silently parsed from Markdown.

The current corpus audit found 871 of 877 table chunks frontend-safe. These six
records have no trustworthy logical headers and are therefore rejected by the
normalizer: `APTV-2025-CHUNK-000241`, `F-2025-CHUNK-000254`,
`F-2025-CHUNK-000430`, `F-2025-CHUNK-000492`, `F-2025-CHUNK-000495`, and
`NVDA-2026-CHUNK-000132`. Repair their upstream table-schema-v2 data before
exposing them; do not infer headers or reconstruct them from Markdown in either
the API or browser.

Explicitly cited chunks are preferred. Citation parsing accepts only exact IDs present in final evidence. When the model emits no resolvable citations, all final evidence chunks are returned as a documented fallback because they are the only chunks the model received; the UI describes them as retrieved evidence, not proof that every chunk supported every sentence.

## Production direction

A later deployment is expected to use:

- static hosting for the built React assets;
- Docker-compatible backend compute able to retain models and the index in memory;
- backend-only secret injection;
- versioned, immutable retrieval/index artifacts with recorded hashes;
- CORS restricted to the deployed frontend origin;
- HTTPS end to end;
- separate liveness and readiness probes;
- request and idle-stream timeouts that accommodate model loading and generation;
- cancellation propagation and graceful shutdown of active streams;
- basic structured logs with request ID, scope, detected companies, evidence IDs, stage latency, citation resolution, and safe error class.

Do not choose a provider or compute size until these are measured on the local vertical slice:

- backend startup time;
- idle and peak RAM;
- embedding-model download/cache size;
- chunk/embedding/index artifact size;
- retrieval latency;
- planner and context-selection latency;
- time to first token;
- complete response latency;
- expected concurrent request count and memory impact.

## Security and operational rules

- Never expose Azure/OpenAI credentials, gateway headers, system prompts, or provider errors to frontend code.
- Never commit `.env` files. Treat all `VITE_*` values as public.
- Reject empty queries and length-limit queries; the default local limit is 4,000 Unicode characters and is configurable through `AVA_QUERY_MAX_LENGTH`.
- Render model Markdown through a safe React renderer. Never render unsanitized model HTML or use raw `dangerouslySetInnerHTML`.
- Cancel backend/provider work when the client disconnects where reasonably possible.
- Log technical exceptions server-side with request correlation; return concise safe errors to users.
- Keep raw SEC filing files immutable.
- Restrict production CORS; do not use wildcard origins with credentials.
- Do not add authentication in this phase.

## Future deployment milestones

1. **Local vertical slice:** real LLM planning, scope-aware retrieval, explicit buffered or provider-streamed answer delivery, citation resolution, structured sources, and responsive themes pass automated and visual checks. Native provider streaming remains the preferred production configuration.
2. **Containerized reproducible build:** pin backend/frontend dependencies, build without local caches, validate artifact hashes, and document measured startup/RAM/latency.
3. **Hosted preview:** deploy static frontend and right-sized backend, enable HTTPS/restricted CORS/readiness, and verify unbuffered streaming.
4. **Persistent conversations:** design storage, retention, deletion, and bounded-context policy before implementation.
5. **Authentication and accounts:** add only with an explicit identity/security design.
6. **Document uploads and corpus management:** define tenancy, validation, provenance, storage, and re-indexing first.
7. **Vector-database migration:** consider only if measured scale, filtering, durability, or concurrency makes the current in-memory artifacts insufficient.
8. **Agentic functionality:** require a concrete use case, threat model, and separate evaluation before adding tools or orchestration.
