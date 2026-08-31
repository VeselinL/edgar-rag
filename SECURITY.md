# AVA Security and Privacy Model

## Scope and trust boundaries

AVA has four trust boundaries: the browser and OIDC provider; the API and model
provider; PostgreSQL; and Qdrant. PostgreSQL is authoritative for identity-linked
conversation state. Qdrant filing chunks and opt-in memory are derived indexes.
SEC filing text, conversation text, recalled memory, model output, URLs, and all
browser input are untrusted data.

The production compose topology exposes only the frontend proxy on loopback.
PostgreSQL and Qdrant use an internal network, and the API is reachable only from
the proxy. A deployment edge must provide HTTPS, restrict administrative access,
and inject secrets from its secret manager. The checked-in compose file is a
self-hosted reference, not a public-Internet TLS terminator.

## Threats and controls

| Threat | Primary controls | Residual risk / operator action |
| --- | --- | --- |
| Prompt injection in filings or memory | System prompt labels both as untrusted evidence/data; tools are absent; citations resolve only against final evidence IDs. | Models can still follow malicious text. Keep generation evaluation and review anomalous output. |
| Cross-tenant conversation or memory access | OIDC-derived tenant/user identity; owner predicates on every relational operation; mandatory indexed Qdrant tenant/user filters; isolation tests. | Tenant claim configuration is security-critical. Use a stable, provider-issued claim and test it before rollout. |
| Session theft, login CSRF, or callback replay | Authorization code with PKCE, state, nonce, one-time transactions, strict ID-token validation, short opaque server sessions, Secure HttpOnly SameSite cookies, and CSRF header/cookie validation. | The deployment must enforce HTTPS, exact redirect URIs, key rotation, and identity-provider MFA/policy. |
| Browser XSS or unsafe model Markdown | React escaping, `react-markdown` with HTML disabled, restrictive CSP, no raw chunk IDs as labels, and fixed frontend schemas. | External filing links leave AVA; users should verify destination and filing provenance. |
| Resource exhaustion and expensive provider calls | Proxy and application rate limits, 16 KiB body limit, query limit, bounded evidence/history tokens, provider/stream timeouts, bounded retries, circuit breaker, and one measured model worker. | In-memory limits are per process. Use an edge/global limiter before horizontal scaling. |
| SSE buffering, interruption, or duplicate billing/state | Proxy buffering disabled, real SSE validation, disconnect checks, bounded streams, client-turn idempotency, and safe partial/retry states. The provider SDK reuses one idempotency key across its automatic retries. | A disconnect cannot guarantee that an upstream provider stopped billing. Monitor request IDs and provider usage. |
| Secret or internal-detail disclosure | Backend-only credentials, ignored `.env`, production bundle secret scan, safe browser errors, structured error classes, and no prompt/score/stack-trace response fields. | Logs contain questions and evidence identifiers. Restrict access and enforce the configured log retention. |
| Supply-chain compromise | Exact Python/npm versions, immutable base-image digests, pinned GitHub Action commits, non-root containers, and blocking high/critical image scans. | Rotate pins deliberately after review; do not blindly suppress scanner findings. |
| Incomplete deletion or recovery | Derived memory is removed before canonical deletion; relational cascades and content-free audits are tested; retention is dry-run-first; backups are checksummed; restores require isolated targets. | Qdrant and PostgreSQL deletion are not one distributed transaction. Retry failed jobs and reconcile audit/state before claiming deletion complete. |

## Secret rotation and incident response

Rotate the OIDC client secret, model-provider key, Qdrant API key, and PostgreSQL
credential independently. Update the secret manager, roll API/Qdrant/database
services as appropriate, revoke the previous credential, then verify liveness,
readiness, sign-in, retrieval, and one streamed response. Never place secrets in
image layers, frontend variables, command arguments, tickets, or logs.

For a suspected incident: restrict ingress, preserve access-controlled logs and
request IDs, revoke affected credentials/sessions, identify tenant scope, verify
PostgreSQL and Qdrant ownership filters, notify the deployment owner, and restore
only from a verified snapshot when integrity—not merely availability—is in doubt.

## Release-blocking checks

The release gate includes backend tests, PostgreSQL/Qdrant isolation and retention,
frontend lint/typecheck/tests/build, bundle secret scanning, production image
builds, proxied SSE smoke/load probes, and high/critical container scanning. Two
historical corpus-consistency failures remain explicitly documented in the
implementation plan and are not silently reclassified as security passes.
