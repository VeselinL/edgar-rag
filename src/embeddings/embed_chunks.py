import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from src.filings.fetch_data import COMPANIES
from src.filings.release_state import assert_release_available
from src.filings.table_processing import TABLE_HEURISTICS_VERSION, TABLE_SCHEMA_VERSION
from src.chunking.chunk_documents import CHUNK_SCHEMA_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHUNKS_DIRECTORY = PROJECT_ROOT / "data" / "chunks"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "embeddings"
DEFAULT_MODEL_NAME = "bgebase"
MODEL_CONFIGS = {
    "minilm": {
        "repository": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "query_prefix": "",
        "document_prefix": "",
    },
    "mpnet": {
        "repository": "sentence-transformers/all-mpnet-base-v2",
        "revision": "e8c3b32edf5434bc2275fc9bab85f82640a19130",
        "query_prefix": "",
        "document_prefix": "",
    },
    "bgebase": {
        "repository": "BAAI/bge-base-en-v1.5",
        "revision": "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a",
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "document_prefix": "",
        "expected_dimension": 768,
        "expected_max_sequence_length": 512,
    },
    "nomic": {
        "repository": "nomic-ai/nomic-embed-text-v1.5",
        "revision": "e9b6763023c676ca8431644204f50c2b100d9aab",
        "query_prefix": "search_query: ",
        "document_prefix": "search_document: ",
    },
}
YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
TABLE_EMBEDDING_POLICY = (
    "logical-table-schema-v2: effective section/region, title, units, source "
    "header context and period/measure paths, plus descriptive logical row "
    "labels; numeric matrices remain in the complete source chunk"
)
TABLE_RENDERER_POLICY = "logical-markdown-v1"
LOGICAL_TABLE_POLICY = "one-logical-table-per-chunk-v1"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ordered_text_hashes_sha256(text_hashes: list[str]) -> str:
    """Bind an ordered list of per-input hashes without duplicating it in JSON."""
    return sha256_text("\n".join(text_hashes) + "\n")


def load_chunks(path: str | Path) -> list[dict]:
    assert_release_available(path)
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
            if chunk.get("chunk_schema_version") != CHUNK_SCHEMA_VERSION:
                raise ValueError(
                    f"Chunk {chunk_id} does not use chunk schema version 3"
                )
            if chunk.get("content_type") == "table":
                if chunk.get("table_schema_version") != TABLE_SCHEMA_VERSION:
                    raise ValueError(
                        f"Table chunk {chunk_id} does not use table schema version 2"
                    )
                if chunk.get("table_heuristics_version") != TABLE_HEURISTICS_VERSION:
                    raise ValueError(
                        f"Table chunk {chunk_id} has an unknown heuristics version"
                    )
                for field in (
                    "logical_table_id",
                    "composition_mode",
                    "table_fragment_count",
                    "fragment_block_ids",
                    "html_table_ids",
                    "logical_header_paths",
                    "logical_header_context",
                    "logical_column_headers",
                    "logical_column_units",
                    "logical_fragments",
                ):
                    if field not in chunk:
                        raise ValueError(
                            f"Table chunk {chunk_id} lacks logical field {field}"
                        )
                if not chunk["logical_fragments"]:
                    raise ValueError(f"Table chunk {chunk_id} has no logical fragments")
                if chunk["composition_mode"] == "compound":
                    fragments = chunk["logical_fragments"]
                else:
                    for field in (
                        "logical_width",
                        "logical_rows",
                        "logical_row_roles",
                        "logical_cell_states",
                        "logical_cell_sources",
                    ):
                        if field not in chunk:
                            raise ValueError(
                                f"Table chunk {chunk_id} lacks composed field {field}"
                            )
                    fragments = chunk["logical_fragments"]
                if any(
                    fragment.get("table_schema_version") != TABLE_SCHEMA_VERSION
                    or fragment.get("table_heuristics_version")
                    != TABLE_HEURISTICS_VERSION
                    for fragment in fragments
                ):
                    raise ValueError(
                        f"Table chunk {chunk_id} contains a stale logical fragment"
                    )
            chunk_ids.add(chunk_id)
            chunks.append(chunk)
    if not chunks:
        raise ValueError(f"No chunks found in {path}")
    for field in ("source_processed_sha256", "chunking_config_sha256"):
        values = {chunk.get(field) for chunk in chunks}
        if None in values or len(values) != 1:
            raise ValueError(f"Chunk file contains mixed or missing {field}")
    return chunks


def find_latest_chunk_file(
    company_name: str,
    chunks_directory: str | Path = DEFAULT_CHUNKS_DIRECTORY,
) -> Path:
    """Return the latest chunk file for a configured company."""
    assert_release_available(chunks_directory)
    company_key = company_name.strip().lower()
    if company_key not in COMPANIES:
        raise ValueError(f"Unknown company: {company_name}")

    ticker = COMPANIES[company_key]["ticker"]
    company_directory = Path(chunks_directory) / ticker
    chunk_paths = list(company_directory.glob("*-10-K.chunks.jsonl"))
    if not chunk_paths:
        raise FileNotFoundError(
            f"No 10-K chunks found for {company_name} in {company_directory}"
        )

    return max(chunk_paths, key=lambda path: int(path.name.split("-", 1)[0]))


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
    if chunk.get("table_schema_version") != TABLE_SCHEMA_VERSION:
        raise ValueError("Table embedding input requires logical table schema version 2")
    parts = list(
        chunk.get("effective_section_path")
        or chunk.get("section_path")
        or [chunk.get("section", "")]
    )
    region = chunk.get("document_region")
    if region and region != "filing_body":
        label = region.replace("_", " ").title()
        if label not in parts:
            parts.append(label)
    title = chunk.get("title")
    if title and title not in parts:
        parts.append(title)
    if chunk.get("units"):
        parts.append(f"Units: {chunk['units']}")
    if any(chunk.get("logical_column_units") or []):
        parts.append(
            f"Column units: {render_row(chunk['logical_column_units'])}"
        )

    fragments = (
        chunk.get("logical_fragments")
        if chunk.get("composition_mode") == "compound"
        else [chunk]
    )
    header_lines = []
    descriptors = []
    for fragment in fragments or []:
        context = fragment.get("logical_header_context") or []
        if context:
            header_lines.append("Header context: " + " — ".join(context))
        for path in fragment.get("logical_header_paths") or []:
            header = " — ".join(value for value in path if value)
            if header and header not in header_lines:
                header_lines.append(header)
        for header in fragment.get("logical_column_headers") or []:
            if header and header not in header_lines:
                header_lines.append(header)
        for row in fragment.get("logical_rows") or []:
            values = [
                str(value).strip()
                for value in row
                if is_descriptive_table_value(value)
            ]
            descriptor = " | ".join(dict.fromkeys(values))
            if descriptor and descriptor not in descriptors:
                descriptors.append(descriptor)
    if header_lines:
        parts.append("Headers:\n" + "\n".join(header_lines))
    if descriptors:
        parts.append("Rows:\n" + "\n".join(descriptors))
    return "\n".join(part for part in parts if part)


def embedding_text(chunk: dict) -> str:
    if chunk.get("content_type") == "table":
        return table_embedding_text(chunk)
    return chunk["text"].strip()


def model_config(model_name: str) -> dict:
    try:
        return MODEL_CONFIGS[model_name]
    except KeyError as exc:
        choices = ", ".join(MODEL_CONFIGS)
        raise ValueError(f"Unknown model name {model_name!r}; choose from: {choices}") from exc


def prepare_document_text(text: str, model_name: str) -> str:
    return model_config(model_name)["document_prefix"] + text


def prepare_query_text(query: str, model_name: str) -> str:
    return model_config(model_name)["query_prefix"] + query


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
    model_name: str = DEFAULT_MODEL_NAME,
    batch_size: int,
    show_progress_bar: bool,
) -> np.ndarray:
    model_inputs = [prepare_document_text(text, model_name) for text in texts]
    vectors = model.encode(
        model_inputs,
        batch_size=batch_size,
        show_progress_bar=show_progress_bar,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return np.asarray(vectors, dtype=np.float32)


def encode_query(
    model, query: str, *, model_name: str = DEFAULT_MODEL_NAME
) -> np.ndarray:
    vector = model.encode(
        [prepare_query_text(query, model_name)],
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


def load_model(
    model_name: str = DEFAULT_MODEL_NAME,
    device: str | None = None,
    revision: str | None = None,
):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required; install dependencies with "
            "'pip install -r requirements.txt'"
        ) from exc

    config = model_config(model_name)
    arguments = {"revision": revision or config["revision"]}
    if device:
        arguments["device"] = device
    return SentenceTransformer(config["repository"], **arguments)


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


def default_output_path(
    input_path: Path, chunks: list[dict], model_name: str = DEFAULT_MODEL_NAME
) -> Path:
    model_config(model_name)
    name = input_path.name.removesuffix(".chunks.jsonl")
    return (
        DEFAULT_OUTPUT_ROOT
        / chunks[0]["ticker"]
        / f"{name}.{model_name}.embeddings.npz"
    )


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
    device: str | None = None,
    batch_size: int = 32,
    show_progress_bar: bool = True,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    config = model_config(model_name)
    input_path = Path(input_path)
    chunks = load_chunks(input_path)
    output_path = (
        Path(output_path)
        if output_path
        else default_output_path(input_path, chunks, model_name)
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    if not overwrite:
        if output_path.exists():
            raise FileExistsError(f"Embedding output already exists: {output_path}")
        if manifest_path.exists():
            raise FileExistsError(f"Embedding manifest already exists: {manifest_path}")

    model = load_model(model_name, device)
    texts = [embedding_text(chunk) for chunk in chunks]
    model_inputs = [prepare_document_text(text, model_name) for text in texts]
    lengths = token_lengths(model, model_inputs)
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
        model_name=model_name,
        batch_size=batch_size,
        show_progress_bar=show_progress_bar,
    )
    validate_embeddings(vectors, len(chunks))
    expected_dimension = config.get("expected_dimension")
    if expected_dimension and vectors.shape[1] != expected_dimension:
        raise ValueError(
            f"{model_name} must produce {expected_dimension}-dimensional vectors"
        )
    expected_max_length = config.get("expected_max_sequence_length")
    if expected_max_length and max_sequence_length != expected_max_length:
        raise ValueError(
            f"{model_name} max sequence length changed: {max_sequence_length}"
        )

    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    text_hashes = [sha256_text(text) for text in model_inputs]
    write_vectors(
        output_path,
        vectors,
        chunk_ids,
        text_hashes,
        overwrite=overwrite,
    )

    length_by_chunk_id = dict(zip(chunk_ids, lengths or []))
    truncated_inputs = [
        {
            "chunk_id": chunk_id,
            "content_type": chunk_lookup[chunk_id].get("content_type"),
            "table_kind": chunk_lookup[chunk_id].get("table_kind"),
            "logical_table_id": chunk_lookup[chunk_id].get("logical_table_id"),
            "token_count": length_by_chunk_id.get(chunk_id),
        }
        for chunk_id in truncated_chunk_ids
    ]
    manifest = {
        "schema_version": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_chunks": str(input_path.resolve()),
        "source_chunks_sha256": sha256_file(input_path),
        "embedding_file": output_path.name,
        "embedding_file_sha256": sha256_file(output_path),
        "model_name": model_name,
        "model_repository": config["repository"],
        "requested_model_revision": config["revision"],
        "resolved_model_revision": resolved_model_revision(model),
        "sentence_transformers_version": package_version("sentence-transformers"),
        "numpy_version": np.__version__,
        "dimension": int(vectors.shape[1]),
        "dtype": str(vectors.dtype),
        "normalized": True,
        "document_encoder": "SentenceTransformer.encode",
        "query_prefix": config["query_prefix"],
        "document_prefix": config["document_prefix"],
        "max_sequence_length": max_sequence_length,
        "embedding_text_policy": {
            "narrative": "complete chunk text",
            "table": TABLE_EMBEDDING_POLICY,
        },
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "table_heuristics_version": TABLE_HEURISTICS_VERSION,
        "chunking_config_sha256": chunks[0]["chunking_config_sha256"],
        "source_processed_sha256": chunks[0]["source_processed_sha256"],
        "logical_table_policy": LOGICAL_TABLE_POLICY,
        "table_renderer_policy": TABLE_RENDERER_POLICY,
        "chunk_count": len(chunks),
        "ordered_text_hashes_sha256": ordered_text_hashes_sha256(text_hashes),
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
        "truncated_content_type_counts": dict(
            sorted(Counter(value["content_type"] for value in truncated_inputs).items())
        ),
        "truncated_table_kind_counts": dict(
            sorted(
                Counter(
                    value["table_kind"]
                    for value in truncated_inputs
                    if value["content_type"] == "table"
                ).items()
            )
        ),
        "truncated_inputs": truncated_inputs,
        "batch_size": batch_size,
        "device": str(getattr(model, "device", device)),
    }
    write_json(manifest_path, manifest, overwrite=overwrite)
    validate_embedding_artifacts(input_path, output_path, manifest_path)
    return output_path, manifest_path


def validate_embedding_artifacts(
    chunks_path: str | Path,
    embedding_path: str | Path,
    manifest_path: str | Path,
) -> dict:
    """Validate an embedding release against exact chunks and input semantics."""
    chunks_path = Path(chunks_path)
    embedding_path = Path(embedding_path)
    manifest_path = Path(manifest_path)
    chunks = load_chunks(chunks_path)
    with manifest_path.open(encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    if manifest.get("schema_version") != 3:
        raise ValueError("Embedding manifest schema_version must be 3")
    required = {
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "table_schema_version": TABLE_SCHEMA_VERSION,
        "table_heuristics_version": TABLE_HEURISTICS_VERSION,
        "logical_table_policy": LOGICAL_TABLE_POLICY,
        "table_renderer_policy": TABLE_RENDERER_POLICY,
        "chunking_config_sha256": chunks[0]["chunking_config_sha256"],
        "source_processed_sha256": chunks[0]["source_processed_sha256"],
        "source_chunks_sha256": sha256_file(chunks_path),
        "embedding_file_sha256": sha256_file(embedding_path),
        "chunk_count": len(chunks),
    }
    for field, expected in required.items():
        if manifest.get(field) != expected:
            raise ValueError(
                f"Embedding manifest {field} mismatch: "
                f"{manifest.get(field)!r} != {expected!r}"
            )
    policy = manifest.get("embedding_text_policy") or {}
    if policy.get("narrative") != "complete chunk text" or policy.get("table") != TABLE_EMBEDDING_POLICY:
        raise ValueError("Embedding manifest text policy is stale")
    model_name = manifest.get("model_name")
    config = model_config(model_name)
    if (
        manifest.get("model_repository") != config["repository"]
        or manifest.get("requested_model_revision") != config["revision"]
        or manifest.get("query_prefix") != config["query_prefix"]
        or manifest.get("document_prefix") != config["document_prefix"]
    ):
        raise ValueError("Embedding manifest model configuration mismatch")
    expected_dimension = config.get("expected_dimension")
    if expected_dimension and manifest.get("dimension") != expected_dimension:
        raise ValueError("Embedding manifest model dimension mismatch")
    expected_max_length = config.get("expected_max_sequence_length")
    if (
        expected_max_length
        and manifest.get("max_sequence_length") != expected_max_length
    ):
        raise ValueError("Embedding manifest max sequence length mismatch")

    with np.load(embedding_path, allow_pickle=False) as saved:
        required_arrays = {"embeddings", "chunk_ids", "text_hashes"}
        if set(saved.files) != required_arrays:
            raise ValueError(f"Embedding NPZ fields differ: {saved.files}")
        vectors = np.asarray(saved["embeddings"])
        chunk_ids = saved["chunk_ids"].tolist()
        text_hashes = saved["text_hashes"].tolist()
    validate_embeddings(vectors, len(chunks))
    if vectors.dtype != np.float32 or str(vectors.dtype) != manifest.get("dtype"):
        raise ValueError("Embedding dtype mismatch")
    if vectors.shape[1] != manifest.get("dimension"):
        raise ValueError("Embedding dimension mismatch")
    expected_ids = [chunk["chunk_id"] for chunk in chunks]
    if chunk_ids != expected_ids:
        raise ValueError("Embedding chunk order does not match source chunks")
    expected_hashes = [
        sha256_text(
            prepare_document_text(embedding_text(chunk), model_name)
        )
        for chunk in chunks
    ]
    if text_hashes != expected_hashes:
        raise ValueError("Embedding input text hashes do not match source chunks")
    if manifest.get("ordered_text_hashes_sha256") != ordered_text_hashes_sha256(
        expected_hashes
    ):
        raise ValueError("Embedding ordered text-hash digest mismatch")
    truncated_inputs = manifest.get("truncated_inputs") or []
    truncated_ids = manifest.get("truncated_chunk_ids") or []
    if [value.get("chunk_id") for value in truncated_inputs] != truncated_ids:
        raise ValueError("Embedding truncation details disagree with chunk IDs")
    chunk_lookup = {chunk["chunk_id"]: chunk for chunk in chunks}
    if any(
        value.get("chunk_id") not in chunk_lookup
        or value.get("content_type")
        != chunk_lookup[value["chunk_id"]].get("content_type")
        or value.get("table_kind")
        != chunk_lookup[value["chunk_id"]].get("table_kind")
        or value.get("logical_table_id")
        != chunk_lookup[value["chunk_id"]].get("logical_table_id")
        or not isinstance(value.get("token_count"), int)
        or value["token_count"] <= manifest.get("max_sequence_length", 0)
        for value in truncated_inputs
    ):
        raise ValueError("Embedding truncation provenance is invalid")
    if manifest.get("truncated_input_count") != len(truncated_inputs):
        raise ValueError("Embedding truncated input count mismatch")
    expected_content_counts = dict(
        sorted(Counter(value["content_type"] for value in truncated_inputs).items())
    )
    expected_kind_counts = dict(
        sorted(
            Counter(
                value["table_kind"]
                for value in truncated_inputs
                if value["content_type"] == "table"
            ).items()
        )
    )
    if (
        manifest.get("truncated_content_type_counts") != expected_content_counts
        or manifest.get("truncated_table_kind_counts") != expected_kind_counts
        or manifest.get("truncated_table_count")
        != expected_content_counts.get("table", 0)
        or manifest.get("truncated_narrative_count")
        != expected_content_counts.get("narrative", 0)
    ):
        raise ValueError("Embedding truncation summary mismatch")
    return {
        "chunk_count": len(chunks),
        "dimension": int(vectors.shape[1]),
        "dtype": str(vectors.dtype),
        "normalized": True,
        "source_chunks_sha256": required["source_chunks_sha256"],
        "embedding_file_sha256": required["embedding_file_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed filing chunks with a local Sentence Transformers model."
    )
    parser.add_argument("company", choices=sorted(COMPANIES))
    parser.add_argument(
        "--chunks-directory",
        type=Path,
        default=DEFAULT_CHUNKS_DIRECTORY,
        help="Directory containing chunk files grouped by ticker.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--model-name",
        choices=tuple(MODEL_CONFIGS),
        default=DEFAULT_MODEL_NAME,
    )
    parser.add_argument("--device")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    input_path = find_latest_chunk_file(
        arguments.company, arguments.chunks_directory
    )
    output_path, manifest_path = run_embedding_pipeline(
        input_path,
        output_path=arguments.output,
        model_name=arguments.model_name,
        device=arguments.device,
        batch_size=arguments.batch_size,
        show_progress_bar=not arguments.no_progress,
        overwrite=arguments.overwrite,
    )
    print(f"Wrote embeddings to {output_path}")
    print(f"Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
