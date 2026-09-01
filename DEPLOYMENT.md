# AVA Deployment Guide

**Status date:** 31 August 2026
**Product:** AVA — Autonomous Vehicle Analyst

This document defines the local vertical slice and the self-hosted production
reference deployment. It does not select a hosting provider.

> This document records the currently verified local deployment. The
> authoritative future architecture, priorities, Qdrant migration, image source
> contract, conversation history, and release gates are in
> [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Where this document describes
> the current 10-chunk or source-fallback behavior, it is a baseline description,
> not the target design.

## System architecture

```text
Browser
  → React + TypeScript frontend
  → FastAPI API
  → optional PostgreSQL transcript/summary store
  → shared LLM-planned scope-aware hybrid retrieval pipeline
  → Qdrant named dense vector + local BM25/custom RRF
  → optional separate tenant-filtered Qdrant conversation-memory collection
  → existing OpenAI-compatible LLM generation
```

The repository name and product identity are separate. Internal code and artifact names may retain the repository's historical name; the browser interface is branded only as **AVA**, short for **Autonomous Vehicle Analyst**.

The frontend owns presentation and current-tab transcript state. It contains no API credentials, model calls, company detection, Comparison Cue detection, subquery planning, retrieval policy, citation resolution, or raw internal chunk handling. It sends the user's original query unchanged.

FastAPI is a thin adapter around independently usable Python modules. The same shared scope-aware entry point is used by the deployed API, `notebooks/hybrid_rag_generation.ipynb`, and `src/scripts/evaluate_scope_aware_hybrid_retrieval.py` (the repository implementation of `evaluate_scope_aware_retrieval`). The notebook is not a runtime dependency. The adapter validates requests, invokes the shared pipeline, streams generation events, resolves citations, and maps internal chunks to frontend-safe source objects.

The stateless path remains available. The optional persistent path stores ordered
messages and summaries in PostgreSQL, sends a bounded recent-turn window plus a
rebuildable summary to the planner/generator, and can retrieve opt-in summary
memory from a separate Qdrant collection. Production uses provider-neutral OIDC
and server-bound tenant/user ownership. An explicitly acknowledged single-user
mode remains available only for trusted local deployments; browser IDs never
define tenant ownership.

### Source-of-truth reconciliation

Repository inspection before this deployment work found three related but previously separate experiments:

- `notebooks/hybrid_rag_generation.ipynb` now calls the same shared resolver,
  planner, company-balanced selector, generation-token counter, and citation
  resolver as the API; its earlier cells remain experiment history.
- `src/scripts/evaluate_scope_aware_hybrid_retrieval.py` supplies the authoritative regex aliases/tickers, Comparison Cues, scope labels, dense/BM25 retrieval, RRF, scoped merging, and evaluation output. It is the validation reference.
- `notebooks/test_reranking.ipynb` contains the current BGE cross-encoder experiment, but reranking was not yet integrated into either path.

The production adapter uses deterministic-first company resolution and validated
planner targets. Each relevant company/subquery pair gets an independently
ticker-filtered 10-candidate dense/BM25/RRF pool. Stable-ID merge preserves all
pool ranks, deterministic diversity precedes quota allocation, and the selector
uses a fixed typed policy with hard limits of 10 final chunks per company and 50
per request. One through five companies target 10 each; larger scopes divide 50
slots as evenly as possible. The complete formatted generation input is counted
with `o200k_base`, with output tokens reserved before packing. No table or
narrative source is truncated. The cross-encoder experiment remains disabled.

## Local development architecture

### Processes and ports

- Vite serves the React development build, normally at `http://localhost:5173`.
- Uvicorn serves FastAPI, normally at `http://localhost:8000`.
- Local CORS allows only the configured development origin, defaulting to `http://localhost:5173`.
- The frontend uses same-origin `/api` requests by default. Vite proxies them to
  `http://127.0.0.1:8000`; `VITE_API_BASE_URL` is needed only for an intentional
  cross-origin deployment.

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
AVA_LLM_CONTEXT_WINDOW_TOKENS=32768
AVA_LLM_RESERVED_OUTPUT_TOKENS=4096
AVA_OBSERVABILITY_RETENTION_DAYS=30
AVA_QDRANT_MODE=shadow
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION_ALIAS=ava_filing_chunks_current
QDRANT_TIMEOUT_SECONDS=30
QDRANT_API_KEY=<production secret when authentication is enabled>
AVA_CONVERSATION_MODE=single_user
AVA_SINGLE_USER_BOUNDARY_ACKNOWLEDGED=true
AVA_POSTGRES_DSN=postgresql://ava:<password>@127.0.0.1:5432/ava
AVA_TENANT_ID=local-tenant
AVA_USER_ID=local-user
AVA_SHORT_TERM_TOKEN_BUDGET=2048
AVA_SUMMARY_TOKEN_BUDGET=768
AVA_LONG_TERM_MEMORY_STORE=qdrant
AVA_LONG_TERM_TOKEN_BUDGET=512
AVA_LONG_TERM_CANDIDATE_K=5
AVA_LONG_TERM_SCORE_THRESHOLD=0.55
AVA_CONVERSATION_RETENTION_DAYS=90
AVA_OIDC_ISSUER=https://identity.example.com
AVA_OIDC_CLIENT_ID=<oidc-client-id>
AVA_OIDC_CLIENT_SECRET=<backend-only-secret-if-required>
AVA_OIDC_REDIRECT_URI=https://ava.example.com/api/auth/callback
AVA_OIDC_TENANT_CLAIM=tenant_id
AVA_AUTH_COOKIE_SECURE=true
AVA_AUTH_COOKIE_SAME_SITE=lax
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

The context window and reserved output values form the token-packing contract;
the generation request uses the same reserved output limit. Company-scoped
evidence has fixed hard limits of 50 chunks per request and 10 per company.
One through five requested companies target 10 each; larger scopes divide the
50 slots as evenly as possible. Token pressure retains balanced partial evidence
and records the unmet target in backend diagnostics.

Every real request emits one access-controlled structured completion record and
returns an opaque `X-Request-ID` response header for correlation. The external
log sink must enforce `AVA_OBSERVABILITY_RETENTION_DAYS` independently from any
conversation retention. Operational logs are not the PostgreSQL transcript and
do not replace it. Provider token usage is recorded when the
configured gateway supplies it; the verified local gateway currently omits it.
See [OBSERVABILITY.md](OBSERVABILITY.md).

Mock mode is explicit:

```dotenv
AVA_PIPELINE_MODE=mock
```

Mock mode does not load embeddings, BGE, BM25, or an LLM client. It emits deterministic real-time test events for normal, pre-token-failure, and mid-stream-failure cases. It is for frontend development and automated tests only. The health response exposes the active mode so mock readiness cannot be mistaken for the production pipeline.

### Local Qdrant service and migration

The pinned versions are Qdrant server `v1.18.2` and Python client `1.19.0`.
The Docker service binds REST and gRPC only to loopback and persists storage in
the `ava_qdrant_data` volume. Both local launch paths disable Qdrant telemetry:

```bash
docker compose -f docker-compose.qdrant.yml up -d
curl --fail http://127.0.0.1:6333/healthz
```

If Docker is unavailable, download the matching Qdrant binary and start it with
the repository wrapper. The wrapper binds to loopback and stores data and
snapshots under the ignored `data/indexes/qdrant_server/` directory:

```bash
QDRANT_BINARY=/absolute/path/to/qdrant scripts/run_qdrant_local.sh
```

Before every import, validate the frozen JSONL, NPZ, and manifest artifacts:

```bash
.venv/bin/python -m src.embeddings.audit_embeddings --strict
```

The importer derives a content-addressed physical collection name, validates
embedding input hashes and normalization, idempotently upserts deterministic
point UUIDs, verifies every returned point ID/payload/vector, performs a filtered
exact-search smoke test, creates a snapshot, and switches the stable alias only
after all audits pass:

```bash
.venv/bin/python -m src.indexing.qdrant_index build \
  --url http://127.0.0.1:6333 \
  --activate --snapshot --batch-size 128 --parallel 4
```

The import manifest is written to `data/indexes/qdrant/`. Re-running `build`
recovers a partial import through idempotent upserts. Check the deployed state
and rerun the strict audit independently with:

```bash
.venv/bin/python -m src.indexing.qdrant_index status
.venv/bin/python -m src.indexing.qdrant_index audit \
  --collection ava_filing_chunks_current
.venv/bin/python -m src.evaluation.evaluate_qdrant_parity
```

Restore a snapshot into a new physical collection, audit it, then test an atomic
cutover and rollback while the API is stopped:

```bash
.venv/bin/python -m src.indexing.qdrant_index restore \
  --snapshot-location http://127.0.0.1:6333/collections/SOURCE/snapshots/SNAPSHOT \
  --restore-collection RESTORED_COLLECTION
.venv/bin/python -m src.indexing.qdrant_index activate \
  --collection RESTORED_COLLECTION
.venv/bin/python -m src.indexing.qdrant_index rollback \
  --collection PREVIOUS_COLLECTION
```

Use `AVA_QDRANT_MODE=shadow` first. It returns local NPZ dense results while
recording Qdrant candidate IDs, top-10 order, candidate overlap, and latency.
The accepted dense tolerance is exact top-10 order plus at least 98% overlap in
the 50-candidate pool. Final hybrid evidence order must remain exact. Promote to
`primary` only after the shadow soak is accepted. Qdrant-native sparse vectors
remain deferred; local `bm25s` and custom RRF are still authoritative.

For a non-local deployment, enable Qdrant authentication and TLS, set
`QDRANT_API_KEY`, use an HTTPS `QDRANT_URL`, keep ports private, and supply the
secret only to the backend. Never expose it through a `VITE_*` variable.

### Conversation persistence and memory

Start the pinned loopback PostgreSQL service with a non-committed password:

```bash
AVA_POSTGRES_PASSWORD=<local-password> \
  docker compose -f docker-compose.postgres.yml up -d
```

`AVA_CONVERSATION_MODE=disabled` is the explicit stateless deployment. To use
persistence, set `single_user`, provide the PostgreSQL DSN and server-owned
tenant/user IDs, and explicitly acknowledge that boundary. This is suitable for
one trusted user only; it is not authentication and must not be deployed as a
multi-user service.

For multi-user deployment, set `AVA_CONVERSATION_MODE=oidc`. AVA uses the OIDC
authorization-code flow with PKCE and nonce, validates a fixed signing algorithm,
issuer, audience, expiry, and the configured tenant claim, and stores only hashed
opaque sessions in PostgreSQL. OAuth tokens never enter browser storage. Session
cookies are HttpOnly and Secure by default; state-changing requests additionally
require the double-submit CSRF value. Register the redirect URI exactly and keep
the client secret backend-only. Use `AVA_AUTH_COOKIE_SECURE=false` only for local
HTTP development.

PostgreSQL migrations run idempotently at startup. It remains authoritative for
conversation/message order, summaries, source-use records, and deletion audit.
Each chat request carries an opaque conversation UUID and a new client-turn UUID;
retries with the same pair replay a completed answer or resume a failed turn
without inserting another user message.

Short-term context defaults to 2,048 tokens and the rebuildable older-message
summary to 768 tokens. The exact formatted conversation context is included in
the existing generation-input count, so history cannot silently overflow or
steal later companies' evidence after packing.

`AVA_LONG_TERM_MEMORY_STORE=qdrant` enables the separate
`ava_conversation_memory_v1` collection. Individual conversations remain
memory-off until the user enables them. Every search/delete uses server-owned
tenant and user filters; summaries are never represented as SEC filing evidence
or source cards. Disabling memory removes that conversation's derived point.
Delete-one and delete-all remove derived points before canonical rows, and the
relational deletion audit deliberately retains no transcript content.

Run the live database contract when a PostgreSQL test instance is available:

```bash
AVA_TEST_POSTGRES_DSN=postgresql://... \
  .venv/bin/python -m pytest -q tests/test_postgres_conversations.py
```

### Retention, backups, and restore drills

Conversation retention is 90 days by default. Schedule the dry run first and
review its JSON count before applying the same configuration:

```bash
.venv/bin/python -m src.conversations.maintenance
.venv/bin/python -m src.conversations.maintenance --apply --batch-size 100
```

The apply job deletes eligible derived Qdrant memory, then conditionally deletes
the still-expired PostgreSQL conversation and its cascaded messages, summaries,
source uses, and feedback. It records a content-free `retention` audit row and
also purges expired login transactions and sessions in OIDC mode. Retain deletion
audit rows for 365 days unless legal/privacy policy requires a shorter period;
they contain identifiers and timestamps but no transcript text.

Take daily state backups and retain 7 daily, 4 weekly, and 12 monthly copies in
encrypted access-controlled storage. The command creates a custom-format
PostgreSQL dump, snapshots both the filing collection and conversation-memory
collection when present, downloads those snapshots, and writes SHA-256 checksums:

```bash
AVA_POSTGRES_DSN=postgresql://... QDRANT_URL=https://... \
  .venv/bin/python -m src.operations.state_backup backup backups/2026-08-31
.venv/bin/python -m src.operations.state_backup verify backups/2026-08-31
```

Run a quarterly restore drill in isolated targets. The PostgreSQL target must be
a separate pre-created database whose name ends in `_restore`, `_restore_test`,
or `_drill`; the Qdrant prefix must begin with `ava_restore_`. Both commands are
fail-safe unless `--apply` is supplied:

```bash
AVA_POSTGRES_DSN=postgresql://... \
  .venv/bin/python -m src.operations.state_backup restore-postgres-drill \
  backups/2026-08-31 --restore-dsn postgresql://.../ava_restore_test --apply

QDRANT_URL=https://... \
  .venv/bin/python -m src.operations.state_backup restore-qdrant-drill \
  backups/2026-08-31 --target-prefix ava_restore_20260831_ --apply
```

Verify migrations, row counts, owner isolation, transcript replay, Qdrant point
counts, filtered memory search, and deletion against the restored targets before
recording the drill as passed. Qdrant collection snapshots do not restore aliases;
never repoint the production alias during a drill. Test restores with the same or
next Qdrant minor version, then remove drill targets through the platform's normal
approved cleanup process.

### Startup and readiness

In real mode, application startup loads and validates the eleven chunk files and aligned BGE-base NPZ artifacts, normalizes the embedding matrix, builds the in-memory BM25 index, then loads the BGE query embedder. It does not rebuild or upload Qdrant. Loading occurs once per API process, not once per request. Startup fails closed when artifact counts or required configuration are invalid. When Qdrant shadow or primary mode is configured, the alias and exact point count are also required; an unavailable or incomplete Qdrant leaves the real pipeline alive but not ready and never falls back to mock output.

`GET /api/health` distinguishes liveness from readiness:

```json
{
  "status": "ok",
  "mode": "real",
  "pipeline_ready": true,
  "answer_delivery": "buffered",
  "qdrant": {
    "configured": true,
    "mode": "shadow",
    "status": "ok",
    "alias_target": "ava_filing_chunks_<artifact-version>",
    "point_count": 4526
  }
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

1. Stateless mode submits `{ "query": "..." }`. Persistent mode additionally
   submits the server-issued `conversation_id` and a fresh UUID
   `client_turn_id`; the original current query remains unchanged.
2. FastAPI validates the request and atomically starts or replays the owned turn.
   It loads only a token-bounded recent window, a versioned rolling summary, and
   any opted-in thresholded memory—not the full transcript.
3. The backend LLM planner receives that context only for follow-up/pronoun and
   topic-switch resolution, then returns the same validated atomic retrieval
   plan; it does not answer the question.
4. For every subquery, shared regex logic detects supported company names, tickers, and aliases, while Comparison Cue logic determines single-company, explicit subset, anchored-global, enumeration, or global scope. A single-company scope detected in the original query is inherited by its subqueries.
5. Every subquery runs normalized BGE dense search and BM25 search; reciprocal-rank fusion combines them and retains up to 10 candidates.
6. Candidates from all subqueries are merged and deduplicated by stable internal chunk ID while preserving each subquery match and rank.
7. Coverage selection takes at least two available chunks per subquery in rounds. Remaining slots are filled by best RRF score plus the notebook's `0.01` multi-subquery bonus.
8. At most 10 unique selected chunks are formatted with internal source IDs and passed to the grounded generation prompt with the original query.
9. With `AVA_LLM_STREAMING=true`, the provider's real streaming iterator yields non-empty text fragments and FastAPI emits each immediately. With `false`, the provider returns one completed answer and FastAPI emits that answer as one `delta` event.
10. Citation IDs found in the generated answer are resolved only against the selected generation evidence.
11. The adapter emits normalized user-facing source objects in one `sources`
    event. The current implementation returns all final generation evidence when
    no valid citation resolves. The API returns an empty source list in that case,
    so candidates and uncited context are never presented as answer support.
12. Before `done`, the completed answer, frontend-safe source event, and exact
    used-source IDs are committed to PostgreSQL. One `done` event terminates a
    successful stream; interruption marks the turn retryable.

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

The current corpus audit found 959 of 965 table chunks frontend-safe. These six
records have no trustworthy logical headers and are therefore rejected by the
normalizer: `APTV-2025-CHUNK-000241`, `F-2025-CHUNK-000254`,
`F-2025-CHUNK-000430`, `F-2025-CHUNK-000492`, `F-2025-CHUNK-000495`, and
`NVDA-2026-CHUNK-000132`. Repair their upstream table-schema-v2 data before
exposing them; do not infer headers or reconstruct them from Markdown in either
the API or browser.

Citation parsing accepts only exact IDs present in final evidence. A response
with no resolved final-evidence citation returns no source cards; candidates and
uncited context are never exposed as answer support.

## Production deployment

`docker-compose.production.yml` is the self-hosted reference topology. Every base
image is pinned by version and multi-architecture digest. The frontend is a
non-root Nginx process that proxies `/api` without SSE buffering. The non-root API
runs one Uvicorn worker because each worker owns the BGE model, normalized dense
matrix, and BM25 index. PostgreSQL and Qdrant are private on an internal network;
only the frontend binds a loopback port for an external TLS proxy.

Before startup, load and audit the immutable Qdrant filing collection, provide
the checked local chunk/embedding artifacts, configure an OIDC client with the
exact callback URI, and inject secrets from the host/orchestrator secret manager.
For the compose reference, all `${...}` values must be supplied without checking
them into a file. Never place secrets in `VITE_*` build arguments.

Validate resolved configuration without printing it, then build and start:

```bash
docker compose -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.production.yml build --pull
docker compose -f docker-compose.production.yml up -d
curl --fail http://127.0.0.1:${AVA_HTTP_PORT:-8080}/api/live
curl --fail http://127.0.0.1:${AVA_HTTP_PORT:-8080}/api/ready
```

Terminate TLS at the deployment edge, forward only to the loopback frontend,
preserve streaming responses, and set an idle timeout above
`AVA_STREAM_TIMEOUT_SECONDS`. Do not publish API, PostgreSQL, or Qdrant ports.
The app emits JSON logs when `AVA_JSON_LOGS=true`; logs and request traces must be
access-controlled and retained separately from conversations.

Measure these values before choosing compute size or increasing worker count:

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
- Follow the controls and incident process in `SECURITY.md`.

## Production verification

CI builds both images, scans them for unfixed high/critical vulnerabilities, scans
the browser bundle for backend secret names, runs live PostgreSQL/Qdrant tenancy,
session, retention, and migration contracts, and sends an SSE request through the
production Nginx configuration. The CI proxy load probe records mock transport
p50/p95 only; it must not be presented as provider latency.

For a provider-backed deployment, create a disposable conversation if OIDC mode
is enabled, supply its IDs and session/CSRF values through environment variables,
and run:

```bash
AVA_LOAD_SESSION_COOKIE=<opaque-cookie> \
AVA_LOAD_CSRF_TOKEN=<csrf-cookie> \
.venv/bin/python scripts/load_sse.py \
  --origin https://ava.example.com \
  --conversation-id <disposable-conversation-uuid> \
  --requests 20 --concurrency 2 --label provider-production-like
```

Record p50/p95 time to first token, complete latency, error count, startup time,
and peak RAM in the release record. Delete the disposable conversation, perform
the documented backup restore drill, and test sign-in, logout, CSRF rejection,
tenant isolation, history resume, feedback, deletion, and mobile keyboard/focus
behavior before promotion.
