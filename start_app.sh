#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

POSTGRES_COMPOSE="docker-compose.postgres.yml"
QDRANT_COMPOSE="docker-compose.qdrant.yml"
POSTGRES_CONTAINER="edgar-insight-rag-postgres-1"
QDRANT_CONTAINER="ava-qdrant"

export AVA_POSTGRES_PASSWORD="${AVA_POSTGRES_PASSWORD:-local-password}"
export AVA_POSTGRES_DSN="${AVA_POSTGRES_DSN:-postgresql://ava:${AVA_POSTGRES_PASSWORD}@127.0.0.1:5432/ava}"
export AVA_PIPELINE_MODE="real"
export AVA_REQUEST_ROUTING_ENABLED="${AVA_REQUEST_ROUTING_ENABLED:-true}"
export AVA_STRICT_ABSTENTION_PROMPT="${AVA_STRICT_ABSTENTION_PROMPT:-false}"
export AVA_CALCULATOR_ENABLED="false"
export AVA_MAX_TOOL_EXECUTIONS="${AVA_MAX_TOOL_EXECUTIONS:-4}"
export AVA_MAX_WEB_SEARCHES="${AVA_MAX_WEB_SEARCHES:-2}"
export AVA_CORS_ORIGINS="${AVA_CORS_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173}"
export AVA_QDRANT_MODE="primary"
export QDRANT_URL="http://127.0.0.1:6333"
export QDRANT_COLLECTION_ALIAS="${QDRANT_COLLECTION_ALIAS:-ava_filing_chunks_current}"
export AVA_CONVERSATION_MODE="single_user"
export AVA_SINGLE_USER_BOUNDARY_ACKNOWLEDGED="true"
export AVA_TENANT_ID="${AVA_TENANT_ID:-local-test}"
export AVA_USER_ID="${AVA_USER_ID:-local-test}"
export AVA_LONG_TERM_MEMORY_STORE="qdrant"
export AVA_UPLOADS_ENABLED="true"
export AVA_UPLOAD_STORE_PATH="${AVA_UPLOAD_STORE_PATH:-$PROJECT_ROOT/data/private/uploads}"

API_PID=""
FRONTEND_PID=""
POSTGRES_STARTED_BY_SCRIPT="false"
QDRANT_STARTED_BY_SCRIPT="false"

log() {
  printf '[AVA] %s\n' "$*"
}

fail() {
  printf '[AVA] ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  trap - EXIT INT TERM
  log "Stopping application processes..."
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID"
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID"
    wait "$API_PID" 2>/dev/null || true
  fi
  if [[ "$QDRANT_STARTED_BY_SCRIPT" == "true" ]]; then
    docker stop "$QDRANT_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [[ "$POSTGRES_STARTED_BY_SCRIPT" == "true" ]]; then
    docker compose -f "$POSTGRES_COMPOSE" stop postgres >/dev/null 2>&1 || true
  fi
  log "AVA stopped. Service state from before startup and all data volumes were preserved."
}

trap cleanup EXIT
trap 'exit 130' INT TERM

[[ -f .env ]] || fail "Missing .env with the configured LLM provider values."
[[ -x .venv/bin/python ]] || fail "Missing Python environment. Run: python3.12 -m venv .venv"
[[ -x .venv/bin/uvicorn ]] || fail "Backend dependencies are missing. Run: .venv/bin/pip install -r requirements-dev.txt"
[[ -d src/frontend/node_modules ]] || fail "Frontend dependencies are missing. Run: cd src/frontend && npm ci"
command -v docker >/dev/null || fail "Docker is not installed."
command -v curl >/dev/null || fail "curl is not installed."
docker info >/dev/null 2>&1 || fail "Docker is unavailable to the current user."

for port in 5173 8000; do
  if ss -ltn "sport = :$port" | tail -n +2 | grep -q .; then
    fail "Port $port is already in use. Stop the existing process and retry."
  fi
done

log "Starting PostgreSQL..."
if [[ "$(docker inspect --format '{{.State.Running}}' "$POSTGRES_CONTAINER" 2>/dev/null || true)" != "true" ]]; then
  POSTGRES_STARTED_BY_SCRIPT="true"
fi
docker compose -f "$POSTGRES_COMPOSE" up -d postgres

log "Starting Qdrant..."
if [[ "$(docker inspect --format '{{.State.Running}}' "$QDRANT_CONTAINER" 2>/dev/null || true)" != "true" ]]; then
  QDRANT_STARTED_BY_SCRIPT="true"
fi
if docker container inspect "$QDRANT_CONTAINER" >/dev/null 2>&1; then
  docker start "$QDRANT_CONTAINER" >/dev/null
else
  docker compose -f "$QDRANT_COMPOSE" up -d qdrant
fi

log "Waiting for PostgreSQL and Qdrant..."
services_ready=false
for _ in $(seq 1 60); do
  if docker exec "$POSTGRES_CONTAINER" pg_isready -U ava -d ava >/dev/null 2>&1 \
    && curl --max-time 2 --fail --silent http://127.0.0.1:6333/healthz >/dev/null; then
    services_ready=true
    break
  fi
  sleep 2
done
[[ "$services_ready" == "true" ]] || fail "PostgreSQL or Qdrant did not become ready."

log "Checking the active filing vector index..."
if ! .venv/bin/python -m src.indexing.qdrant_index status \
  --url "$QDRANT_URL" \
  | .venv/bin/python -c \
    'import json, sys; value=json.load(sys.stdin); raise SystemExit(0 if value.get("collection_exists") and value.get("alias_target") else 1)'; then
  log "Building, auditing, snapshotting, and activating the filing index..."
  .venv/bin/python -m src.indexing.qdrant_index build \
    --url "$QDRANT_URL" \
    --activate \
    --snapshot \
    --batch-size 128 \
    --parallel 4
fi

log "Starting the real AVA API with short- and long-term memory support..."
.venv/bin/uvicorn src.backend.app:app --host 127.0.0.1 --port 8000 &
API_PID=$!

api_ready=false
for _ in $(seq 1 90); do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    wait "$API_PID" || true
    fail "The API stopped during startup. Review the error above."
  fi
  if curl --max-time 2 --fail --silent http://127.0.0.1:8000/api/ready >/dev/null; then
    api_ready=true
    break
  fi
  sleep 2
done
[[ "$api_ready" == "true" ]] || fail "The API did not become ready within three minutes."

log "Starting the AVA frontend..."
npm --prefix src/frontend run dev -- --host 127.0.0.1 &
FRONTEND_PID=$!

frontend_ready=false
for _ in $(seq 1 30); do
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    wait "$FRONTEND_PID" || true
    fail "The frontend stopped during startup. Review the error above."
  fi
  if curl --max-time 2 --fail --silent http://127.0.0.1:5173/ >/dev/null; then
    frontend_ready=true
    break
  fi
  sleep 1
done
[[ "$frontend_ready" == "true" ]] || fail "The frontend did not become ready."

log "AVA is ready at http://localhost:5173"
log "Short-term history is active. Long-term memory is available per conversation."
log "Press Ctrl+C to stop the complete stack."

wait -n "$API_PID" "$FRONTEND_PID"
