"""FastAPI streaming adapter for AVA."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .pipeline import PipelineEvent, PipelineSettings, build_pipeline
from src.conversations.context import ConversationContextBuilder
from src.conversations.memory import NullMemoryStore, QdrantMemoryStore
from src.conversations.repository import (
    ConversationNotFoundError,
    PostgresConversationRepository,
    TurnConflictError,
)
from src.conversations.service import ConversationService, ConversationSettings
from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.indexing.qdrant_index import make_client


LOGGER = logging.getLogger("ava.api")
SAFE_SERVICE_ERROR = (
    "The filing-analysis service is temporarily unavailable. Please retry shortly."
)
SAFE_VALIDATION_ERROR = (
    "The filing search could not be prepared because of a temporary service issue. "
    "Please retry shortly."
)


def safe_stream_error(error: Exception) -> str:
    """Map internal failures to actionable browser-safe error states."""
    if isinstance(error, ValueError):
        return SAFE_VALIDATION_ERROR
    return SAFE_SERVICE_ERROR


class ChatRequest(BaseModel):
    query: str
    conversation_id: UUID | None = None
    client_turn_id: UUID | None = None


class CreateConversationRequest(BaseModel):
    title: str = "New conversation"
    memory_enabled: bool = False


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    memory_enabled: bool | None = None


def encode_sse(event: PipelineEvent) -> bytes:
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n".encode("utf-8")


def _conversation_payload(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "title": value.title,
        "memory_enabled": value.memory_enabled,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
    }


def _message_payload(value: Any) -> dict[str, Any]:
    payload = {
        "id": value.id,
        "client_turn_id": value.client_turn_id,
        "role": value.role,
        "text": value.content,
        "status": value.status,
        "ordinal": value.ordinal,
        "created_at": value.created_at.isoformat(),
    }
    if value.role == "assistant":
        payload["source_event"] = value.metadata.get("source_event", {
            "sources": [],
            "source_status": "none_cited",
            "malformed_source_count": 0,
        })
    return payload


def _build_conversation_service(
    settings: ConversationSettings,
    pipeline_settings: PipelineSettings,
    active_pipeline: Any,
) -> ConversationService | None:
    if settings.mode == "disabled":
        return None
    repository = PostgresConversationRepository(settings.postgres_dsn or "")
    context_builder = ConversationContextBuilder(
        repository,
        recent_token_budget=settings.recent_token_budget,
        summary_token_budget=settings.summary_token_budget,
    )
    memory_store: Any = NullMemoryStore()
    if settings.long_term_store == "qdrant":
        model = getattr(getattr(active_pipeline, "retriever", None), "model", None)
        if model is None:
            raise RuntimeError("Qdrant memory requires the real BGE retrieval model.")
        local_path = (
            Path(pipeline_settings.qdrant_local_path).expanduser().resolve()
            if pipeline_settings.qdrant_local_path
            else None
        )
        client = make_client(
            url=None if local_path else pipeline_settings.qdrant_url,
            api_key=pipeline_settings.qdrant_api_key,
            local_path=local_path,
            timeout=pipeline_settings.qdrant_timeout_seconds,
        )
        memory_store = QdrantMemoryStore(
            client,
            model,
            query_prefix=MODEL_CONFIGS["bgebase"]["query_prefix"],
        )
    return ConversationService(
        repository,
        tenant_id=settings.tenant_id or "",
        user_id=settings.user_id or "",
        context_builder=context_builder,
        memory_store=memory_store,
        long_term_candidate_k=settings.long_term_candidate_k,
        long_term_score_threshold=settings.long_term_score_threshold,
        long_term_token_budget=settings.long_term_token_budget,
    )


def create_app(
    *,
    pipeline: Any | None = None,
    conversation_service: ConversationService | None = None,
) -> FastAPI:
    load_dotenv()
    settings = PipelineSettings.from_environment()
    conversation_settings = ConversationSettings.from_environment()
    query_max_length = int(os.getenv("AVA_QUERY_MAX_LENGTH", "4000"))
    if query_max_length < 1:
        raise ValueError("AVA_QUERY_MAX_LENGTH must be positive.")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.pipeline = pipeline or build_pipeline(settings)
        application.state.conversations = conversation_service or _build_conversation_service(
            conversation_settings, settings, application.state.pipeline
        )
        yield

    application = FastAPI(
        title="AVA API",
        version="0.1.0",
        lifespan=lifespan,
    )
    origins = [
        origin.strip()
        for origin in os.getenv("AVA_CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Request-ID"],
    )

    @application.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        active_pipeline = getattr(request.app.state, "pipeline", None)
        response = {
            "status": "ok",
            "mode": getattr(active_pipeline, "mode", settings.mode),
            "pipeline_ready": bool(getattr(active_pipeline, "ready", False)),
            "answer_delivery": getattr(active_pipeline, "answer_delivery", "unknown"),
        }
        qdrant_health = getattr(active_pipeline, "qdrant_health", None)
        if qdrant_health is not None:
            response["qdrant"] = qdrant_health
        response["conversation_history"] = {
            "enabled": getattr(request.app.state, "conversations", None) is not None,
            "deployment_boundary": (
                "single_user" if getattr(request.app.state, "conversations", None) is not None else "stateless"
            ),
            "long_term_store": conversation_settings.long_term_store,
        }
        return response

    def conversation_service_for(request: Request) -> ConversationService:
        service = getattr(request.app.state, "conversations", None)
        if service is None:
            raise HTTPException(status_code=503, detail="Conversation history is not enabled.")
        return service

    @application.post("/api/conversations", status_code=201)
    async def create_conversation(body: CreateConversationRequest, request: Request) -> dict[str, Any]:
        service = conversation_service_for(request)
        try:
            value = await asyncio.to_thread(
                service.create, title=body.title, memory_enabled=body.memory_enabled
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _conversation_payload(value)

    @application.get("/api/conversations")
    async def list_conversations(
        request: Request,
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        values = await asyncio.to_thread(conversation_service_for(request).list)
        if cursor:
            positions = [index for index, item in enumerate(values) if item.id == cursor]
            if not positions:
                raise HTTPException(status_code=422, detail="Invalid conversation cursor.")
            values = values[positions[0] + 1:]
        page = values[:limit]
        return {
            "conversations": [_conversation_payload(item) for item in page],
            "next_cursor": page[-1].id if len(values) > limit else None,
        }

    @application.get("/api/conversations/{conversation_id}/messages")
    async def list_messages(
        conversation_id: UUID,
        request: Request,
        limit: int = Query(default=100, ge=1, le=200),
        before: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        try:
            values = await asyncio.to_thread(
                conversation_service_for(request).messages, str(conversation_id)
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        if before is not None:
            values = [item for item in values if item.ordinal < before]
        values = values[-limit:]
        return {
            "messages": [_message_payload(item) for item in values],
            "next_before": values[0].ordinal if values and values[0].ordinal > 1 else None,
        }

    @application.patch("/api/conversations/{conversation_id}")
    async def update_conversation(
        conversation_id: UUID, body: UpdateConversationRequest, request: Request
    ) -> dict[str, Any]:
        if body.title is None and body.memory_enabled is None:
            raise HTTPException(status_code=422, detail="No conversation change was supplied.")
        try:
            value = await asyncio.to_thread(
                conversation_service_for(request).update,
                str(conversation_id),
                title=body.title,
                memory_enabled=body.memory_enabled,
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _conversation_payload(value)

    @application.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: UUID, request: Request) -> Response:
        try:
            await asyncio.to_thread(
                conversation_service_for(request).delete, str(conversation_id)
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        return Response(status_code=204)

    @application.delete("/api/conversations", status_code=204)
    async def delete_all_conversations(request: Request) -> Response:
        await asyncio.to_thread(conversation_service_for(request).delete_all)
        return Response(status_code=204)

    @application.post("/api/chat/stream")
    async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
        if not body.query.strip():
            raise HTTPException(status_code=422, detail="Enter a question to continue.")
        if len(body.query) > query_max_length:
            raise HTTPException(
                status_code=422,
                detail=f"Question must be {query_max_length} characters or fewer.",
            )
        active_pipeline = getattr(request.app.state, "pipeline", None)
        if active_pipeline is None or not getattr(active_pipeline, "ready", False):
            raise HTTPException(status_code=503, detail="AVA is still preparing its filing index.")

        request_id = str(uuid4())
        service = getattr(request.app.state, "conversations", None)
        turn = None
        conversation_context = None
        if service is not None:
            if not body.conversation_id or not body.client_turn_id:
                raise HTTPException(
                    status_code=422,
                    detail="conversation_id and client_turn_id are required when history is enabled.",
                )
            conversation_id = str(body.conversation_id)
            client_turn_id = str(body.client_turn_id)
            try:
                turn = await asyncio.to_thread(
                    service.begin_turn,
                    conversation_id,
                    client_turn_id,
                    body.query,
                    request_id,
                )
                if not turn.replay:
                    conversation_context = await asyncio.to_thread(
                        service.prepare_context,
                        conversation_id,
                        client_turn_id,
                        body.query,
                    )
            except ConversationNotFoundError as error:
                raise HTTPException(status_code=404, detail="Conversation was not found.") from error
            except TurnConflictError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        elif body.conversation_id or body.client_turn_id:
            raise HTTPException(status_code=503, detail="Conversation history is not enabled.")

        async def event_stream() -> AsyncIterator[bytes]:
            answer_fragments: list[str] = []
            source_event = {
                "sources": [],
                "source_status": "none_cited",
                "malformed_source_count": 0,
            }
            used_source_ids: list[str] = []
            turn_completed = False
            try:
                if turn is not None and turn.replay:
                    replay_sources = turn.assistant_message.metadata.get(
                        "source_event", source_event
                    )
                    yield encode_sse(PipelineEvent("delta", {"text": turn.assistant_message.content}))
                    yield encode_sse(PipelineEvent("sources", replay_sources))
                    yield encode_sse(PipelineEvent("done", {"replayed": True}))
                    return
                stream_arguments = {
                    "request_id": request_id,
                    "conversation_context": conversation_context,
                    "conversation_id": str(body.conversation_id) if body.conversation_id else None,
                    "turn_id": str(body.client_turn_id) if body.client_turn_id else None,
                }
                async for event in active_pipeline.stream(
                    body.query, request.is_disconnected, **stream_arguments
                ):
                    if event.event == "delta":
                        answer_fragments.append(str(event.data.get("text", "")))
                    elif event.event == "sources":
                        source_event = event.data
                        used_source_ids = list((event.internal or {}).get("used_source_ids", []))
                    elif event.event == "done" and service is not None and body.conversation_id and body.client_turn_id:
                        await asyncio.to_thread(
                            service.complete_turn,
                            str(body.conversation_id),
                            str(body.client_turn_id),
                            "".join(answer_fragments),
                            source_event,
                            used_source_ids,
                        )
                        turn_completed = True
                    yield encode_sse(event)
            except Exception as error:
                LOGGER.exception("AVA stream failed")
                yield encode_sse(
                    PipelineEvent("error", {"message": safe_stream_error(error)})
                )
            finally:
                if (
                    not turn_completed
                    and turn is not None
                    and not turn.replay
                    and service is not None
                    and body.conversation_id
                    and body.client_turn_id
                ):
                    try:
                        await asyncio.to_thread(
                            service.fail_turn,
                            str(body.conversation_id),
                            str(body.client_turn_id),
                        )
                    except Exception:
                        LOGGER.exception("AVA failed to mark the interrupted turn")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Request-ID": request_id,
            },
        )

    return application


app = create_app()
