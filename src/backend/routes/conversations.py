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
    company_scope: list[str] = []


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    company_scope: list[str] | None = None


class FeedbackRequest(BaseModel):
    value: Literal["helpful", "not_helpful"]
    comment: str | None = None


class MemoryRequest(BaseModel):
    content: str


class PreferencesRequest(BaseModel):
    nickname: str | None = None
    warmth: Literal["cold", "balanced", "warm"] | None = None
    enthusiasm: Literal["low", "balanced", "high"] | None = None
    emoji_use: Literal["off", "light"] | None = None
    custom_instructions: str | None = None
    language: Literal["en", "sr"] | None = None
    model: str | None = None
    theme: Literal["light", "dark", "system"] | None = None
    memory_enabled: bool | None = None


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


def memory_payload(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "content": value.content,
        "type": value.memory_type,
        "saved_by": "ava" if value.source_message_id else "user",
        "source_conversation_id": value.conversation_id,
        "source_message_id": value.source_message_id,
        "version": value.version,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
    }


def preferences_payload(value: Any) -> dict[str, Any]:
    return {
        "nickname": value.nickname,
        "warmth": value.warmth,
        "enthusiasm": value.enthusiasm,
        "emoji_use": value.emoji_use,
        "custom_instructions": value.custom_instructions,
        "language": value.language,
        "model": value.model,
        "theme": value.theme,
        "memory_enabled": value.memory_enabled,
    }


def create_router(services: RequestServices) -> APIRouter:
    router = APIRouter()

    @router.post("/api/conversations", status_code=201)
    async def create_conversation(body: CreateConversationRequest, request: Request) -> dict[str, Any]:
        service = await services.conversation_service_for(request, require_csrf=True)
        try:
            value = await asyncio.to_thread(
                service.create, title=body.title,
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
        if body.title is None and body.pinned is None and body.company_scope is None:
            raise HTTPException(status_code=422, detail="No conversation change was supplied.")
        try:
            value = await asyncio.to_thread(
                (await services.conversation_service_for(request, require_csrf=True)).update,
                str(conversation_id),
                title=body.title,
                pinned=body.pinned,
                company_scope=body.company_scope,
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return conversation_payload(value)

    @router.get("/api/memory")
    async def list_memory(request: Request) -> dict[str, Any]:
        service = await services.conversation_service_for(request)
        values = await asyncio.to_thread(service.list_memory)
        return {"memory": [memory_payload(value) for value in values]}

    @router.post("/api/memory", status_code=201)
    async def create_memory(body: MemoryRequest, request: Request) -> dict[str, Any]:
        service = await services.conversation_service_for(request, require_csrf=True)
        try:
            value = await asyncio.to_thread(service.create_memory, body.content)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return memory_payload(value)

    @router.patch("/api/memory/{memory_id}")
    async def update_memory(memory_id: UUID, body: MemoryRequest, request: Request) -> dict[str, Any]:
        service = await services.conversation_service_for(request, require_csrf=True)
        try:
            value = await asyncio.to_thread(service.update_memory, str(memory_id), body.content)
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Memory item was not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return memory_payload(value)

    @router.delete("/api/memory/{memory_id}", status_code=204)
    async def delete_memory(memory_id: UUID, request: Request) -> Response:
        service = await services.conversation_service_for(request, require_csrf=True)
        try:
            await asyncio.to_thread(service.delete_memory, str(memory_id))
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Memory item was not found.") from error
        return Response(status_code=204)

    @router.get("/api/preferences")
    async def get_preferences(request: Request) -> dict[str, Any]:
        service = await services.conversation_service_for(request)
        return preferences_payload(await asyncio.to_thread(service.preferences))

    @router.patch("/api/preferences")
    async def update_preferences(body: PreferencesRequest, request: Request) -> dict[str, Any]:
        values = body.model_dump(exclude_none=True)
        if not values:
            raise HTTPException(status_code=422, detail="No preference change was supplied.")
        from src.backend.pipeline import AVAILABLE_MODELS
        if "model" in values and values["model"] not in AVAILABLE_MODELS:
            raise HTTPException(status_code=422, detail="That model is not available.")
        service = await services.conversation_service_for(request, require_csrf=True)
        try:
            value = await asyncio.to_thread(service.update_preferences, **values)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return preferences_payload(value)

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
