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
from sentence_transformers import SentenceTransformer

from src.embeddings.embed_chunks import MODEL_CONFIGS
from src.filings.corpus import ACTIVE_FILINGS
from src.generation.rag import GenerationService, make_llm_client, resolve_cited_evidence
from src.retrieval.scope_aware import ScopeAwareRetriever

from .sources import normalize_sources


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILINGS = ACTIVE_FILINGS


@dataclass(frozen=True)
class PipelineEvent:
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class PipelineSettings:
    mode: str = "real"
    model_device: str = "cpu"
    llm_model: str = "AZURE_GPT_4o_2024_1120"
    llm_streaming: bool = True

    @classmethod
    def from_environment(cls) -> "PipelineSettings":
        mode = os.getenv("AVA_PIPELINE_MODE", "real").strip().casefold()
        if mode not in {"real", "mock"}:
            raise ValueError("AVA_PIPELINE_MODE must be 'real' or 'mock'.")
        raw_streaming = os.getenv("AVA_LLM_STREAMING", "true").strip().casefold()
        if raw_streaming not in {"true", "false"}:
            raise ValueError("AVA_LLM_STREAMING must be 'true' or 'false'.")
        return cls(
            mode=mode,
            model_device=os.getenv("AVA_MODEL_DEVICE", "cpu"),
            llm_model=os.getenv("AVA_LLM_MODEL", "AZURE_GPT_4o_2024_1120"),
            llm_streaming=raw_streaming == "true",
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
        *,
        llm_streaming: bool = True,
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.llm_streaming = llm_streaming
        self.answer_delivery = "provider_streaming" if llm_streaming else "buffered"

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
        retriever = ScopeAwareRetriever(
            model=embedder,
            query_prefix=embedding_config["query_prefix"],
            normalized_embeddings=normalized,
            bm25_retriever=bm25_retriever,
            all_chunks=chunks,
        )
        generator = GenerationService(make_llm_client(), model=settings.llm_model)
        return cls(retriever, generator, llm_streaming=settings.llm_streaming)

    async def stream(
        self,
        query: str,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[PipelineEvent]:
        plan = await asyncio.to_thread(self.generator.plan_retrieval, query)
        outcome = await asyncio.to_thread(
            self.retriever.retrieve, query, plan["subqueries"]
        )
        if await is_disconnected():
            return
        evidence = list(outcome.evidence)
        answer_fragments: list[str] = []

        if self.llm_streaming:
            provider_stream = self.generator.stream_answer(query, evidence)
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
        else:
            answer = await asyncio.to_thread(self.generator.answer, query, evidence)
            if await is_disconnected():
                return
            if answer:
                answer_fragments.append(answer)
                yield PipelineEvent("delta", {"text": answer})

        if not answer_fragments:
            raise RuntimeError("The LLM returned no generated text.")

        citation_resolution = resolve_cited_evidence(
            "".join(answer_fragments), evidence
        )
        sources, malformed_count = normalize_sources(
            list(citation_resolution.evidence)
        )
        if citation_resolution.resolved_ids and malformed_count:
            source_status = "cited_with_unrenderable_items"
        elif citation_resolution.resolved_ids:
            source_status = "cited"
        else:
            source_status = "none_cited"
        yield PipelineEvent(
            "sources",
            {
                "sources": sources,
                "source_status": source_status,
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
    "headers": ["Category", "2025", "2024", "2023", "2022", "2021", "2020", "2019"],
    "rows": [
        ["Product revenue", "1,613", "1,756", "1,783", "1,691", "1,386", "967", "879"],
        ["Other revenue", "41", "37", "36", "31", "29", "21", "18"],
    ],
    "column_units": [
        "text", "USD millions", "USD millions", "USD millions",
        "USD millions", "USD millions", "USD millions", "USD millions",
    ],
}


class MockPipeline:
    """Explicit deterministic development stream; never used by real mode."""

    mode = "mock"
    ready = True
    answer_delivery = "mock_streaming"

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
                "source_status": "cited",
                "malformed_source_count": 0,
            },
        )
        yield PipelineEvent("done", {})


def build_pipeline(settings: PipelineSettings) -> RealPipeline | MockPipeline:
    if settings.mode == "mock":
        return MockPipeline()
    return RealPipeline.build(settings)
