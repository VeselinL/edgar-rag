import unittest

from src.observability import RequestTrace, safe_error_class, summarize_request_records


class RequestTraceTests(unittest.TestCase):
    def test_record_has_stable_empty_future_boundaries_and_stage_timing(self):
        trace = RequestTrace("Question", request_id="request")
        with trace.stage("retrieval"):
            pass
        record = trace.as_record()
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["turn_id"], "request")
        self.assertEqual(record["image_candidates"], [])
        self.assertEqual(record["short_term_memory_ids"], [])
        self.assertGreaterEqual(record["stage_latency_ms"]["retrieval"], 0)
        self.assertNotIn("prompt", record)

    def test_safe_error_class_never_contains_raw_error_text(self):
        error = RuntimeError("provider key sk-secret and raw failure")
        self.assertEqual(safe_error_class(error), "pipeline_error")
        self.assertNotIn("secret", safe_error_class(error))

    def test_operations_summary_reports_latency_errors_cancellation_and_concurrency(self):
        first = RequestTrace("one", request_id="one")
        first.stage_latency_ms = {"retrieval": 10.0}
        first.mark_first_token()
        second = RequestTrace("two", request_id="two")
        second.stage_latency_ms = {"retrieval": 20.0}
        second.cancelled = True
        second.safe_error_class = "pipeline_error"
        records = [first.as_record(), second.as_record()]
        summary = summarize_request_records(records)
        self.assertEqual(summary["request_count"], 2)
        self.assertEqual(summary["stage_latency_ms"]["retrieval"]["p50"], 15.0)
        self.assertEqual(summary["error_rate"], 0.5)
        self.assertEqual(summary["cancellation_rate"], 0.5)
        self.assertGreaterEqual(summary["observed_max_concurrency"], 1)
        self.assertIsNone(summary["qdrant_latency_ms"])

    def test_operations_summary_reports_qdrant_latency_and_shadow_parity(self):
        first = RequestTrace("one", request_id="one")
        first.qdrant_latency_ms = 10.0
        first.qdrant_parity_satisfied = True
        second = RequestTrace("two", request_id="two")
        second.qdrant_latency_ms = 30.0
        second.qdrant_parity_satisfied = False
        summary = summarize_request_records([first.as_record(), second.as_record()])
        self.assertEqual(summary["qdrant_latency_ms"]["p50"], 20.0)
        self.assertEqual(summary["qdrant_parity_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
