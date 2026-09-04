"""FastAPI factory and lifecycle wiring for AVA."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.auth.service import OIDCSessionService
from src.config.settings import ApplicationSettings
from src.conversations.service import ConversationService, ConversationServiceFactory
from src.documents import DocumentServiceFactory

from .dependencies import (
    RequestServices,
    build_auth_service,
    build_conversation_factory,
)
from .operations import (
    BodyLimitMiddleware,
    OperationalMiddleware,
    configure_json_logging,
)
from .pipeline import build_pipeline
from .routes import auth, chat, conversations, documents, health


# Temporary compatibility export for existing tests and callers.
safe_stream_error = chat.safe_stream_error


def create_app(
    *,
    application_settings: ApplicationSettings | None = None,
    pipeline: Any | None = None,
    conversation_service: ConversationService | None = None,
    conversation_factory: ConversationServiceFactory | None = None,
    document_factory: DocumentServiceFactory | None = None,
    auth_service: OIDCSessionService | None = None,
) -> FastAPI:
    application_settings = application_settings or ApplicationSettings.from_environment()
    pipeline_settings = application_settings.pipeline
    conversation_settings = application_settings.conversation
    document_settings = application_settings.documents
    operational_settings = application_settings.operations
    request_services = RequestServices(conversation_settings, document_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_json_logging(application_settings.logging)
        application.state.pipeline = pipeline or build_pipeline(
            pipeline_settings, application_settings.provider
        )
        application.state.conversation_service = conversation_service
        application.state.conversation_factory = (
            conversation_factory
            or (
                None
                if conversation_service is not None
                else build_conversation_factory(
                    conversation_settings,
                    pipeline_settings,
                    application.state.pipeline,
                    document_settings,
                )
            )
        )
        application.state.document_factory = (
            document_factory
            or getattr(application.state.conversation_factory, "document_factory", None)
        )
        if (
            conversation_service is not None
            and application.state.document_factory is not None
            and conversation_service.document_lifecycle is None
        ):
            conversation_service.document_lifecycle = (
                application.state.document_factory.for_owner(conversation_service)
            )
        application.state.auth = auth_service or build_auth_service(
            conversation_settings, application_settings.auth
        )
        try:
            yield
        finally:
            factory = getattr(application.state, "conversation_factory", None)
            memory_store = getattr(factory, "memory_store", None)
            close_memory = getattr(memory_store, "close", None)
            if callable(close_memory):
                close_memory()
            active_document_factory = getattr(
                application.state, "document_factory", None
            )
            close_documents = getattr(active_document_factory, "close", None)
            if callable(close_documents):
                close_documents()
            close_pipeline = getattr(application.state.pipeline, "close", None)
            if callable(close_pipeline):
                close_pipeline()

    application = FastAPI(title="AVA API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(application_settings.ui.cors_origins),
        allow_credentials=conversation_settings.mode == "oidc",
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )
    application.add_middleware(
        BodyLimitMiddleware,
        maximum_bytes=operational_settings.maximum_body_bytes,
        maximum_upload_bytes=operational_settings.maximum_upload_bytes,
    )
    application.add_middleware(
        OperationalMiddleware,
        requests_per_minute=operational_settings.requests_per_minute,
    )
    application.include_router(
        health.create_router(
            pipeline_settings,
            conversation_settings,
            document_settings,
            operational_settings,
        )
    )
    application.include_router(auth.create_router(conversation_settings, request_services))
    application.include_router(conversations.create_router(request_services))
    application.include_router(documents.create_router(request_services))
    application.include_router(
        chat.create_router(
            request_services,
            operational_settings,
            document_settings,
            application_settings.ui.query_max_length,
        )
    )
    return application
