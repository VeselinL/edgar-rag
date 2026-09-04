"""Create and validate the reproducible AVA release-candidate freeze."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable

import httpx

from src.config.settings import ApplicationSettings, PROJECT_ROOT
from src.filings.corpus import ACTIVE_FILINGS
from src.generation import prompts
from src.indexing.qdrant_index import alias_target, load_artifact_bundle, make_client
from src.orchestration import routing
from src.retrieval import scope_aware
from src.tools.web_search import TRUSTED_WEB_SOURCES


DEFAULT_MANIFEST = PROJECT_ROOT / "data/evaluation/finalization/v1/freeze_manifest.json"
SCHEMA_VERSION = 1


def _git(project_root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=project_root, check=True, text=True,
        capture_output=True,
    ).stdout.strip()


def _assert_clean(project_root: Path) -> None:
    if _git(project_root, "status", "--porcelain"):
        raise ValueError("Freeze validation requires a clean worktree.")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _sha256_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"Unsupported freeze-hash value: {type(item).__name__}")

    return "sha256:" + hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=default
        ).encode("utf-8")
    ).hexdigest()


def _relative_hashes(project_root: Path, paths: Iterable[Path]) -> dict[str, str]:
    return {
        str(path.relative_to(project_root)): _sha256_file(path)
        for path in sorted(paths)
    }


def _prompt_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for module in (prompts, routing):
        for name, value in vars(module).items():
            if ("PROMPT" in name or "INSTRUCTION" in name) and isinstance(value, str):
                hashes[f"{module.__name__}.{name}"] = _sha256_json(value)
    return hashes


def _safe_settings(settings: ApplicationSettings) -> dict[str, Any]:
    pipeline = asdict(settings.pipeline)
    pipeline.pop("qdrant_api_key", None)
    pipeline.pop("web_search_api_key", None)
    provider = asdict(settings.provider)
    provider.pop("api_key", None)
    provider.pop("base_url", None)
    provider.pop("app_id", None)
    provider.pop("user_id", None)
    provider.pop("company_id", None)
    conversation = asdict(settings.conversation)
    conversation.pop("postgres_dsn", None)
    auth = asdict(settings.auth)
    auth.pop("client_secret", None)
    value = {
        "pipeline": pipeline,
        "provider": provider,
        "conversation": conversation,
        "documents": {"enabled": settings.documents.enabled},
        "operations": asdict(settings.operations),
        "auth": auth,
        "ui": asdict(settings.ui),
        "logging": asdict(settings.logging),
    }
    return json.loads(json.dumps(value, sort_keys=True))


def _input_hashes(project_root: Path) -> dict[str, dict[str, str]]:
    artifacts = {
        "raw_metadata": [], "processed_blocks": [], "chunks": [],
        "embedding_manifests": [], "embedding_vectors": [],
        "evaluation_manifests": [
            project_root / "data/evaluation/finalization/v1" / name
            for name in (
                "qa_gold.jsonl", "agent_routes.jsonl", "conversations.jsonl",
                "memory.jsonl", "security.jsonl", "ui_language.jsonl", "splits.json",
            )
        ],
    }
    for ticker, filing in ACTIVE_FILINGS.items():
        artifacts["raw_metadata"].append(project_root / "data/raw" / ticker / f"{filing}.metadata.json")
        artifacts["processed_blocks"].append(project_root / "data/processed" / ticker / f"{filing}.blocks.jsonl")
        artifacts["chunks"].append(project_root / "data/chunks" / ticker / f"{filing}.chunks.jsonl")
        artifacts["embedding_manifests"].append(project_root / "data/embeddings" / ticker / f"{filing}.bgebase.embeddings.manifest.json")
        artifacts["embedding_vectors"].append(project_root / "data/embeddings" / ticker / f"{filing}.bgebase.embeddings.npz")
    return {name: _relative_hashes(project_root, paths) for name, paths in artifacts.items()}


def _embedding_configuration(project_root: Path) -> dict[str, Any]:
    ticker, filing = next(iter(ACTIVE_FILINGS.items()))
    value = json.loads(
        (project_root / "data/embeddings" / ticker / f"{filing}.bgebase.embeddings.manifest.json").read_text(encoding="utf-8")
    )
    return {
        key: value[key]
        for key in ("model_repository", "requested_model_revision", "resolved_model_revision", "dimension", "normalized", "query_prefix", "document_prefix")
    }


def _qdrant_server_version(url: str, timeout: int) -> str:
    response = httpx.get(url.rstrip("/") + "/", timeout=timeout)
    response.raise_for_status()
    version_value = response.json().get("version")
    if not isinstance(version_value, str):
        raise ValueError("Qdrant server did not provide a version.")
    return version_value


def _live_qdrant_state(settings: ApplicationSettings) -> dict[str, Any]:
    pipeline = settings.pipeline
    client = make_client(
        url=pipeline.qdrant_url,
        api_key=pipeline.qdrant_api_key,
        timeout=pipeline.qdrant_timeout_seconds,
    )
    target = alias_target(client, pipeline.qdrant_collection_alias)
    if target is None:
        raise ValueError("Frozen Qdrant alias is unavailable.")
    return {
        "server_version": _qdrant_server_version(
            pipeline.qdrant_url, pipeline.qdrant_timeout_seconds
        ),
        "client_version": version("qdrant-client"),
        "alias": pipeline.qdrant_collection_alias,
        "physical_collection": target,
        "point_count": int(client.count(collection_name=target, exact=True).count),
    }


def create_manifest(
    path: Path = DEFAULT_MANIFEST, *, project_root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    """Freeze the clean pre-manifest source commit and all evaluation inputs."""
    project_root = project_root.resolve()
    _assert_clean(project_root)
    bundle = load_artifact_bundle(project_root)
    settings = ApplicationSettings.from_environment(project_root)
    import_path = project_root / "data/indexes/qdrant" / f"{bundle.collection_name}.import.json"
    qdrant_import = json.loads(import_path.read_text(encoding="utf-8"))
    live_qdrant = _live_qdrant_state(settings)
    if live_qdrant["physical_collection"] != bundle.collection_name:
        raise ValueError("Live Qdrant alias does not target the frozen corpus.")
    migration_paths = sorted((project_root / "src").glob("**/migrations/*.sql"))
    frontend_lock = project_root / "src/frontend/package-lock.json"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _git(project_root, "rev-parse", "HEAD"),
        "source_tree": _git(project_root, "rev-parse", "HEAD^{tree}"),
        "runtime": {"python": sys.version.split()[0], "python_requirements": _sha256_file(project_root / "requirements.txt"), "frontend_lockfile": _sha256_file(frontend_lock)},
        "corpus": {"active_filings": ACTIVE_FILINGS, "input_hashes": _input_hashes(project_root), "chunking_config": _sha256_file(project_root / "data/chunks/chunking-config.json"), "artifact_version": bundle.artifact_version, "point_count": bundle.point_count},
        "embeddings": _embedding_configuration(project_root),
        "qdrant": {**live_qdrant, "import_manifest": str(import_path.relative_to(project_root)), "import_manifest_sha256": _sha256_file(import_path), "payload_schema": "keyword: " + ", ".join(("chunk_id", "ticker", "cik", "accession_number", "content_type", "artifact_version")), "audit": qdrant_import["audit"]},
        "retrieval": {"rrf_k": scope_aware.DEFAULT_RRF_K, "candidate_k": scope_aware.DEFAULT_CANDIDATE_K, "final_evidence_k": scope_aware.DEFAULT_FINAL_EVIDENCE_K, "min_chunks_per_subquery": scope_aware.DEFAULT_MIN_CHUNKS_PER_SUBQUERY, "multi_subquery_bonus": scope_aware.DEFAULT_MULTI_SUBQUERY_BONUS},
        "prompts": {"filing_version": prompts.FILING_PROMPT_VERSION, "hashes": _prompt_hashes()},
        "trusted_source_registry_sha256": _sha256_json(TRUSTED_WEB_SOURCES),
        "migrations": _relative_hashes(project_root, migration_paths),
        "effective_configuration": _safe_settings(settings),
    }
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _permitted_post_freeze_paths(path: Path) -> set[str]:
    return {str(path), "data/evaluation/finalization/v1/phase3/RESULTS.md"}


def _is_ancestor(project_root: Path, ancestor: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, "HEAD"],
        cwd=project_root,
        capture_output=True,
        check=False,
    ).returncode == 0


def _validate_runtime(manifest: dict[str, Any], project_root: Path) -> None:
    settings = ApplicationSettings.from_environment(project_root)
    if _safe_settings(settings) != manifest["effective_configuration"]:
        raise ValueError("Freeze manifest effective configuration does not match.")
    live_qdrant = _live_qdrant_state(settings)
    expected = manifest["qdrant"]
    if any(live_qdrant[key] != expected[key] for key in live_qdrant):
        raise ValueError("Freeze manifest live Qdrant state does not match.")


def validate_manifest(
    path: Path = DEFAULT_MANIFEST, *, project_root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    project_root = project_root.resolve()
    path = path.resolve()
    _assert_clean(project_root)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Freeze manifest schema version is invalid.")
    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str):
        raise ValueError("Freeze manifest source commit is invalid.")
    changed = set(filter(None, _git(project_root, "diff", "--name-only", f"{source_commit}..HEAD").splitlines()))
    permitted = _permitted_post_freeze_paths(path.relative_to(project_root))
    if changed - permitted:
        raise ValueError("Freeze manifest source commit does not match current code.")
    if not _is_ancestor(project_root, source_commit):
        raise ValueError("Freeze manifest source commit is not an ancestor of HEAD.")
    current_hashes = _input_hashes(project_root)
    if current_hashes != manifest["corpus"]["input_hashes"]:
        raise ValueError("Freeze manifest evaluation inputs have changed.")
    bundle = load_artifact_bundle(project_root)
    if bundle.artifact_version != manifest["corpus"]["artifact_version"] or bundle.point_count != manifest["corpus"]["point_count"]:
        raise ValueError("Freeze manifest corpus fingerprint does not match.")
    if _prompt_hashes() != manifest["prompts"]["hashes"]:
        raise ValueError("Freeze manifest prompt hashes do not match.")
    if _sha256_json(TRUSTED_WEB_SOURCES) != manifest["trusted_source_registry_sha256"]:
        raise ValueError("Freeze manifest trusted-source registry changed.")
    _validate_runtime(manifest, project_root)
    return {"status": "passed", "source_commit": source_commit, "artifact_version": bundle.artifact_version}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "validate"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    arguments = parser.parse_args()
    result = create_manifest(arguments.manifest, project_root=arguments.project_root) if arguments.command == "create" else validate_manifest(arguments.manifest, project_root=arguments.project_root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
