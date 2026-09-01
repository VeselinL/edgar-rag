"""Conversation-scoped uploaded document handling."""

from .extraction import (
    DocumentExtractionError,
    ExtractedDocument,
    UploadedDocumentChunk,
    extract_document,
)
from .storage import FilesystemAssetStore, StoredAsset
from .models import StoredDocumentChunk, UploadedDocument
from .repository import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    InMemoryDocumentRepository,
    PostgresDocumentRepository,
)
from .service import DocumentQuotaError, DocumentService
from .retrieval import (
    DocumentEvidence,
    NullDocumentIndex,
    QdrantDocumentIndex,
)

__all__ = [
    "DocumentExtractionError",
    "ExtractedDocument",
    "FilesystemAssetStore",
    "StoredAsset",
    "UploadedDocumentChunk",
    "extract_document",
    "DocumentNotFoundError",
    "DocumentQuotaError",
    "DocumentService",
    "DuplicateDocumentError",
    "InMemoryDocumentRepository",
    "PostgresDocumentRepository",
    "StoredDocumentChunk",
    "UploadedDocument",
    "DocumentEvidence",
    "NullDocumentIndex",
    "QdrantDocumentIndex",
]
