"""Corpus and retrieval dependency construction for the AVA backend."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import bm25s
import numpy as np
from fastapi import HTTPException, Request

from src.auth.oidc import AuthenticationError, OIDCSettings, OIDCTokenVerifier
from src.auth.repository import PostgresAuthRepository
from src.auth.service import OIDCSessionService, SessionSettings
from src.config.settings import AuthSettings, PipelineSettings
from src.conversations.context import ConversationContextBuilder
from src.conversations.memory import NullMemoryStore, QdrantMemoryStore
from src.conversations.repository import PostgresConversationRepository
from src.conversations.service import (
    ConversationService,
    ConversationServiceFactory,
    ConversationSettings,
)
from src.documents import (
    DocumentService,
    DocumentServiceFactory,
    DocumentSettings,
    FilesystemAssetStore,
    PostgresDocumentRepository,
    QdrantDocumentIndex,
)
from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.filings.corpus import ACTIVE_FILINGS
from src.indexing.qdrant_index import make_client


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILINGS = ACTIVE_FILINGS


def corpus_version(chunks: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk["chunk_id"].encode())
        digest.update(b"\0")
        digest.update(chunk.get("source_processed_sha256", "").encode())
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def load_corpus(
    project_root: Path = PROJECT_ROOT,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    company_embeddings = []
    all_chunks: list[dict[str, Any]] = []
    for ticker, filing_name in FILINGS.items():
        embedding_paths = list(
            (project_root / "data" / "embeddings" / ticker).glob(
                f"{filing_name}.bgebase*.npz"
            )
        )
        if len(embedding_paths) != 1:
            raise ValueError(
                f"Expected one BGE-base vector artifact for {ticker}; found {len(embedding_paths)}."
            )
        with np.load(embedding_paths[0]) as archive:
            embeddings = archive["embeddings"]
        chunk_path = project_root / "data" / "chunks" / ticker / f"{filing_name}.chunks.jsonl"
        with chunk_path.open(encoding="utf-8") as file:
            chunks = [json.loads(line) for line in file if line.strip()]
        if len(embeddings) != len(chunks):
            raise ValueError(f"{ticker}: embedding and chunk counts differ.")
        company_embeddings.append(embeddings)
        all_chunks.extend(chunks)
    matrix = np.vstack(company_embeddings)
    if len(matrix) != len(all_chunks):
        raise ValueError("Full embedding and chunk corpus counts differ.")
    return matrix, all_chunks


def build_bm25_index(chunks: list[dict[str, Any]]) -> bm25s.BM25:
    texts = [chunk.get("text", "") for chunk in chunks]
    if any(not text.strip() for text in texts):
        raise ValueError("Every chunk must have searchable text.")
    retriever = bm25s.BM25()
    retriever.index(bm25s.tokenize(texts))
    return retriever


def build_conversation_factory(
    settings: ConversationSettings,
    pipeline_settings: PipelineSettings,
    active_pipeline: Any,
    document_settings: DocumentSettings,
) -> ConversationServiceFactory | None:
    if settings.mode == "disabled":
        return None
    repository = PostgresConversationRepository(settings.postgres_dsn or "")
    context_builder = ConversationContextBuilder(
        repository,
        recent_token_budget=settings.recent_token_budget,
        summary_token_budget=settings.summary_token_budget,
    )
    memory_store: Any = NullMemoryStore()
    document_factory: DocumentServiceFactory | None = None
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
    if document_settings.enabled:
        model = getattr(getattr(active_pipeline, "retriever", None), "model", None)
        if model is None:
            raise RuntimeError("Uploaded-document retrieval requires the real BGE model.")
        local_path = (
            Path(pipeline_settings.qdrant_local_path).expanduser().resolve()
            if pipeline_settings.qdrant_local_path
            else None
        )
        document_client = make_client(
            url=None if local_path else pipeline_settings.qdrant_url,
            api_key=pipeline_settings.qdrant_api_key,
            local_path=local_path,
            timeout=pipeline_settings.qdrant_timeout_seconds,
        )
        document_factory = DocumentServiceFactory(
            PostgresDocumentRepository(settings.postgres_dsn or ""),
            FilesystemAssetStore(document_settings.asset_root),
            QdrantDocumentIndex(
                document_client,
                model,
                query_prefix=MODEL_CONFIGS["bgebase"]["query_prefix"],
            ),
        )
    return ConversationServiceFactory(
        repository,
        context_builder=context_builder,
        memory_store=memory_store,
        long_term_candidate_k=settings.long_term_candidate_k,
        long_term_score_threshold=settings.long_term_score_threshold,
        long_term_token_budget=settings.long_term_token_budget,
        document_factory=document_factory,
    )


def build_auth_service(
    settings: ConversationSettings,
    auth_settings: AuthSettings,
) -> OIDCSessionService | None:
    if settings.mode != "oidc":
        return None
    oidc_settings = OIDCSettings(
        issuer=auth_settings.issuer,
        client_id=auth_settings.client_id,
        client_secret=auth_settings.client_secret,
        redirect_uri=auth_settings.redirect_uri,
        fixed_tenant_id=auth_settings.fixed_tenant_id,
        tenant_claim=auth_settings.tenant_claim,
        algorithms=auth_settings.algorithms,
        scopes=auth_settings.scopes,
        discovery_timeout_seconds=auth_settings.discovery_timeout_seconds,
        clock_skew_seconds=auth_settings.clock_skew_seconds,
        allow_insecure_http=auth_settings.allow_insecure_http,
    )
    oidc_settings.validate()
    session_settings = SessionSettings(
        cookie_name=auth_settings.cookie_name,
        csrf_cookie_name=auth_settings.csrf_cookie_name,
        login_ttl_seconds=auth_settings.login_ttl_seconds,
        session_ttl_seconds=auth_settings.session_ttl_seconds,
        cookie_secure=auth_settings.cookie_secure,
        cookie_same_site=auth_settings.cookie_same_site,
    )
    session_settings.validate()
    verifier = OIDCTokenVerifier(oidc_settings)
    repository = PostgresAuthRepository(settings.postgres_dsn or "")
    return OIDCSessionService(
        repository,
        verifier,
        session_settings=session_settings,
    )


class RequestServices:
    """Resolve owner-scoped conversation and document services per request."""

    def __init__(
        self,
        conversation_settings: ConversationSettings,
        document_settings: DocumentSettings,
    ) -> None:
        self.conversation_settings = conversation_settings
        self.document_settings = document_settings

    async def conversation_service_for(
        self, request: Request, *, require_csrf: bool = False
    ) -> ConversationService:
        static_service = getattr(request.app.state, "conversation_service", None)
        if static_service is not None:
            return static_service
        factory = getattr(request.app.state, "conversation_factory", None)
        if factory is None:
            raise HTTPException(status_code=503, detail="Conversation history is not enabled.")
        if self.conversation_settings.mode == "single_user":
            return factory.for_owner(
                self.conversation_settings.tenant_id or "",
                self.conversation_settings.user_id or "",
            )
        auth = getattr(request.app.state, "auth", None)
        if auth is None:
            raise HTTPException(status_code=503, detail="Authentication is not available.")
        try:
            session = await asyncio.to_thread(
                auth.authenticate,
                request.cookies.get(auth.settings.cookie_name),
            )
            if require_csrf:
                supplied = request.headers.get("X-CSRF-Token")
                cookie_value = request.cookies.get(auth.settings.csrf_cookie_name)
                if supplied != cookie_value:
                    raise AuthenticationError("The request security token is invalid.")
                await asyncio.to_thread(auth.require_csrf, session, supplied)
        except AuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        return factory.for_owner(
            session.principal.tenant_id,
            session.principal.user_id,
        )

    async def document_service_for(
        self, request: Request, *, require_csrf: bool = False
    ) -> DocumentService:
        conversation_service = await self.conversation_service_for(
            request, require_csrf=require_csrf
        )
        factory = getattr(request.app.state, "document_factory", None)
        if not self.document_settings.enabled or factory is None:
            raise HTTPException(status_code=503, detail="Document uploads are not enabled.")
        lifecycle = getattr(conversation_service, "document_lifecycle", None)
        return lifecycle or factory.for_owner(conversation_service)
