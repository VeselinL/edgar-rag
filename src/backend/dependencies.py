"""Corpus and retrieval dependency construction for the AVA backend."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import bm25s
import numpy as np

from src.filings.corpus import ACTIVE_FILINGS


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
