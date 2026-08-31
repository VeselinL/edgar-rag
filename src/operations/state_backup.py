"""Create and restore-check portable PostgreSQL and Qdrant state backups."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Sequence
from urllib.parse import quote, urlparse

import httpx

from src.conversations.memory import MEMORY_COLLECTION
from src.indexing.qdrant_index import DEFAULT_ALIAS, alias_target, make_client


MANIFEST_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_output_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved in {Path("/"), Path.home().resolve()}:
        raise ValueError("Backup output must be a dedicated directory.")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _qdrant_headers(api_key: str | None) -> dict[str, str]:
    return {"api-key": api_key} if api_key else {}


def _download_snapshot(
    *,
    base_url: str,
    api_key: str | None,
    collection: str,
    snapshot_name: str,
    destination: Path,
    timeout: float,
) -> None:
    url = (
        f"{base_url.rstrip('/')}/collections/{quote(collection, safe='')}"
        f"/snapshots/{quote(snapshot_name, safe='')}"
    )
    with httpx.stream(
        "GET", url, headers=_qdrant_headers(api_key), timeout=timeout
    ) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            for block in response.iter_bytes():
                output.write(block)


def create_backup(
    *,
    output_directory: Path,
    postgres_dsn: str,
    qdrant_url: str,
    qdrant_api_key: str | None = None,
    filing_alias: str = DEFAULT_ALIAS,
    memory_collection: str = MEMORY_COLLECTION,
    timeout: float = 60.0,
) -> Path:
    """Create a pg_dump plus downloaded Qdrant snapshots and checksummed manifest."""
    destination = _safe_output_directory(output_directory)
    if any(destination.iterdir()):
        raise ValueError("Backup output directory must be empty.")
    postgres_path = destination / "postgres.dump"
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            f"--file={postgres_path}",
        ],
        check=True,
        env={**os.environ, "PGDATABASE": postgres_dsn},
    )

    client = make_client(
        url=qdrant_url, api_key=qdrant_api_key, timeout=max(1, int(timeout))
    )
    filing_collection = alias_target(client, filing_alias)
    collections = [filing_collection]
    if client.collection_exists(memory_collection):
        collections.append(memory_collection)
    qdrant_records: list[dict[str, Any]] = []
    for collection in dict.fromkeys(collections):
        snapshot = client.create_snapshot(collection_name=collection, wait=True)
        if snapshot is None or not snapshot.name:
            raise RuntimeError(f"Qdrant did not create a snapshot for {collection}.")
        snapshot_path = destination / f"qdrant-{collection}.snapshot"
        _download_snapshot(
            base_url=qdrant_url,
            api_key=qdrant_api_key,
            collection=collection,
            snapshot_name=snapshot.name,
            destination=snapshot_path,
            timeout=timeout,
        )
        collection_info = client.get_collection(collection)
        qdrant_records.append(
            {
                "collection": collection,
                "file": snapshot_path.name,
                "sha256": _sha256(snapshot_path),
                "points_count": collection_info.points_count,
                "snapshot_name": snapshot.name,
            }
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "postgres": {
            "file": postgres_path.name,
            "sha256": _sha256(postgres_path),
        },
        "qdrant": {
            "filing_alias": filing_alias,
            "filing_collection": filing_collection,
            "collections": qdrant_records,
        },
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def verify_backup(directory: Path) -> dict[str, Any]:
    root = directory.expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Unsupported backup manifest schema.")
    records = [manifest["postgres"], *manifest["qdrant"]["collections"]]
    for record in records:
        path = (root / record["file"]).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError(f"Backup file is missing or unsafe: {record['file']}")
        if _sha256(path) != record["sha256"]:
            raise ValueError(f"Backup checksum mismatch: {record['file']}")
    return manifest


def _database_name(dsn: str) -> str:
    return urlparse(dsn).path.lstrip("/")


def restore_postgres_drill(
    *, directory: Path, source_dsn: str, restore_dsn: str, apply: bool
) -> None:
    manifest = verify_backup(directory)
    source_database = _database_name(source_dsn)
    restore_database = _database_name(restore_dsn)
    if not restore_database or restore_database == source_database:
        raise ValueError("Restore DSN must name a separate database.")
    if not restore_database.endswith(("_restore", "_restore_test", "_drill")):
        raise ValueError("Restore database name must end in _restore, _restore_test, or _drill.")
    if not apply:
        raise ValueError("Restore is destructive to the drill database; pass --apply.")
    dump_path = directory.expanduser().resolve() / manifest["postgres"]["file"]
    subprocess.run(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            str(dump_path),
        ],
        check=True,
        env={**os.environ, "PGDATABASE": restore_dsn},
    )


def restore_qdrant_drill(
    *,
    directory: Path,
    qdrant_url: str,
    target_prefix: str,
    qdrant_api_key: str | None = None,
    timeout: float = 60.0,
    apply: bool,
) -> dict[str, str]:
    """Upload every saved snapshot into isolated, previously absent collections."""
    manifest = verify_backup(directory)
    if not target_prefix.startswith("ava_restore_"):
        raise ValueError("Qdrant restore prefix must start with ava_restore_.")
    if not apply:
        raise ValueError("Qdrant restore creates drill collections; pass --apply.")
    client = make_client(
        url=qdrant_url, api_key=qdrant_api_key, timeout=max(1, int(timeout))
    )
    root = directory.expanduser().resolve()
    restored: dict[str, str] = {}
    for record in manifest["qdrant"]["collections"]:
        source = record["collection"]
        target = f"{target_prefix}{source}"
        if client.collection_exists(target):
            raise ValueError(f"Restore collection already exists: {target}")
        snapshot_path = root / record["file"]
        url = (
            f"{qdrant_url.rstrip('/')}/collections/{quote(target, safe='')}"
            "/snapshots/upload?priority=snapshot&wait=true"
        )
        with snapshot_path.open("rb") as snapshot_file:
            response = httpx.post(
                url,
                headers=_qdrant_headers(qdrant_api_key),
                files={"snapshot": (snapshot_path.name, snapshot_file)},
                timeout=timeout,
            )
        response.raise_for_status()
        actual_count = client.get_collection(target).points_count
        expected_count = record.get("points_count")
        if expected_count is not None and actual_count != expected_count:
            raise RuntimeError(
                f"Restored Qdrant point count mismatch for {target}: "
                f"{actual_count} != {expected_count}."
            )
        restored[source] = target
    return restored


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("directory", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("directory", type=Path)
    restore = subparsers.add_parser("restore-postgres-drill")
    restore.add_argument("directory", type=Path)
    restore.add_argument("--restore-dsn", required=True)
    restore.add_argument("--apply", action="store_true")
    qdrant_restore = subparsers.add_parser("restore-qdrant-drill")
    qdrant_restore.add_argument("directory", type=Path)
    qdrant_restore.add_argument("--target-prefix", required=True)
    qdrant_restore.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_arguments(argv)
    source_dsn = os.getenv("AVA_POSTGRES_DSN", "").strip()
    if args.command == "backup":
        if not source_dsn:
            raise RuntimeError("AVA_POSTGRES_DSN is required.")
        result = create_backup(
            output_directory=args.directory,
            postgres_dsn=source_dsn,
            qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            filing_alias=os.getenv("QDRANT_COLLECTION_ALIAS", DEFAULT_ALIAS),
        )
        print(json.dumps({"manifest": str(result)}, indent=2))
    elif args.command == "verify":
        manifest = verify_backup(args.directory)
        print(json.dumps(manifest, indent=2))
    elif args.command == "restore-postgres-drill":
        if not source_dsn:
            raise RuntimeError("AVA_POSTGRES_DSN is required for the safety comparison.")
        restore_postgres_drill(
            directory=args.directory,
            source_dsn=source_dsn,
            restore_dsn=args.restore_dsn,
            apply=args.apply,
        )
        print(json.dumps({"restored": True, "database": _database_name(args.restore_dsn)}))
    else:
        restored = restore_qdrant_drill(
            directory=args.directory,
            qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            target_prefix=args.target_prefix,
            apply=args.apply,
        )
        print(json.dumps({"restored_collections": restored}, indent=2))


if __name__ == "__main__":
    main()
