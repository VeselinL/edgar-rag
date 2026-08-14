import tempfile
import unittest
import json
from pathlib import Path

import numpy as np

from src.embeddings.embed_chunks import (
    LOGICAL_TABLE_POLICY,
    MODEL_CONFIGS,
    TABLE_EMBEDDING_POLICY,
    TABLE_RENDERER_POLICY,
    default_output_path,
    embedding_text,
    encode_documents,
    encode_query,
    load_chunks,
    prepare_document_text,
    ordered_text_hashes_sha256,
    prepare_query_text,
    sha256_file,
    sha256_text,
    table_embedding_text,
    validate_embedding_artifacts,
    validate_embeddings,
    write_vectors,
)


class EmbeddingPipelineTests(unittest.TestCase):
    def test_supported_models_and_model_specific_prefixes(self):
        self.assertEqual(
            tuple(MODEL_CONFIGS), ("minilm", "mpnet", "bgebase", "nomic")
        )
        self.assertEqual(prepare_query_text("question", "minilm"), "question")
        self.assertEqual(prepare_query_text("question", "mpnet"), "question")
        self.assertTrue(
            prepare_query_text("question", "bgebase").startswith("Represent this")
        )
        self.assertEqual(
            prepare_query_text("question", "nomic"), "search_query: question"
        )
        self.assertEqual(
            prepare_document_text("evidence", "nomic"),
            "search_document: evidence",
        )

    def test_encoders_apply_only_the_selected_model_prefixes(self):
        class RecordingModel:
            def __init__(self):
                self.inputs = []

            def encode(self, texts, **kwargs):
                self.inputs.append(texts)
                return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

        model = RecordingModel()
        encode_documents(
            model,
            ["document"],
            model_name="nomic",
            batch_size=1,
            show_progress_bar=False,
        )
        encode_query(model, "question", model_name="nomic")

        self.assertEqual(model.inputs[0], ["search_document: document"])
        self.assertEqual(model.inputs[1], ["search_query: question"])

    def test_default_output_path_contains_model_name(self):
        path = default_output_path(
            Path("2025-10-K.chunks.jsonl"),
            [{"ticker": "MBLY"}],
            "mpnet",
        )

        self.assertEqual(path.name, "2025-10-K.mpnet.embeddings.npz")

    def test_table_embedding_text_keeps_searchable_structure(self):
        chunk = {
            "content_type": "table",
            "table_schema_version": 2,
            "section": "Item 8 — Financial Statements",
            "section_path": ["Item 8 — Financial Statements", "Leases"],
            "document_region": "financial_statement_notes",
            "effective_section_path": [
                "Item 8 — Financial Statements",
                "Leases",
            ],
            "title": "Operating Lease Maturities",
            "units": "U.S. dollars in millions",
            "logical_column_units": ["years", "usd_millions"],
            "logical_header_context": ["December 27"],
            "logical_header_paths": [["Year"], ["Amount"]],
            "logical_column_headers": ["Year", "Amount"],
            "logical_rows": [
                ["2026", "$20"],
                ["Total lease liabilities", "$71"],
            ],
            "text": "complete table text",
        }

        text = table_embedding_text(chunk)

        self.assertIn("Operating Lease Maturities", text)
        self.assertEqual(text.count("Header context: December 27"), 1)
        self.assertIn("Year", text)
        self.assertIn("Amount", text)
        self.assertIn("2026", text)
        self.assertIn("Total lease liabilities", text)
        self.assertNotIn("$20", text)
        self.assertNotIn("$71", text)

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

    def test_embedding_artifact_validator_binds_vectors_to_schema_and_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunks_path = root / "chunks.jsonl"
            embedding_path = root / "embeddings.npz"
            manifest_path = root / "embeddings.manifest.json"
            chunk = {
                "chunk_id": "MBLY-2025-CHUNK-000001",
                "chunk_schema_version": 3,
                "content_type": "narrative",
                "text": "Complete evidence.",
                "source_processed_sha256": "processed-hash",
                "chunking_config_sha256": "config-hash",
            }
            chunks_path.write_text(json.dumps(chunk) + "\n", encoding="utf-8")
            vectors = np.zeros((1, 768), dtype=np.float32)
            vectors[0, 0] = 1.0
            write_vectors(
                embedding_path,
                vectors,
                [chunk["chunk_id"]],
                [sha256_text(chunk["text"])],
                overwrite=False,
            )
            config = MODEL_CONFIGS["bgebase"]
            manifest = {
                "schema_version": 3,
                "source_chunks_sha256": sha256_file(chunks_path),
                "embedding_file_sha256": sha256_file(embedding_path),
                "model_name": "bgebase",
                "model_repository": config["repository"],
                "requested_model_revision": config["revision"],
                "query_prefix": config["query_prefix"],
                "document_prefix": config["document_prefix"],
                "dimension": 768,
                "dtype": "float32",
                "max_sequence_length": 512,
                "embedding_text_policy": {
                    "narrative": "complete chunk text",
                    "table": TABLE_EMBEDDING_POLICY,
                },
                "chunk_schema_version": 3,
                "table_schema_version": 2,
                "table_heuristics_version": "sec-logical-v2",
                "chunking_config_sha256": "config-hash",
                "source_processed_sha256": "processed-hash",
                "logical_table_policy": LOGICAL_TABLE_POLICY,
                "table_renderer_policy": TABLE_RENDERER_POLICY,
                "chunk_count": 1,
                "ordered_text_hashes_sha256": ordered_text_hashes_sha256(
                    [sha256_text(chunk["text"])]
                ),
                "truncated_input_count": 0,
                "truncated_table_count": 0,
                "truncated_narrative_count": 0,
                "truncated_chunk_ids": [],
                "truncated_content_type_counts": {},
                "truncated_table_kind_counts": {},
                "truncated_inputs": [],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = validate_embedding_artifacts(
                chunks_path, embedding_path, manifest_path
            )

            self.assertEqual(result["chunk_count"], 1)
            self.assertEqual(result["dimension"], 768)

            manifest["schema_version"] = 2
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version must be 3"):
                validate_embedding_artifacts(
                    chunks_path, embedding_path, manifest_path
                )

    def test_load_chunks_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunks.jsonl"
            path.write_text(
                '{"chunk_id":"same","chunk_schema_version":3,"content_type":"narrative",'
                '"source_processed_sha256":"processed","chunking_config_sha256":"config",'
                '"text":"one"}\n'
                '{"chunk_id":"same","chunk_schema_version":3,"content_type":"narrative",'
                '"source_processed_sha256":"processed","chunking_config_sha256":"config",'
                '"text":"two"}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate chunk_id"):
                load_chunks(path)

    def test_load_chunks_rejects_stale_table_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunks.jsonl"
            path.write_text(
                '{"chunk_id":"table","chunk_schema_version":3,"table_schema_version":1,'
                '"content_type":"table","source_processed_sha256":"processed",'
                '"chunking_config_sha256":"config","text":"old"}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "table schema version 2"):
                load_chunks(path)


if __name__ == "__main__":
    unittest.main()
