"""Passive, bounded PDF and UTF-8 text extraction for chat sources."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
import re
from typing import Literal

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from pypdf.errors import PdfReadError
import tiktoken


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_EXTRACTED_TOKENS = 200_000
CHUNK_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 32
ALLOWED_MEDIA_TYPES = {
    "text/plain": "txt",
    "application/pdf": "pdf",
}
_ACTIVE_PDF_MARKERS = re.compile(
    rb"/(?:JavaScript|JS|OpenAction|AA|Launch|EmbeddedFile|RichMedia|XFA)\b",
    re.IGNORECASE,
)


class DocumentExtractionError(ValueError):
    """Uploaded bytes violate the bounded passive-document contract."""


@dataclass(frozen=True)
class UploadedDocumentChunk:
    ordinal: int
    page_number: int | None
    text: str
    token_count: int


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    media_type: Literal["text/plain", "application/pdf"]
    page_count: int | None
    token_count: int
    text: str
    chunks: tuple[UploadedDocumentChunk, ...]


def _safe_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
        raise DocumentExtractionError("The uploaded filename is invalid.")
    clean = PurePath(filename).name
    if clean != filename or clean in {".", ".."} or "\x00" in clean:
        raise DocumentExtractionError("The uploaded filename is invalid.")
    return clean


def _normalize_text(value: str) -> str:
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in value.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _split_pages(
    pages: list[tuple[int | None, str]], encoding: object
) -> tuple[UploadedDocumentChunk, ...]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
        length_function=lambda value: len(encoding.encode(value)),
        separators=["\n\n", "\n", " ", ""],
    )
    chunks: list[UploadedDocumentChunk] = []
    for page_number, text in pages:
        for value in splitter.split_text(text):
            normalized = value.strip()
            if not normalized:
                continue
            chunks.append(
                UploadedDocumentChunk(
                    len(chunks),
                    page_number,
                    normalized,
                    len(encoding.encode(normalized)),
                )
            )
    return tuple(chunks)


def extract_document(filename: str, media_type: str, content: bytes) -> ExtractedDocument:
    """Validate bytes, extract passive text, and produce bounded source chunks."""
    safe_name = _safe_filename(filename)
    expected_extension = ALLOWED_MEDIA_TYPES.get(media_type)
    if expected_extension is None:
        raise DocumentExtractionError("Only PDF and UTF-8 plain-text files are supported.")
    if PurePath(safe_name).suffix.casefold() != f".{expected_extension}":
        raise DocumentExtractionError("The filename extension does not match its media type.")
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise DocumentExtractionError("The uploaded file is empty or exceeds 20 MiB.")

    page_count: int | None = None
    pages: list[tuple[int | None, str]] = []
    if media_type == "text/plain":
        if content.startswith(b"%PDF-") or b"\x00" in content:
            raise DocumentExtractionError("The plain-text file signature is invalid.")
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise DocumentExtractionError("Plain-text uploads must be valid UTF-8.") from error
        normalized = _normalize_text(text)
        if normalized:
            pages.append((None, normalized))
    else:
        if not content.startswith(b"%PDF-"):
            raise DocumentExtractionError("The PDF file signature is invalid.")
        if _ACTIVE_PDF_MARKERS.search(content):
            raise DocumentExtractionError("PDFs with active or embedded content are rejected.")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise DocumentExtractionError("Encrypted PDFs are not supported.")
            page_count = len(reader.pages)
            if page_count > MAX_PDF_PAGES:
                raise DocumentExtractionError("PDF uploads are limited to 200 pages.")
            for index, page in enumerate(reader.pages, start=1):
                normalized = _normalize_text(page.extract_text() or "")
                if normalized:
                    pages.append((index, normalized))
        except DocumentExtractionError:
            raise
        except (PdfReadError, ValueError, TypeError, KeyError, OSError) as error:
            raise DocumentExtractionError("The PDF is malformed or cannot be read safely.") from error

    if not pages:
        raise DocumentExtractionError("The uploaded file contains no extractable text.")
    encoding = tiktoken.get_encoding("o200k_base")
    text = "\n\n".join(page for _, page in pages)
    token_count = len(encoding.encode(text))
    if token_count > MAX_EXTRACTED_TOKENS:
        raise DocumentExtractionError("Extracted document text exceeds 200,000 tokens.")
    chunks = _split_pages(pages, encoding)
    if not chunks:
        raise DocumentExtractionError("The uploaded file contains no indexable text.")
    return ExtractedDocument(
        safe_name,
        media_type,
        page_count,
        token_count,
        text,
        chunks,
    )
