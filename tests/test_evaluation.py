import unittest
import hashlib
import json
from pathlib import Path

from src.evaluation.evaluate_retrieval import metric_summary
from src.evaluation.migrate_mobileye_gold import migrate_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvaluationCompatibilityTests(unittest.TestCase):
    def test_metrics_keep_recall_and_ranking_separate(self):
        summary = metric_summary(
            [
                {"recall_at_k": 1.0, "reciprocal_rank_at_k": 0.5},
                {"recall_at_k": 0.5, "reciprocal_rank_at_k": 0.0},
            ]
        )
        self.assertEqual(summary["mean_recall_at_k"], 0.75)
        self.assertEqual(summary["mean_reciprocal_rank_at_k"], 0.25)
        self.assertEqual(summary["hit_rate_at_k"], 0.5)

    def test_mobileye_gold_migrates_all_records_by_evidence_identity(self):
        migrated = migrate_dataset(
            PROJECT_ROOT
            / "data"
            / "evaluation"
            / "mobileye_retrieval_gold_v1.json",
            PROJECT_ROOT
            / "data"
            / "chunks"
            / "MBLY"
            / "2025-10-K.chunks.jsonl",
            approve_review=True,
        )

        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["chunk_schema_version"], 3)
        self.assertEqual(migrated["table_schema_version"], 2)
        self.assertEqual(
            migrated["table_heuristics_version"], "sec-logical-v2"
        )
        self.assertEqual(migrated["record_count"], 60)
        self.assertEqual(
            migrated["migration_summary"]["evidence_item_count"], 102
        )
        self.assertFalse(
            migrated["migration_summary"]["manual_review_required_items"]
        )
        self.assertTrue(
            all(
                record["old_relevant_chunk_ids"]
                and record["new_relevant_chunk_ids"]
                and record["review_state"]["status"] == "approved"
                for record in migrated["records"]
            )
        )

    def test_baseline_review_covers_every_incomplete_retrieval(self):
        baseline_path = (
            PROJECT_ROOT
            / "data"
            / "evaluation"
            / "mobileye_bgebase_table_v2_baseline.json"
        )
        review_path = (
            PROJECT_ROOT
            / "data"
            / "evaluation"
            / "mobileye_bgebase_table_v2_review.json"
        )
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        baseline_hash = hashlib.sha256(baseline_path.read_bytes()).hexdigest()

        self.assertEqual(review["baseline_sha256"], f"sha256:{baseline_hash}")
        self.assertEqual(
            {value["id"] for value in baseline["regressions_for_review"]},
            {value["id"] for value in review["reviews"]},
        )
        self.assertTrue(
            all(
                value["decision"] == "approved_as_baseline_limitation"
                and value["artifact_integrity"] == "verified"
                and value["reason"]
                for value in review["reviews"]
            )
        )


if __name__ == "__main__":
    unittest.main()
