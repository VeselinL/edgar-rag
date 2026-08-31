import json
import tempfile
import unittest
import warnings
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient

from src.indexing.qdrant_index import (
    ArtifactBundle,
    ArtifactRecord,
    audit_collection,
    create_payload_indexes,
    ensure_collection,
    point_id,
    switch_alias,
    upload_bundle,
    write_import_manifest,
)


def unit_vector(position: int) -> np.ndarray:
    vector = np.zeros(768, dtype=np.float32)
    vector[position] = 1.0
    return vector


def synthetic_bundle() -> ArtifactBundle:
    chunks = (
        {
            "chunk_id": "TSLA-TEST-1",
            "ticker": "TSLA",
            "cik": "1318605",
            "accession_number": "test",
            "content_type": "narrative",
            "filing_year": 2025,
            "text": "Tesla evidence.",
        },
        {
            "chunk_id": "F-TEST-1",
            "ticker": "F",
            "cik": "37996",
            "accession_number": "test",
            "content_type": "table",
            "filing_year": 2025,
            "text": "Ford evidence.",
        },
    )
    records = tuple(
        ArtifactRecord(
            ticker=chunk["ticker"],
            filing_name="2025-10-K",
            chunk_path=Path(f"{chunk['ticker']}.chunks.jsonl"),
            embedding_path=Path(f"{chunk['ticker']}.embeddings.npz"),
            manifest_path=Path(f"{chunk['ticker']}.manifest.json"),
            chunk_sha256=character * 64,
            embedding_sha256=character * 64,
            manifest_sha256=character * 64,
            chunks=(chunk,),
            vectors=np.stack([unit_vector(position)]),
            embedding_text_hashes=(f"hash-{position + 1}",),
        )
        for position, (chunk, character) in enumerate(zip(chunks, ("a", "b")))
    )
    return ArtifactBundle(
        artifact_version="sha256:test-artifact",
        collection_name="ava_test_collection",
        records=records,
    )


class QdrantIndexTests(unittest.TestCase):
    def setUp(self):
        self.client = QdrantClient(":memory:")
        self.bundle = synthetic_bundle()

    def import_bundle(self):
        self.assertTrue(ensure_collection(self.client, self.bundle.collection_name))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            create_payload_indexes(self.client, self.bundle.collection_name)
            upload_bundle(self.client, self.bundle, parallel=1)

    def test_import_is_idempotent_and_strict_audit_passes(self):
        self.import_bundle()
        self.assertFalse(ensure_collection(self.client, self.bundle.collection_name))
        upload_bundle(self.client, self.bundle, parallel=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            audit = audit_collection(self.client, self.bundle)
        self.assertEqual(audit["point_count"], 2)
        self.assertEqual(audit["status"], "passed")

    def test_alias_switch_returns_previous_target_for_rollback(self):
        self.import_bundle()
        previous = switch_alias(
            self.client,
            alias_name="ava_current",
            collection_name=self.bundle.collection_name,
        )
        self.assertIsNone(previous)
        self.assertEqual(
            switch_alias(
                self.client,
                alias_name="ava_current",
                collection_name=self.bundle.collection_name,
            ),
            self.bundle.collection_name,
        )

    def test_audit_rejects_payload_tampering(self):
        self.import_bundle()
        self.client.set_payload(
            collection_name=self.bundle.collection_name,
            payload={"content_sha256": "tampered"},
            points=[point_id("TSLA-TEST-1")],
            wait=True,
        )
        with warnings.catch_warnings(), self.assertRaisesRegex(
            ValueError, "payload_mismatches"
        ):
            warnings.simplefilter("ignore", UserWarning)
            audit_collection(self.client, self.bundle)

    def test_idempotent_manifest_write_preserves_recovery_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            first = {
                "physical_collection": "ava_test",
                "snapshot_name": "snapshot-1",
                "previous_alias_target": "ava_previous",
            }
            write_import_manifest(first, project_root=project_root)
            second = {
                "physical_collection": "ava_test",
                "snapshot_name": None,
                "previous_alias_target": None,
            }
            path = write_import_manifest(second, project_root=project_root)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["snapshot_name"], "snapshot-1")
        self.assertEqual(saved["previous_alias_target"], "ava_previous")


if __name__ == "__main__":
    unittest.main()
