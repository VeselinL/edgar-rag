"""Conversation-scoped uploaded-document routes."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from src.backend.dependencies import RequestServices
from src.conversations.repository import ConversationNotFoundError
from src.documents import (
    DocumentExtractionError,
    DocumentNotFoundError,
    DocumentQuotaError,
    DuplicateDocumentError,
)


def document_payload(value: Any) -> dict[str, Any]:
    return {
        "id": value.id,
        "conversation_id": value.conversation_id,
        "filename": value.filename,
        "media_type": value.media_type,
        "size_bytes": value.size_bytes,
        "status": value.status,
        "page_count": value.page_count,
        "token_count": value.token_count,
        "chunk_count": value.chunk_count,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
    }


def create_router(services: RequestServices) -> APIRouter:
    router = APIRouter()

    @router.post("/api/conversations/{conversation_id}/documents", status_code=201)
    async def upload_document(
        conversation_id: UUID,
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
    ) -> dict[str, Any]:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if media_type not in {"application/pdf", "text/plain"}:
            raise HTTPException(
                status_code=415,
                detail="Only application/pdf and text/plain uploads are supported.",
            )
        content = await request.body()
        try:
            value = await asyncio.to_thread(
                (await services.document_service_for(request, require_csrf=True)).upload,
                str(conversation_id),
                filename,
                media_type,
                content,
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        except DuplicateDocumentError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (DocumentExtractionError, DocumentQuotaError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return document_payload(value)

    @router.get("/api/conversations/{conversation_id}/documents")
    async def list_documents(
        conversation_id: UUID, request: Request
    ) -> dict[str, Any]:
        try:
            values = await asyncio.to_thread(
                (await services.document_service_for(request)).list,
                str(conversation_id),
            )
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found.") from error
        return {"documents": [document_payload(value) for value in values]}

    @router.delete(
        "/api/conversations/{conversation_id}/documents/{document_id}",
        status_code=204,
    )
    async def delete_document(
        conversation_id: UUID, document_id: UUID, request: Request
    ) -> Response:
        try:
            await asyncio.to_thread(
                (await services.document_service_for(request, require_csrf=True)).delete,
                str(conversation_id),
                str(document_id),
            )
        except (ConversationNotFoundError, DocumentNotFoundError) as error:
            raise HTTPException(status_code=404, detail="Uploaded document was not found.") from error
        return Response(status_code=204)

    return router
