import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.embeddings.embed_chunks import (
    embedding_text,
    load_chunks,
    table_embedding_text,
    validate_embeddings,
    write_vectors,
)


class EmbeddingPipelineTests(unittest.TestCase):
    def test_table_embedding_text_keeps_searchable_structure(self):
        chunk = {
            "content_type": "table",
            "section": "Item 8 — Financial Statements",
            "section_path": ["Item 8 — Financial Statements", "Leases"],
            "title": "Operating Lease Maturities",
            "units": "U.S. dollars in millions",
            "column_units": [None, "dollars"],
            "table_headers": [["Year", "Amount"]],
            "table_rows": [["2026", "20"], ["Total lease liabilities", "71"]],
            "text": "complete table text",
        }

        text = table_embedding_text(chunk)

        self.assertIn("Operating Lease Maturities", text)
        self.assertIn("Year | Amount", text)
        self.assertIn("2026", text)
        self.assertIn("Total lease liabilities", text)
        self.assertNotIn("| 20", text)

    def test_narrative_uses_complete_chunk_text(self):
        chunk = {"content_type": "narrative", "text": "  Complete evidence.  "}

        self.assertEqual(embedding_text(chunk), "Complete evidence.")

    def test_embedding_validation_and_npz_output(self):
        vectors = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        validate_embeddings(vectors, 2)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "embeddings.npz"
            write_vectors(
                output,
                vectors,
                ["chunk-1", "chunk-2"],
                ["hash-1", "hash-2"],
                overwrite=False,
            )
            saved = np.load(output, allow_pickle=False)

            np.testing.assert_array_equal(saved["embeddings"], vectors)
            self.assertEqual(saved["chunk_ids"].tolist(), ["chunk-1", "chunk-2"])

    def test_load_chunks_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunks.jsonl"
            path.write_text(
                '{"chunk_id":"same","text":"one"}\n'
                '{"chunk_id":"same","text":"two"}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate chunk_id"):
                load_chunks(path)


if __name__ == "__main__":
    unittest.main()
