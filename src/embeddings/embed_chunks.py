import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = (
    PROJECT_ROOT / "data" / "chunks" / "MBLY" / "2025-10-K.chunks.jsonl"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "embeddings"
DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
TABLE_EMBEDDING_POLICY = (
    "section, title, units, headers, and descriptive table cells; "
    "the complete table remains in the source chunk"
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_chunks(path: str | Path) -> list[dict]:
    chunks = []
    chunk_ids = set()
    with Path(path).open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            chunk = json.loads(line)
            chunk_id = chunk.get("chunk_id")
            if not chunk_id or chunk_id in chunk_ids:
                raise ValueError(f"Missing or duplicate chunk_id on line {line_number}")
            if not isinstance(chunk.get("text"), str) or not chunk["text"].strip():
                raise ValueError(f"Chunk {chunk_id} has no usable text")
            chunk_ids.add(chunk_id)
            chunks.append(chunk)
    if not chunks:
        raise ValueError(f"No chunks found in {path}")
    return chunks


def render_row(row: list) -> str:
    return " | ".join(
        "" if value is None else str(value).strip() for value in row
    ).rstrip()


def is_descriptive_table_value(value: object) -> bool:
    text = str(value).strip()
    return bool(
        text
        and (
            any(character.isalpha() for character in text)
            or YEAR_PATTERN.fullmatch(text)
        )
    )


def table_embedding_text(chunk: dict) -> str:
    parts = list(chunk.get("section_path") or [chunk.get("section", "")])
    title = chunk.get("title")
    if title and title not in parts:
        parts.append(title)
    if chunk.get("units"):
        parts.append(f"Units: {chunk['units']}")
    if any(chunk.get("column_units") or []):
        parts.append(f"Column units: {render_row(chunk['column_units'])}")

    headers = [render_row(row) for row in chunk.get("table_headers") or []]
    if headers:
        parts.append("Headers:\n" + "\n".join(headers))

    descriptors = []
    for row in chunk.get("table_rows") or []:
        values = [
            str(value).strip()
            for value in row
            if is_descriptive_table_value(value)
        ]
        descriptor = " | ".join(dict.fromkeys(values))
        if descriptor and descriptor not in descriptors:
            descriptors.append(descriptor)
    if descriptors:
        parts.append("Rows:\n" + "\n".join(descriptors))
    return "\n".join(part for part in parts if part)


def embedding_text(chunk: dict) -> str:
    if chunk.get("content_type") == "table":
        return table_embedding_text(chunk)
    return chunk["text"].strip()


def token_lengths(model, texts: list[str]) -> list[int] | None:
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        return None
    tokenized = tokenizer(
        texts,
        add_special_tokens=True,
        padding=False,
        truncation=False,
        verbose=False,
    )
    return [len(input_ids) for input_ids in tokenized["input_ids"]]


def encode_documents(
    model,
    texts: list[str],
    *,
    batch_size: int,
    show_progress_bar: bool,
) -> np.ndarray:
    encoder = getattr(model, "encode_document", None) or model.encode
    vectors = encoder(
        texts,
        batch_size=batch_size,
        show_progress_bar=show_progress_bar,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return np.asarray(vectors, dtype=np.float32)


def encode_query(model, query: str) -> np.ndarray:
    encoder = getattr(model, "encode_query", None) or model.encode
    vector = encoder(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    result = np.asarray(vector, dtype=np.float32)
    validate_embeddings(result, 1)
    return result[0]


def validate_embeddings(vectors: np.ndarray, expected_count: int) -> None:
    if vectors.ndim != 2 or vectors.shape[0] != expected_count or vectors.shape[1] == 0:
        raise ValueError(
            f"Expected {expected_count} embedding vectors, got shape {vectors.shape}"
        )
    if not np.isfinite(vectors).all():
        raise ValueError("Embeddings contain non-finite values")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        raise ValueError("Embeddings are not normalized")


def load_model(model_name: str, device: str | None, revision: str | None):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required; install dependencies with "
            "'pip install -r requirements.txt'"
        ) from exc

    arguments = {}
    if device:
        arguments["device"] = device
    if revision:
        arguments["revision"] = revision
    return SentenceTransformer(model_name, **arguments)


def resolved_model_revision(model) -> str | None:
    try:
        config = model._first_module().auto_model.config
    except (AttributeError, IndexError):
        return None
    return getattr(config, "_commit_hash", None)


def package_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def default_output_path(input_path: Path, chunks: list[dict]) -> Path:
    name = input_path.name.removesuffix(".chunks.jsonl")
    return DEFAULT_OUTPUT_ROOT / chunks[0]["ticker"] / f"{name}.embeddings.npz"


def write_vectors(
    output_path: Path,
    vectors: np.ndarray,
    chunk_ids: list[str],
    text_hashes: list[str],
    *,
    overwrite: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Embedding output already exists: {output_path}")

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=output_path.parent,
            suffix=".npz",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            np.savez_compressed(
                temporary_file,
                embeddings=vectors,
                chunk_ids=np.asarray(chunk_ids),
                text_hashes=np.asarray(text_hashes),
            )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def write_json(path: Path, value: dict, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Embedding manifest already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(value, temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def run_embedding_pipeline(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    revision: str | None = DEFAULT_MODEL_REVISION,
    device: str | None = None,
    batch_size: int = 32,
    show_progress_bar: bool = True,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    input_path = Path(input_path)
    chunks = load_chunks(input_path)
    output_path = Path(output_path) if output_path else default_output_path(input_path, chunks)
    manifest_path = output_path.with_suffix(".manifest.json")
    if not overwrite:
        if output_path.exists():
            raise FileExistsError(f"Embedding output already exists: {output_path}")
        if manifest_path.exists():
            raise FileExistsError(f"Embedding manifest already exists: {manifest_path}")

    model = load_model(model_name, device, revision)
    texts = [embedding_text(chunk) for chunk in chunks]
    lengths = token_lengths(model, texts)
    max_sequence_length = getattr(model, "max_seq_length", None)
    truncated_chunk_ids = []
    if lengths and max_sequence_length:
        truncated_chunk_ids = [
            chunk["chunk_id"]
            for chunk, length in zip(chunks, lengths)
            if length > max_sequence_length
        ]
    chunk_lookup = {chunk["chunk_id"]: chunk for chunk in chunks}

    vectors = encode_documents(
        model,
        texts,
        batch_size=batch_size,
        show_progress_bar=show_progress_bar,
    )
    validate_embeddings(vectors, len(chunks))

    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    text_hashes = [sha256_text(text) for text in texts]
    write_vectors(
        output_path,
        vectors,
        chunk_ids,
        text_hashes,
        overwrite=overwrite,
    )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_chunks": str(input_path.resolve()),
        "source_chunks_sha256": sha256_file(input_path),
        "embedding_file": output_path.name,
        "embedding_file_sha256": sha256_file(output_path),
        "model_name": model_name,
        "requested_model_revision": revision,
        "resolved_model_revision": resolved_model_revision(model),
        "sentence_transformers_version": package_version("sentence-transformers"),
        "numpy_version": np.__version__,
        "dimension": int(vectors.shape[1]),
        "dtype": str(vectors.dtype),
        "normalized": True,
        "document_encoder": "encode_document",
        "max_sequence_length": max_sequence_length,
        "embedding_text_policy": {
            "narrative": "complete chunk text",
            "table": TABLE_EMBEDDING_POLICY,
        },
        "chunk_count": len(chunks),
        "input_token_length_max": max(lengths) if lengths else None,
        "truncated_input_count": len(truncated_chunk_ids),
        "truncated_table_count": sum(
            chunk_lookup[chunk_id].get("content_type") == "table"
            for chunk_id in truncated_chunk_ids
        ),
        "truncated_narrative_count": sum(
            chunk_lookup[chunk_id].get("content_type") == "narrative"
            for chunk_id in truncated_chunk_ids
        ),
        "truncated_chunk_ids": truncated_chunk_ids,
        "batch_size": batch_size,
        "device": str(getattr(model, "device", device)),
    }
    write_json(manifest_path, manifest, overwrite=overwrite)
    return output_path, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed filing chunks with a local Sentence Transformers model."
    )
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--device")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    output_path, manifest_path = run_embedding_pipeline(
        arguments.input,
        output_path=arguments.output,
        model_name=arguments.model,
        revision=arguments.revision,
        device=arguments.device,
        batch_size=arguments.batch_size,
        show_progress_bar=not arguments.no_progress,
        overwrite=arguments.overwrite,
    )
    print(f"Wrote embeddings to {output_path}")
    print(f"Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
