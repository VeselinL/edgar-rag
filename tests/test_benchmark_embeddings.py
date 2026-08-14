import json
import tempfile
import unittest
from pathlib import Path

from src.embeddings.benchmark_embeddings import (
    REPORT_DATA_END,
    REPORT_DATA_START,
    build_report,
    load_report_results,
    update_report,
    upsert_result,
)


def result(
    model_name: str,
    *,
    input_path: str = "data/chunks/MBLY/2025-10-K.chunks.jsonl",
    source_hash: str = "source-v1",
) -> dict:
    repositories = {
        "minilm": "sentence-transformers/all-MiniLM-L6-v2",
        "mpnet": "sentence-transformers/all-mpnet-base-v2",
    }
    return {
        "run_at": "2026-08-12T12:00:00+00:00",
        "company": "Mobileye Global Inc.",
        "ticker": "MBLY",
        "filing_year": 2025,
        "form": "10-K",
        "input_path": input_path,
        "source_chunks_sha256": source_hash,
        "model_name": model_name,
        "model_repository": repositories[model_name],
        "requested_model_revision": "requested-revision",
        "resolved_model_revision": "resolved-revision",
        "device": "cpu",
        "batch_size": 32,
        "chunk_count": 100,
        "narrative_chunks": 90,
        "table_chunks": 10,
        "dimension": 384 if model_name == "minilm" else 768,
        "parameter_count": 22_000_000,
        "model_bytes": 88_000_000,
        "vector_bytes": 153_600,
        "normalized": True,
        "max_sequence_length": 256,
        "input_token_count": 20_000,
        "effective_token_count": 19_900,
        "input_token_median": 180,
        "input_token_p95": 250,
        "input_token_max": 500,
        "truncated_input_count": 1,
        "truncated_narrative_count": 0,
        "truncated_table_count": 1,
        "model_load_seconds": 1.0,
        "preparation_seconds": 0.5,
        "encoding_seconds": 10.0,
        "end_to_end_seconds": 11.5,
        "chunks_per_second": 10.0,
        "tokens_per_second": 1_990.0,
        "query_latency_median_ms": 10.0,
        "query_latency_p95_ms": 12.0,
        "query_repetitions": 5,
        "python_version": "3.12.0",
        "platform": "Linux-test",
        "sentence_transformers_version": "5.6.1",
        "torch_version": "2.13.0",
        "numpy_version": "2.5.2",
    }


class EmbeddingBenchmarkTests(unittest.TestCase):
    def test_upsert_replaces_same_model_and_adds_new_model(self):
        first = result("minilm")
        replacement = {**first, "encoding_seconds": 5.0}
        second_model = result("mpnet")

        results = upsert_result([first], replacement)
        results = upsert_result(results, second_model)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["encoding_seconds"], 5.0)
        self.assertEqual(results[1]["model_name"], "mpnet")

    def test_new_source_hash_removes_incomparable_results_for_same_input(self):
        old_results = [result("minilm"), result("mpnet")]

        results = upsert_result(
            old_results,
            result("minilm", source_hash="source-v2"),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_chunks_sha256"], "source-v2")

    def test_update_report_creates_machine_readable_report_and_replaces_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "EMBEDDING_REPORT.md"
            update_report(path, result("minilm"))
            update_report(path, {**result("minilm"), "batch_size": 16})
            update_report(path, result("mpnet"))

            report = path.read_text(encoding="utf-8")
            saved = load_report_results(path)

        self.assertIn("# Embedding Report", report)
        self.assertIn(REPORT_DATA_START, report)
        self.assertIn(REPORT_DATA_END, report)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["batch_size"], 16)
        self.assertEqual(saved[1]["model_name"], "mpnet")

    def test_build_report_keeps_json_data_valid(self):
        report = build_report([result("minilm")])
        start = report.index(REPORT_DATA_START) + len(REPORT_DATA_START)
        end = report.index(REPORT_DATA_END, start)

        saved = json.loads(report[start:end])

        self.assertEqual(saved[0]["model_name"], "minilm")
        self.assertIn("This report measures runtime", report)


if __name__ == "__main__":
    unittest.main()
