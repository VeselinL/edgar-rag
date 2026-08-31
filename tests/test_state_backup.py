import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.operations.state_backup import restore_postgres_drill, verify_backup


class StateBackupTests(unittest.TestCase):
    def make_backup(self, directory: Path) -> None:
        postgres = directory / "postgres.dump"
        snapshot = directory / "qdrant-memory.snapshot"
        postgres.write_bytes(b"postgres")
        snapshot.write_bytes(b"qdrant")
        digest = lambda value: hashlib.sha256(value).hexdigest()
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "postgres": {"file": postgres.name, "sha256": digest(b"postgres")},
                    "qdrant": {
                        "collections": [
                            {"file": snapshot.name, "sha256": digest(b"qdrant")}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_verify_rejects_changed_artifact(self):
        with tempfile.TemporaryDirectory() as value:
            directory = Path(value)
            self.make_backup(directory)
            verify_backup(directory)
            (directory / "postgres.dump").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_backup(directory)

    @patch("src.operations.state_backup.subprocess.run")
    def test_restore_requires_separate_drill_database_and_apply(self, run):
        with tempfile.TemporaryDirectory() as value:
            directory = Path(value)
            self.make_backup(directory)
            source = "postgresql://ava:secret@db/ava"
            with self.assertRaisesRegex(ValueError, "separate"):
                restore_postgres_drill(
                    directory=directory,
                    source_dsn=source,
                    restore_dsn=source,
                    apply=True,
                )
            with self.assertRaisesRegex(ValueError, "--apply"):
                restore_postgres_drill(
                    directory=directory,
                    source_dsn=source,
                    restore_dsn="postgresql://ava:secret@db/ava_restore_test",
                    apply=False,
                )
            restore_postgres_drill(
                directory=directory,
                source_dsn=source,
                restore_dsn="postgresql://ava:secret@db/ava_restore_test",
                apply=True,
            )
            command = run.call_args.args[0]
            self.assertIn("--clean", command)
            self.assertNotIn(source, command)
            self.assertEqual(
                run.call_args.kwargs["env"]["PGDATABASE"],
                "postgresql://ava:secret@db/ava_restore_test",
            )


if __name__ == "__main__":
    unittest.main()
