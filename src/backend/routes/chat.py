"""Validated SSE chat transport and turn persistence."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.backend.dependencies import RequestServices
from src.backend.operations import OperationalSettings
from src.backend.pipeline import AVAILABLE_MODELS, PipelineEvent
from src.conversations.repository import ConversationNotFoundError, TurnConflictError
from src.documents import DocumentSettings


LOGGER = logging.getLogger("ava.api")
SAFE_SERVICE_ERROR = (
    "The filing-analysis service is temporarily unavailable. Please retry shortly."
)
SAFE_VALIDATION_ERROR = (
    "The filing search could not be prepared because of a temporary service issue. "
    "Please retry shortly."
)


class ChatRequest(BaseModel):
    query: str
    conversation_id: UUID | None = None
    client_turn_id: UUID | None = None
    model: str | None = None


def safe_stream_error(error: Exception) -> str:
    if isinstance(error, ValueError):
        return SAFE_VALIDATION_ERROR
    return SAFE_SERVICE_ERROR


def encode_sse(event: PipelineEvent) -> bytes:
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n".encode("utf-8")


def create_router(
    services: RequestServices,
    operational_settings: OperationalSettings,
    document_settings: DocumentSettings,
    query_max_length: int,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/chat/stream")
    async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
        if not body.query.strip():
            raise HTTPException(status_code=422, detail="Enter a question to continue.")
        if len(body.query) > query_max_length:
            raise HTTPException(
                status_code=422,
                detail=f"Question must be {query_max_length} characters or fewer.",
            )
        if body.model is not None and body.model not in AVAILABLE_MODELS:
            raise HTTPException(status_code=422, detail="That model is not available.")
        active_pipeline = getattr(request.app.state, "pipeline", None)
        if active_pipeline is None or not getattr(active_pipeline, "ready", False):
            raise HTTPException(status_code=503, detail="AVA is still preparing its filing index.")

        request_id = getattr(request.state, "request_id", str(uuid4()))
        service = None
        turn = None
        conversation_context = None
        active_document_service = None
        company_scope: list[str] = []
        effective_model = body.model
        history_enabled = (
            getattr(request.app.state, "conversation_service", None) is not None
            or getattr(request.app.state, "conversation_factory", None) is not None
        )
        if history_enabled:
            if not body.conversation_id or not body.client_turn_id:
                raise HTTPException(
                    status_code=422,
                    detail="conversation_id and client_turn_id are required when history is enabled.",
                )
            service = await services.conversation_service_for(request, require_csrf=True)
            if effective_model is None:
                effective_model = (await asyncio.to_thread(service.preferences)).model
            conversation_id = str(body.conversation_id)
            client_turn_id = str(body.client_turn_id)
            try:
                company_scope = list((await asyncio.to_thread(service.get, conversation_id)).company_scope)
                turn = await asyncio.to_thread(
                    service.begin_turn,
                    conversation_id,
                    client_turn_id,
                    body.query,
                    request_id,
                )
                if not turn.replay:
                    preferences = await asyncio.to_thread(service.preferences)
                    memory_query = body.query
                    translator = getattr(
                        getattr(active_pipeline, "generator", None),
                        "translate_memory_retrieval_query",
                        None,
                    )
                    if preferences.language == "sr" and callable(translator):
                        try:
                            request_generator = active_pipeline.generator.for_model(effective_model)
                            translated = await asyncio.to_thread(
                                request_generator.translate_memory_retrieval_query,
                                body.query,
                            )
                            if translated:
                                memory_query = translated
                        except Exception:
                            LOGGER.info("AVA Serbian memory retrieval translation unavailable")
                    conversation_context = await asyncio.to_thread(
                        service.prepare_context, conversation_id, client_turn_id, body.query,
                        memory_query=memory_query,
                    )
                    if document_settings.enabled:
                        active_document_service = getattr(
                            service, "document_lifecycle", None
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
                        "source_event",
                        turn.assistant_message.metadata
                        if "sources" in turn.assistant_message.metadata
                        else source_event,
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
                    "company_scope": company_scope,
                    "model": effective_model,
                }
                if active_document_service is not None:
                    stream_arguments["document_service"] = active_document_service
                async with asyncio.timeout(operational_settings.stream_timeout_seconds):
                    async for event in active_pipeline.stream(
                        body.query, request.is_disconnected, **stream_arguments
                    ):
                        if event.event not in {"status", "delta", "sources", "done", "error"}:
                            LOGGER.debug("Dropping non-contract pipeline event", extra={
                                "event": event.event,
                            })
                            continue
                        if event.event == "delta":
                            answer_fragments.append(str(event.data.get("text", "")))
                        elif event.event == "sources":
                            source_event = event.data
                            used_source_ids = list((event.internal or {}).get("used_source_ids", []))
                        elif event.event == "done" and service is not None and body.conversation_id and body.client_turn_id:
                            stored_metadata = {
                                "source_event": source_event,
                                "used_source_ids": used_source_ids,
                                "answer_version": {
                                    "corpus_version": getattr(active_pipeline, "corpus_version", "unknown"),
                                    "index_version": getattr(active_pipeline, "index_version", "unknown"),
                                    "model": effective_model or getattr(getattr(active_pipeline, "generator", None), "model", "unknown"),
                                    "prompt_version": getattr(getattr(active_pipeline, "generator", None), "prompt_version", "unknown"),
                                },
                            }
                            await asyncio.to_thread(
                                service.complete_turn,
                                str(body.conversation_id),
                                str(body.client_turn_id),
                                "".join(answer_fragments),
                                stored_metadata,
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
            },
        )

    return router
