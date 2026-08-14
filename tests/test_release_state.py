import tempfile
import unittest
from pathlib import Path

from src.filings.release_state import (
    TABLE_MIGRATION_MARKER,
    assert_release_available,
)


class ReleaseStateTests(unittest.TestCase):
    def test_live_reader_fails_closed_while_marker_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            artifact = data_root / "chunks" / "MBLY" / "2025-10-K.chunks.jsonl"
            artifact.parent.mkdir(parents=True)
            marker = data_root / TABLE_MIGRATION_MARKER
            marker.write_text("test migration\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "migration is in progress"):
                assert_release_available(artifact, data_root=data_root)

            marker.unlink()
            assert_release_available(artifact, data_root=data_root)

    def test_staged_artifact_outside_data_root_is_not_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            data_root.mkdir()
            (data_root / TABLE_MIGRATION_MARKER).write_text(
                "test migration\n", encoding="utf-8"
            )

            assert_release_available(
                root / "table-v2-stage" / "chunks.jsonl",
                data_root=data_root,
            )


if __name__ == "__main__":
    unittest.main()
