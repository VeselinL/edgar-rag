"""Build, audit, snapshot, and activate versioned AVA Qdrant collections."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from uuid import UUID, uuid5

import numpy as np
from qdrant_client import QdrantClient, models

from src.embeddings.embed_chunks import (
    embedding_text,
    ordered_text_hashes_sha256,
    prepare_document_text,
)
from src.filings.corpus import ACTIVE_FILINGS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DEFAULT_ALIAS = "ava_filing_chunks_current"
DENSE_VECTOR_NAME = "dense_bge_base_v1_5"
VECTOR_DIMENSION = 768
POINT_NAMESPACE = UUID("a9125271-2928-4d2b-9726-65a74c27f55b")
IMPORT_SCHEMA_VERSION = 1
PAYLOAD_KEYWORD_FIELDS = (
    "chunk_id",
    "ticker",
    "cik",
    "accession_number",
    "content_type",
    "artifact_version",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def point_id(chunk_id: str) -> str:
    """Return the stable Qdrant UUID for one canonical chunk identifier."""
    return str(uuid5(POINT_NAMESPACE, chunk_id))


@dataclass(frozen=True)
class ArtifactRecord:
    ticker: str
    filing_name: str
    chunk_path: Path
    embedding_path: Path
    manifest_path: Path
    chunk_sha256: str
    embedding_sha256: str
    manifest_sha256: str
    chunks: tuple[dict[str, Any], ...]
    vectors: np.ndarray
    embedding_text_hashes: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.chunks)


@dataclass(frozen=True)
class ArtifactBundle:
    artifact_version: str
    collection_name: str
    records: tuple[ArtifactRecord, ...]

    @property
    def point_count(self) -> int:
        return sum(record.count for record in self.records)

    def iter_points(self) -> Iterator[models.PointStruct]:
        for record in self.records:
            for chunk, vector, embedding_hash in zip(
                record.chunks,
                record.vectors,
                record.embedding_text_hashes,
            ):
                payload = {
                    **chunk,
                    "artifact_version": self.artifact_version,
                    "embedding_model": "BAAI/bge-base-en-v1.5",
                    "embedding_vector_name": DENSE_VECTOR_NAME,
                    "embedding_text_sha256": embedding_hash,
                    "content_sha256": sha256_text(chunk["text"]),
                    "image_asset_ids": chunk.get("image_asset_ids", []),
                }
                yield models.PointStruct(
                    id=point_id(chunk["chunk_id"]),
                    vector={DENSE_VECTOR_NAME: vector.tolist()},
                    payload=payload,
                )


def _read_chunks(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        chunks = [json.loads(line) for line in file if line.strip()]
    if not chunks:
        raise ValueError(f"Chunk artifact is empty: {path}")
    return chunks


def _load_record(project_root: Path, ticker: str, filing_name: str) -> ArtifactRecord:
    chunk_path = project_root / "data" / "chunks" / ticker / f"{filing_name}.chunks.jsonl"
    embedding_directory = project_root / "data" / "embeddings" / ticker
    embedding_paths = list(embedding_directory.glob(f"{filing_name}.bgebase*.npz"))
    manifest_paths = list(
        embedding_directory.glob(f"{filing_name}.bgebase*.manifest.json")
    )
    if len(embedding_paths) != 1 or len(manifest_paths) != 1:
        raise ValueError(
            f"{ticker}: expected one BGE NPZ and manifest; found "
            f"{len(embedding_paths)} and {len(manifest_paths)}."
        )
    embedding_path = embedding_paths[0]
    manifest_path = manifest_paths[0]
    chunks = _read_chunks(chunk_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunk_sha256 = sha256_file(chunk_path)
    embedding_sha256 = sha256_file(embedding_path)
    if manifest.get("schema_version") != 3:
        raise ValueError(f"{ticker}: embedding manifest schema must be 3.")
    expected_manifest = {
        "source_chunks_sha256": chunk_sha256,
        "embedding_file_sha256": embedding_sha256,
        "model_name": "bgebase",
        "dimension": VECTOR_DIMENSION,
        "dtype": "float32",
        "normalized": True,
        "chunk_count": len(chunks),
        "chunk_schema_version": 3,
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            raise ValueError(
                f"{ticker}: manifest {field} mismatch: "
                f"{manifest.get(field)!r} != {expected!r}."
            )
    with np.load(embedding_path, allow_pickle=False) as archive:
        if set(archive.files) != {"embeddings", "chunk_ids", "text_hashes"}:
            raise ValueError(f"{ticker}: unexpected NPZ fields {archive.files}.")
        vectors = np.asarray(archive["embeddings"])
        chunk_ids = archive["chunk_ids"].tolist()
        text_hashes = archive["text_hashes"].tolist()
    expected_ids = [chunk["chunk_id"] for chunk in chunks]
    if chunk_ids != expected_ids:
        raise ValueError(f"{ticker}: NPZ chunk IDs are not aligned with JSONL order.")
    if len(text_hashes) != len(chunks):
        raise ValueError(f"{ticker}: embedding text-hash count mismatch.")
    expected_text_hashes = [
        sha256_text(prepare_document_text(embedding_text(chunk), "bgebase"))
        for chunk in chunks
    ]
    if text_hashes != expected_text_hashes:
        raise ValueError(f"{ticker}: embedding input text hashes are stale.")
    if manifest.get("ordered_text_hashes_sha256") != ordered_text_hashes_sha256(
        expected_text_hashes
    ):
        raise ValueError(f"{ticker}: ordered embedding text-hash digest mismatch.")
    if vectors.shape != (len(chunks), VECTOR_DIMENSION):
        raise ValueError(f"{ticker}: invalid vector shape {vectors.shape}.")
    if vectors.dtype != np.float32 or not np.isfinite(vectors).all():
        raise ValueError(f"{ticker}: vectors must be finite float32 values.")
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3):
        raise ValueError(f"{ticker}: vectors are not normalized.")
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError(f"{ticker}: duplicate chunk IDs in JSONL.")
    return ArtifactRecord(
        ticker=ticker,
        filing_name=filing_name,
        chunk_path=chunk_path,
        embedding_path=embedding_path,
        manifest_path=manifest_path,
        chunk_sha256=chunk_sha256,
        embedding_sha256=embedding_sha256,
        manifest_sha256=sha256_file(manifest_path),
        chunks=tuple(chunks),
        vectors=vectors,
        embedding_text_hashes=tuple(text_hashes),
    )


def load_artifact_bundle(project_root: Path = PROJECT_ROOT) -> ArtifactBundle:
    records = tuple(
        _load_record(project_root, ticker, filing_name)
        for ticker, filing_name in ACTIVE_FILINGS.items()
    )
    all_ids = [
        chunk["chunk_id"] for record in records for chunk in record.chunks
    ]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Corpus contains duplicate chunk IDs across filings.")
    digest = hashlib.sha256()
    for record in records:
        digest.update(record.ticker.encode())
        digest.update(bytes.fromhex(record.chunk_sha256))
        digest.update(bytes.fromhex(record.embedding_sha256))
        digest.update(bytes.fromhex(record.manifest_sha256))
    artifact_hash = digest.hexdigest()
    return ArtifactBundle(
        artifact_version=f"sha256:{artifact_hash}",
        collection_name=f"ava_filing_chunks_{artifact_hash[:16]}",
        records=records,
    )


def make_client(
    *,
    url: str | None = DEFAULT_QDRANT_URL,
    api_key: str | None = None,
    local_path: Path | None = None,
    timeout: int = 30,
) -> QdrantClient:
    if local_path is not None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(local_path))
    if not url:
        raise ValueError("A Qdrant URL or local path is required.")
    return QdrantClient(url=url, api_key=api_key, timeout=timeout)


def ensure_collection(client: QdrantClient, collection_name: str) -> bool:
    """Create the immutable-schema physical collection if it does not exist."""
    if client.collection_exists(collection_name):
        info = client.get_collection(collection_name)
        vectors = info.config.params.vectors
        config = vectors.get(DENSE_VECTOR_NAME) if isinstance(vectors, dict) else None
        if config is None or config.size != VECTOR_DIMENSION or config.distance != models.Distance.DOT:
            raise ValueError(f"Existing collection {collection_name} has incompatible vectors.")
        return False
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(
                size=VECTOR_DIMENSION,
                distance=models.Distance.DOT,
            )
        },
    )
    return True


def create_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    for field in PAYLOAD_KEYWORD_FIELDS:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
    client.create_payload_index(
        collection_name=collection_name,
        field_name="filing_year",
        field_schema=models.PayloadSchemaType.INTEGER,
        wait=True,
    )


def upload_bundle(
    client: QdrantClient,
    bundle: ArtifactBundle,
    *,
    batch_size: int = 128,
    parallel: int = 4,
) -> None:
    if batch_size <= 0 or parallel <= 0:
        raise ValueError("Batch size and parallelism must be positive.")
    client.upload_points(
        collection_name=bundle.collection_name,
        points=bundle.iter_points(),
        batch_size=batch_size,
        parallel=parallel,
        max_retries=3,
        wait=True,
    )


def _scroll_points(
    client: QdrantClient, collection_name: str
) -> Iterator[Any]:
    offset: Any = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=[DENSE_VECTOR_NAME],
        )
        for point in points:
            yield point
        if offset is None:
            return


def audit_collection(
    client: QdrantClient,
    bundle: ArtifactBundle,
    *,
    collection_name: str | None = None,
) -> dict[str, Any]:
    target = collection_name or bundle.collection_name
    count = int(client.count(collection_name=target, exact=True).count)
    expected = {
        chunk["chunk_id"]: {
            "point_id": point_id(chunk["chunk_id"]),
            "ticker": chunk["ticker"],
            "content_sha256": sha256_text(chunk["text"]),
            "embedding_text_sha256": embedding_hash,
            "vector": vector,
        }
        for record in bundle.records
        for chunk, embedding_hash, vector in zip(
            record.chunks, record.embedding_text_hashes, record.vectors
        )
    }
    observed: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    point_id_mismatches: list[str] = []
    vector_mismatches: list[str] = []
    for point in _scroll_points(client, target):
        payload = point.payload or {}
        chunk_id = payload.get("chunk_id")
        if not isinstance(chunk_id, str):
            raise ValueError("Qdrant point is missing a string chunk_id payload.")
        if chunk_id in observed:
            duplicates.append(chunk_id)
        observed[chunk_id] = payload
        expected_item = expected.get(chunk_id)
        if expected_item is None:
            continue
        if str(point.id) != expected_item["point_id"]:
            point_id_mismatches.append(chunk_id)
        vectors = point.vector or {}
        vector = vectors.get(DENSE_VECTOR_NAME) if isinstance(vectors, dict) else None
        if vector is None or not np.array_equal(
            np.asarray(vector, dtype=np.float32), expected_item["vector"]
        ):
            vector_mismatches.append(chunk_id)
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected))
    mismatched = sorted(
        chunk_id
        for chunk_id in set(expected) & set(observed)
        if observed[chunk_id].get("artifact_version") != bundle.artifact_version
        or observed[chunk_id].get("ticker") != expected[chunk_id]["ticker"]
        or observed[chunk_id].get("content_sha256")
        != expected[chunk_id]["content_sha256"]
        or observed[chunk_id].get("embedding_text_sha256")
        != expected[chunk_id]["embedding_text_sha256"]
    )
    failures = {
        "count_mismatch": count != bundle.point_count,
        "missing_ids": missing,
        "unexpected_ids": unexpected,
        "duplicate_ids": sorted(set(duplicates)),
        "point_id_mismatches": sorted(set(point_id_mismatches)),
        "vector_mismatches": sorted(set(vector_mismatches)),
        "payload_mismatches": mismatched,
    }
    if any(bool(value) for value in failures.values()):
        raise ValueError(f"Qdrant collection audit failed: {failures}")
    first_record = bundle.records[0]
    first_chunk = first_record.chunks[0]
    query = client.query_points(
        collection_name=target,
        query=first_record.vectors[0].tolist(),
        using=DENSE_VECTOR_NAME,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="ticker",
                    match=models.MatchValue(value=first_record.ticker),
                )
            ]
        ),
        search_params=models.SearchParams(exact=True),
        limit=1,
        with_payload=["chunk_id", "ticker"],
    )
    if not query.points or query.points[0].payload.get("chunk_id") != first_chunk["chunk_id"]:
        raise ValueError("Qdrant exact-search smoke test did not return the source point.")
    return {
        "collection_name": target,
        "artifact_version": bundle.artifact_version,
        "point_count": count,
        "expected_point_count": bundle.point_count,
        "company_count": len(bundle.records),
        "dimension": VECTOR_DIMENSION,
        "vector_name": DENSE_VECTOR_NAME,
        "distance": "Dot",
        "exact_search_smoke_chunk_id": first_chunk["chunk_id"],
        "status": "passed",
    }


def alias_target(client: QdrantClient, alias_name: str) -> str | None:
    aliases = client.get_aliases().aliases
    return next(
        (alias.collection_name for alias in aliases if alias.alias_name == alias_name),
        None,
    )


def switch_alias(
    client: QdrantClient, *, alias_name: str, collection_name: str
) -> str | None:
    if not client.collection_exists(collection_name):
        raise ValueError(f"Qdrant collection does not exist: {collection_name}")
    previous = alias_target(client, alias_name)
    if previous == collection_name:
        return previous
    operations: list[Any] = []
    if previous is not None:
        operations.append(
            models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=alias_name)
            )
        )
    operations.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(
                collection_name=collection_name,
                alias_name=alias_name,
            )
        )
    )
    client.update_collection_aliases(change_aliases_operations=operations)
    if alias_target(client, alias_name) != collection_name:
        raise RuntimeError("Qdrant alias switch did not take effect.")
    return previous


def create_snapshot(client: QdrantClient, collection_name: str) -> str:
    snapshot = client.create_snapshot(collection_name=collection_name, wait=True)
    if snapshot is None or not snapshot.name:
        raise RuntimeError("Qdrant did not return a snapshot name.")
    return snapshot.name


def restore_snapshot(
    client: QdrantClient, *, collection_name: str, snapshot_location: str
) -> None:
    """Restore a server snapshot into a new physical collection."""
    if client.collection_exists(collection_name):
        raise ValueError(
            f"Restore target already exists; choose a new collection: {collection_name}"
        )
    recovered = client.recover_snapshot(
        collection_name=collection_name,
        location=snapshot_location,
        wait=True,
    )
    if recovered is False or not client.collection_exists(collection_name):
        raise RuntimeError("Qdrant snapshot recovery did not create the target collection.")


def import_manifest(
    bundle: ArtifactBundle,
    audit: dict[str, Any],
    *,
    alias_name: str,
    previous_alias_target: str | None,
    snapshot_name: str | None,
    project_root: Path,
) -> dict[str, Any]:
    return {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_version": bundle.artifact_version,
        "physical_collection": bundle.collection_name,
        "read_alias": alias_name,
        "previous_alias_target": previous_alias_target,
        "snapshot_name": snapshot_name,
        "audit": audit,
        "records": [
            {
                "ticker": record.ticker,
                "filing_name": record.filing_name,
                "chunk_path": str(record.chunk_path.relative_to(project_root)),
                "embedding_path": str(record.embedding_path.relative_to(project_root)),
                "manifest_path": str(record.manifest_path.relative_to(project_root)),
                "chunk_sha256": record.chunk_sha256,
                "embedding_sha256": record.embedding_sha256,
                "manifest_sha256": record.manifest_sha256,
                "point_count": record.count,
            }
            for record in bundle.records
        ],
    }


def write_import_manifest(
    value: dict[str, Any], *, project_root: Path = PROJECT_ROOT
) -> Path:
    directory = project_root / "data" / "indexes" / "qdrant"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{value['physical_collection']}.import.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if value.get("snapshot_name") is None:
            value["snapshot_name"] = existing.get("snapshot_name")
        if value.get("previous_alias_target") is None:
            value["previous_alias_target"] = existing.get(
                "previous_alias_target"
            )
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _client_from_arguments(arguments: argparse.Namespace) -> QdrantClient:
    local_path = Path(arguments.local_path) if arguments.local_path else None
    return make_client(
        url=None if local_path else arguments.url,
        api_key=arguments.api_key,
        local_path=local_path,
        timeout=arguments.timeout,
    )


def build_index(arguments: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(arguments.project_root).resolve()
    bundle = load_artifact_bundle(project_root)
    client = _client_from_arguments(arguments)
    collection_name = arguments.collection or bundle.collection_name
    if collection_name != bundle.collection_name:
        bundle = ArtifactBundle(bundle.artifact_version, collection_name, bundle.records)
    ensure_collection(client, bundle.collection_name)
    create_payload_indexes(client, bundle.collection_name)
    upload_bundle(
        client,
        bundle,
        batch_size=arguments.batch_size,
        parallel=1 if arguments.local_path else arguments.parallel,
    )
    audit = audit_collection(client, bundle)
    snapshot_name = None
    if arguments.snapshot:
        if arguments.local_path:
            raise ValueError("Snapshots require a running Qdrant server, not local mode.")
        snapshot_name = create_snapshot(client, bundle.collection_name)
    previous = None
    if arguments.activate:
        previous = switch_alias(
            client,
            alias_name=arguments.alias,
            collection_name=bundle.collection_name,
        )
    manifest = import_manifest(
        bundle,
        audit,
        alias_name=arguments.alias,
        previous_alias_target=previous,
        snapshot_name=snapshot_name,
        project_root=project_root,
    )
    manifest_path = write_import_manifest(manifest, project_root=project_root)
    return {**manifest, "import_manifest_path": str(manifest_path)}


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("build", "audit", "snapshot", "restore", "activate", "rollback", "status"),
    )
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--url", default=os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL))
    parser.add_argument("--api-key", default=os.getenv("QDRANT_API_KEY"))
    parser.add_argument("--local-path")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--collection")
    parser.add_argument("--alias", default=os.getenv("QDRANT_COLLECTION_ALIAS", DEFAULT_ALIAS))
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--snapshot-location")
    parser.add_argument("--restore-collection")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = parse_arguments(argv)
    bundle = load_artifact_bundle(Path(arguments.project_root).resolve())
    if arguments.command == "build":
        result = build_index(arguments)
    else:
        client = _client_from_arguments(arguments)
        collection = arguments.collection or bundle.collection_name
        if arguments.command == "audit":
            result = audit_collection(client, bundle, collection_name=collection)
        elif arguments.command == "snapshot":
            if arguments.local_path:
                raise ValueError("Snapshots require a running Qdrant server.")
            result = {"collection_name": collection, "snapshot_name": create_snapshot(client, collection)}
        elif arguments.command == "restore":
            if arguments.local_path:
                raise ValueError("Snapshots require a running Qdrant server.")
            if not arguments.snapshot_location or not arguments.restore_collection:
                raise ValueError(
                    "restore requires --snapshot-location and --restore-collection."
                )
            restore_snapshot(
                client,
                collection_name=arguments.restore_collection,
                snapshot_location=arguments.snapshot_location,
            )
            result = audit_collection(
                client, bundle, collection_name=arguments.restore_collection
            )
            result["snapshot_location"] = arguments.snapshot_location
        elif arguments.command in {"activate", "rollback"}:
            previous = switch_alias(client, alias_name=arguments.alias, collection_name=collection)
            result = {
                "operation": arguments.command,
                "alias": arguments.alias,
                "collection_name": collection,
                "previous_collection_name": previous,
            }
        else:
            result = {
                "collection_name": collection,
                "collection_exists": client.collection_exists(collection),
                "alias": arguments.alias,
                "alias_target": alias_target(client, arguments.alias),
                "expected_point_count": bundle.point_count,
                "artifact_version": bundle.artifact_version,
            }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
