"""Private immutable byte storage for conversation-scoped uploads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class StoredAsset:
    asset_key: str
    sha256: str
    size_bytes: int


class FilesystemAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    @staticmethod
    def _validated_key(asset_key: str) -> str:
        try:
            return str(UUID(asset_key))
        except (ValueError, TypeError) as error:
            raise ValueError("Asset keys must be UUIDs.") from error

    def _path(self, asset_key: str) -> Path:
        key = self._validated_key(asset_key)
        return self.root / key[:2] / f"{key}.blob"

    def put(self, asset_key: str, content: bytes) -> StoredAsset:
        if not content:
            raise ValueError("Cannot store an empty upload asset.")
        path = self._path(asset_key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StoredAsset(
            asset_key,
            hashlib.sha256(content).hexdigest(),
            len(content),
        )

    def read(self, asset_key: str) -> bytes:
        return self._path(asset_key).read_bytes()

    def delete(self, asset_key: str) -> bool:
        path = self._path(asset_key)
        if not path.exists():
            return False
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True
