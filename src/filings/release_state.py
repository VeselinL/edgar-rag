"""Guard readers from observing a partially promoted table corpus."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"
TABLE_MIGRATION_MARKER = ".table-migration-in-progress"


def assert_release_available(
    artifact_path: str | Path,
    *,
    data_root: str | Path = DATA_ROOT,
) -> None:
    """Reject live-data reads while an aligned table release is being switched.

    Staged paths outside ``data/`` are deliberately unaffected so a release can
    be validated before promotion.  A marker is left in place if file-level
    promotion fails; readers then fail closed instead of mixing schema versions.
    """
    artifact = Path(artifact_path).resolve()
    root = Path(data_root).resolve()
    try:
        artifact.relative_to(root)
    except ValueError:
        return
    marker = root / TABLE_MIGRATION_MARKER
    if marker.exists():
        raise RuntimeError(
            "Table corpus migration is in progress; refusing to load live "
            f"artifact {artifact}. Resolve or complete {marker} first."
        )
