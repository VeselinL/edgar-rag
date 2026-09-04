"""Owner-scoped conversation, message, and feedback routes."""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.backend.dependencies import RequestServices
from src.conversations.repository import ConversationNotFoundError


class CreateConversationRequest(BaseModel):
    title: str = "New conversation"
    memory_enabled: bool = False
    company_scope: list[str] = []


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    memory_enabled: bool | None = None
    pinned: bool | None = None
    company_scope: list[str] | None = None


class FeedbackRequest(BaseModel):
    value: Literal["helpful", "not_helpful"]
    comment: str | None = None


def conversation_payload(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "title": value.title,
        "memory_enabled": value.memory_enabled,
        "pinned": value.pinned,
        "pinned_at": value.pinned_at.isoformat() if value.pinned_at else None,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "company_scope": list(value.company_scope),
    }


def message_payload(value: Any) -> dict[str, Any]:
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
        payload["source_event"] = value.metadata.get(
            "source_event",
            value.metadata if "sources" in value.metadata else {
                "sources": [],
                "source_status": "none_cited",
                "malformed_source_count": 0,
            },
        )
    return payload


def create_router(services: RequestServices) -> APIRouter:
    router = APIRouter()

    @router.post("/api/conversations", status_code=201)
    async def create_conversation(body: CreateConversationRequest, request: Request) -> dict[str, Any]:
        service = await services.conversation_service_for(request, require_csrf=True)
        try:
            value = await asyncio.to_thread(
                service.create, title=body.title, memory_enabled=body.memory_enabled,
                company_scope=body.company_scope,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return conversation_payload(value)

    @router.get("/api/conversations")
    async def list_conversations(
        request: Request,
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        service = await services.conversation_service_for(request)
        values = await asyncio.to_thread(service.list)
        if cursor:
            positions = [index for index, item in enumerate(values) if item.id == cursor]
            if not positions:
                raise HTTPException(status_code=422, detail="Invalid conversation cursor.")
            values = values[positions[0] + 1:]
        page = values[:limit]
        return {
            "conversations": [conversation_payload(item) for item in page],
            "next_cursor": page[-1].id if len(values) > limit else None,
        }

    @router.get("/api/conversations/export")
    async def export_conversations(request: Request) -> JSONResponse:
        """Export only the authenticated owner's browser-safe conversation data."""
        service = await services.conversation_service_for(request)
        conversations = await asyncio.to_thread(service.list)
        exported = []
        for conversation in conversations:
            messages = await asyncio.to_thread(service.messages, conversation.id)
            exported.append({
                **conversation_payload(conversation),
                "messages": [message_payload(message) for message in messages],
            })
        return JSONResponse(
            {"schema_version": 1, "conversations": exported},
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="ava-conversations.json"',
            },
        )

    @router.get("/api/conversations/{conversation_id}/messages")
    async def list_messages(
        conversation_id: UUID,
        request: Request,
        limit: int = Query(default=100, ge=1, le=200),
        before: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        try:
            values = await asyncio.to_thread(
                (await services.conversation_service_for(request)).messages, str(conversation_id)
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        if before is not None:
            values = [item for item in values if item.ordinal < before]
        values = values[-limit:]
        return {
            "messages": [message_payload(item) for item in values],
            "next_before": values[0].ordinal if values and values[0].ordinal > 1 else None,
        }

    @router.patch("/api/conversations/{conversation_id}")
    async def update_conversation(
        conversation_id: UUID, body: UpdateConversationRequest, request: Request
    ) -> dict[str, Any]:
        if body.title is None and body.memory_enabled is None and body.pinned is None and body.company_scope is None:
            raise HTTPException(status_code=422, detail="No conversation change was supplied.")
        try:
            value = await asyncio.to_thread(
                (await services.conversation_service_for(request, require_csrf=True)).update,
                str(conversation_id),
                title=body.title,
                memory_enabled=body.memory_enabled,
                pinned=body.pinned,
                company_scope=body.company_scope,
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return conversation_payload(value)

    @router.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: UUID, request: Request) -> Response:
        try:
            await asyncio.to_thread(
                (await services.conversation_service_for(request, require_csrf=True)).delete,
                str(conversation_id),
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        return Response(status_code=204)

    @router.delete("/api/conversations", status_code=204)
    async def delete_all_conversations(request: Request) -> Response:
        service = await services.conversation_service_for(request, require_csrf=True)
        await asyncio.to_thread(service.delete_all)
        return Response(status_code=204)

    @router.post(
        "/api/conversations/{conversation_id}/messages/{message_id}/feedback",
        status_code=204,
    )
    async def submit_feedback(
        conversation_id: UUID,
        message_id: UUID,
        body: FeedbackRequest,
        request: Request,
    ) -> Response:
        if body.comment is not None and len(body.comment) > 1000:
            raise HTTPException(status_code=422, detail="Feedback comment is too long.")
        active_pipeline = getattr(request.app.state, "pipeline", None)
        generator = getattr(active_pipeline, "generator", None)
        answer_version = {
            "corpus_version": getattr(active_pipeline, "corpus_version", "unknown"),
            "index_version": getattr(active_pipeline, "index_version", "unknown"),
            "model": getattr(generator, "model", "unknown"),
            "prompt_version": getattr(generator, "prompt_version", "unknown"),
        }
        try:
            await asyncio.to_thread(
                (await services.conversation_service_for(request, require_csrf=True)).submit_feedback,
                str(conversation_id),
                str(message_id),
                body.value,
                body.comment,
                answer_version,
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Assistant response was not found.") from error
        return Response(status_code=204)

    return router
