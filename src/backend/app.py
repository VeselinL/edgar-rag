"""FastAPI streaming adapter for AVA."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .pipeline import PipelineEvent, PipelineSettings, build_pipeline


LOGGER = logging.getLogger("ava.api")
SAFE_STREAM_ERROR = "AVA could not complete this response. Please try again."


class ChatRequest(BaseModel):
    query: str


def encode_sse(event: PipelineEvent) -> bytes:
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n".encode("utf-8")


def create_app(*, pipeline: Any | None = None) -> FastAPI:
    load_dotenv()
    settings = PipelineSettings.from_environment()
    query_max_length = int(os.getenv("AVA_QUERY_MAX_LENGTH", "4000"))
    if query_max_length < 1:
        raise ValueError("AVA_QUERY_MAX_LENGTH must be positive.")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.pipeline = pipeline or build_pipeline(settings)
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
    )

    @application.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        active_pipeline = getattr(request.app.state, "pipeline", None)
        return {
            "status": "ok",
            "mode": getattr(active_pipeline, "mode", settings.mode),
            "pipeline_ready": bool(getattr(active_pipeline, "ready", False)),
        }

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

        async def event_stream() -> AsyncIterator[bytes]:
            try:
                async for event in active_pipeline.stream(body.query, request.is_disconnected):
                    yield encode_sse(event)
            except Exception:
                LOGGER.exception("AVA stream failed")
                yield encode_sse(PipelineEvent("error", {"message": SAFE_STREAM_ERROR}))

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return application


app = create_app()
