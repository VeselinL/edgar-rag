"""Runtime assembly and stream orchestration for AVA."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

import bm25s
import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer

from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.generation.rag import GenerationService, make_llm_client, resolve_cited_evidence
from src.retrieval.scope_aware import ScopeAwareRetriever

from .sources import normalize_sources


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILINGS = {
    "AUR": "2025-10-K",
    "TSLA": "2025-10-K",
    "MBLY": "2025-10-K",
    "GOOGL": "2025-10-K",
    "GM": "2025-10-K",
    "F": "2025-10-K",
    "NVDA": "2026-10-K",
    "QCOM": "2025-10-K",
    "APTV": "2025-10-K",
    "OUST": "2025-10-K",
}


@dataclass(frozen=True)
class PipelineEvent:
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class PipelineSettings:
    mode: str = "real"
    model_device: str = "cpu"
    reranker_device: str = "cpu"
    llm_model: str = "AZURE_GPT_4o_2024_1120"
    reranker_model: str = "BAAI/bge-reranker-base"

    @classmethod
    def from_environment(cls) -> "PipelineSettings":
        mode = os.getenv("AVA_PIPELINE_MODE", "real").strip().casefold()
        if mode not in {"real", "mock"}:
            raise ValueError("AVA_PIPELINE_MODE must be 'real' or 'mock'.")
        return cls(
            mode=mode,
            model_device=os.getenv("AVA_MODEL_DEVICE", "cpu"),
            reranker_device=os.getenv("AVA_RERANKER_DEVICE", "cpu"),
            llm_model=os.getenv("AVA_LLM_MODEL", "AZURE_GPT_4o_2024_1120"),
            reranker_model=os.getenv("AVA_RERANKER_MODEL", "BAAI/bge-reranker-base"),
        )


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


class RealPipeline:
    mode = "real"
    ready = True

    def __init__(
        self,
        retriever: ScopeAwareRetriever,
        generator: GenerationService,
    ) -> None:
        self.retriever = retriever
        self.generator = generator

    @classmethod
    def build(cls, settings: PipelineSettings) -> "RealPipeline":
        embeddings, chunks = load_corpus()
        normalized = embeddings / np.clip(
            np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-12, None
        )
        bm25_retriever = build_bm25_index(chunks)
        embedding_config = MODEL_CONFIGS["bgebase"]
        embedder = SentenceTransformer(
            embedding_config["repository"], device=settings.model_device
        )
        reranker = CrossEncoder(settings.reranker_model, device=settings.reranker_device)
        retriever = ScopeAwareRetriever(
            model=embedder,
            query_prefix=embedding_config["query_prefix"],
            normalized_embeddings=normalized,
            bm25_retriever=bm25_retriever,
            all_chunks=chunks,
            reranker=reranker,
        )
        generator = GenerationService(make_llm_client(), model=settings.llm_model)
        return cls(retriever, generator)

    async def stream(
        self,
        query: str,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[PipelineEvent]:
        outcome = await asyncio.to_thread(self.retriever.retrieve, query)
        if await is_disconnected():
            return
        evidence = list(outcome.evidence)
        provider_stream = self.generator.stream_answer(query, evidence)
        answer_fragments: list[str] = []
        sentinel = object()

        def next_fragment() -> object:
            return next(provider_stream, sentinel)

        try:
            while True:
                fragment = await asyncio.to_thread(next_fragment)
                if fragment is sentinel:
                    break
                if await is_disconnected():
                    return
                if isinstance(fragment, str) and fragment:
                    answer_fragments.append(fragment)
                    yield PipelineEvent("delta", {"text": fragment})
        finally:
            close = getattr(provider_stream, "close", None)
            if callable(close):
                close()

        if not answer_fragments:
            raise RuntimeError("The LLM stream ended without generated text.")

        cited, used_fallback = resolve_cited_evidence("".join(answer_fragments), evidence)
        sources, malformed_count = normalize_sources(cited)
        yield PipelineEvent(
            "sources",
            {
                "sources": sources,
                "citation_fallback": used_fallback,
                "malformed_source_count": malformed_count,
            },
        )
        yield PipelineEvent("done", {})


MOCK_NARRATIVE = {
    "company": "Tesla, Inc.",
    "ticker": "TSLA",
    "filing_year": 2025,
    "section": "Item 1 — Business",
    "content_type": "text",
    "text": "Tesla designs, develops, manufactures, leases, and sells electric vehicles and energy generation and storage systems.",
    "source_url": "https://www.sec.gov/Archives/edgar/data/1318605/",
}

MOCK_TABLE = {
    "company": "Mobileye Global Inc.",
    "ticker": "MBLY",
    "filing_year": 2025,
    "section": "Item 8 — Financial Statements",
    "content_type": "table",
    "title": "Illustrative revenue by category",
    "units": "USD millions",
    "headers": ["Category", "2025", "2024", "2023"],
    "rows": [
        ["Product revenue", "1,613", "1,756", "1,783"],
        ["Other revenue", "41", "37", "36"],
    ],
    "column_units": ["text", "USD millions", "USD millions", "USD millions"],
}


class MockPipeline:
    """Explicit deterministic development stream; never used by real mode."""

    mode = "mock"
    ready = True

    def __init__(self, delay_seconds: float = 0.06) -> None:
        self.delay_seconds = delay_seconds

    async def stream(
        self,
        query: str,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[PipelineEvent]:
        await asyncio.sleep(self.delay_seconds)
        if "[mock:pre-error]" in query.casefold():
            raise RuntimeError("Deterministic mock failure before the first token")
        fragments = [
            "AVA found relevant filing evidence. ",
            "This response is arriving as real streamed fragments ",
            "from the isolated development pipeline.",
        ]
        for position, fragment in enumerate(fragments):
            if await is_disconnected():
                return
            yield PipelineEvent("delta", {"text": fragment})
            if "[mock:mid-error]" in query.casefold() and position == 0:
                raise RuntimeError("Deterministic mock failure after partial output")
            await asyncio.sleep(self.delay_seconds)
        yield PipelineEvent(
            "sources",
            {
                "sources": [MOCK_NARRATIVE, MOCK_TABLE],
                "citation_fallback": False,
                "malformed_source_count": 0,
            },
        )
        yield PipelineEvent("done", {})


def build_pipeline(settings: PipelineSettings) -> RealPipeline | MockPipeline:
    if settings.mode == "mock":
        return MockPipeline()
    return RealPipeline.build(settings)
