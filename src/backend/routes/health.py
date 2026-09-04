"""Health, readiness, and safe effective-mode routes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response

from src.config.settings import PipelineSettings
from src.conversations.service import ConversationSettings
from src.documents import DocumentSettings
from src.backend.operations import OperationalSettings


LOGGER = logging.getLogger("ava.api")


def create_router(
    settings: PipelineSettings,
    conversation_settings: ConversationSettings,
    document_settings: DocumentSettings,
    operational_settings: OperationalSettings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/ready")
    async def readiness(request: Request) -> Response:
        active_pipeline = getattr(request.app.state, "pipeline", None)
        ready = bool(getattr(active_pipeline, "ready", False))
        dependencies = []
        factory = getattr(request.app.state, "conversation_factory", None)
        if factory is not None:
            dependencies.extend([factory.repository, factory.memory_store])
        document_factory_value = getattr(request.app.state, "document_factory", None)
        if document_factory_value is not None:
            dependencies.append(document_factory_value)
        auth = getattr(request.app.state, "auth", None)
        if auth is not None:
            dependencies.append(auth.repository)
        try:
            for dependency in dependencies:
                check = getattr(dependency, "health_check", None)
                if callable(check) and not await asyncio.to_thread(check):
                    ready = False
        except Exception:
            ready = False
            LOGGER.exception("AVA readiness dependency check failed")
        payload = {"status": "ready" if ready else "not_ready"}
        return Response(
            content=json.dumps(payload),
            status_code=200 if ready else 503,
            media_type="application/json",
        )

    @router.get("/api/health")
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
        history_enabled = (
            getattr(request.app.state, "conversation_service", None) is not None
            or getattr(request.app.state, "conversation_factory", None) is not None
        )
        response["conversation_history"] = {
            "enabled": history_enabled,
            "deployment_boundary": (
                "oidc_multi_user"
                if conversation_settings.mode == "oidc"
                else "single_user"
                if history_enabled
                else "stateless"
            ),
            "long_term_store": conversation_settings.long_term_store,
        }
        response["authentication"] = {
            "mode": "oidc" if conversation_settings.mode == "oidc" else "none",
            "required": conversation_settings.mode == "oidc",
        }
        response["uploads"] = {
            "enabled": bool(
                document_settings.enabled
                and getattr(request.app.state, "document_factory", None) is not None
            ),
            "media_types": ["application/pdf", "text/plain"],
            "maximum_bytes": operational_settings.maximum_upload_bytes,
        }
        response["request_routing"] = {
            "enabled": bool(getattr(active_pipeline, "request_routing_enabled", False)),
            "filing_only_rollback": not bool(
                getattr(active_pipeline, "request_routing_enabled", False)
            ),
        }
        response["tools"] = {
            "calculator_enabled": bool(
                getattr(active_pipeline, "calculator_enabled", False)
            ),
            "web_search_enabled": bool(
                getattr(active_pipeline, "web_search_enabled", False)
            ),
            "maximum_executions": getattr(active_pipeline, "max_tool_executions", 0),
            "maximum_web_searches": getattr(active_pipeline, "max_web_searches", 0),
        }
        return response

    return router
