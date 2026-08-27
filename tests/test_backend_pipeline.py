import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.backend.pipeline import FILINGS, PipelineSettings, RealPipeline
from src.retrieval.evidence_policy import EvidencePolicyError


class FakeRetriever:
    def __init__(self):
        self.arguments = None

    def retrieve(
        self, query, subqueries, company_resolution=None, subquery_targets=None
    ):
        self.arguments = (query, subqueries, company_resolution, subquery_targets)
        return SimpleNamespace(
            evidence=(
                {
                    "chunk": {
                        "chunk_id": "TSLA-2025-CHUNK-000001",
                        "company": "Tesla, Inc.",
                        "ticker": "TSLA",
                        "filing_year": 2025,
                        "section": "Item 1 — Business",
                        "content_type": "narrative",
                        "text": "Tesla evidence.",
                    }
                },
            ),
            policy_name="company-balanced-token-aware-v1",
            candidate_counts_by_company=(("TSLA", 10),),
            candidate_counts_by_company_subquery=(("TSLA:0", 10),),
            selected_counts_by_company=(("TSLA", 10),),
            quota_satisfied=True,
            context_input_tokens=100,
            context_input_limit=28_672,
            candidates=(),
            chunk_ids=("TSLA-2025-CHUNK-000001",),
        )


class MalformedTableRetriever:
    def retrieve(
        self, query, subqueries, company_resolution=None, subquery_targets=None
    ):
        return SimpleNamespace(
            evidence=(
                {
                    "chunk": {
                        "chunk_id": "TSLA-2025-CHUNK-000099",
                        "company": "Tesla, Inc.",
                        "ticker": "TSLA",
                        "filing_year": 2025,
                        "section": "Item 8 — Financial Statements",
                        "content_type": "table",
                        "text": "Unrenderable table.",
                    }
                },
            ),
            policy_name="company-balanced-token-aware-v1",
            candidate_counts_by_company=(("TSLA", 10),),
            candidate_counts_by_company_subquery=(("TSLA:0", 10),),
            selected_counts_by_company=(("TSLA", 10),),
            quota_satisfied=True,
            context_input_tokens=100,
            context_input_limit=28_672,
            candidates=(),
            chunk_ids=("TSLA-2025-CHUNK-000099",),
        )


class PolicyErrorRetriever:
    def retrieve(self, *args, **kwargs):
        raise EvidencePolicyError("four-plus budget is not configured")


class FakeGenerator:
    def __init__(self, answer_text="Buffered answer [TSLA-2025-CHUNK-000001]"):
        self.planned_query = None
        self.answer_arguments = None
        self.stream_answer_called = False
        self.answer_called = False
        self.answer_text = answer_text
        self.deterministic_resolution = None

    def plan_retrieval(self, query, deterministic_resolution=None):
        self.planned_query = query
        self.deterministic_resolution = deterministic_resolution
        return {
            "needs_multiple_retrievals": True,
            "subqueries": [
                {"query": "Tesla revenue", "tickers": []},
                {"query": "Tesla risk factors", "tickers": []},
            ],
            "operation": None,
            "resolved_tickers": [],
            "company_mentions": [],
            "comparison": False,
            "ambiguity": False,
        }

    def stream_answer(self, query, evidence):
        self.stream_answer_called = True
        self.answer_arguments = (query, evidence)
        yield self.answer_text

    def answer(self, query, evidence):
        self.answer_called = True
        self.answer_arguments = (query, evidence)
        return self.answer_text


class FordTypoGenerator(FakeGenerator):
    def plan_retrieval(self, query, deterministic_resolution=None):
        self.planned_query = query
        self.deterministic_resolution = deterministic_resolution
        return {
            "needs_multiple_retrievals": False,
            "subqueries": [{"query": query, "tickers": ["F"]}],
            "operation": None,
            "resolved_tickers": ["F"],
            "company_mentions": [],
            "comparison": False,
            "ambiguity": False,
        }


class AmbiguousGenerator(FakeGenerator):
    def plan_retrieval(self, query, deterministic_resolution=None):
        self.planned_query = query
        self.deterministic_resolution = deterministic_resolution
        return {
            "needs_multiple_retrievals": False,
            "subqueries": [{"query": query, "tickers": []}],
            "operation": None,
            "resolved_tickers": [],
            "company_mentions": [{"raw_text": "Toyota", "ticker": "none"}],
            "comparison": False,
            "ambiguity": True,
        }


class EachCompanyGenerator(FakeGenerator):
    def plan_retrieval(self, query, deterministic_resolution=None):
        self.planned_query = query
        self.deterministic_resolution = deterministic_resolution
        tickers = list(FILINGS)
        return {
            "needs_multiple_retrievals": False,
            "subqueries": [{"query": query, "tickers": tickers}],
            "operation": None,
            "resolved_tickers": tickers,
            "company_mentions": [],
            "comparison": False,
            "ambiguity": False,
        }


class IndependentCeoGenerator(FakeGenerator):
    def plan_retrieval(self, query, deterministic_resolution=None):
        self.planned_query = query
        self.deterministic_resolution = deterministic_resolution
        return {
            "needs_multiple_retrievals": True,
            "subqueries": [
                {"query": "Tesla Chief Executive Officer", "tickers": ["TSLA"]},
                {"query": "Mobileye Chief Executive Officer", "tickers": ["MBLY"]},
            ],
            "operation": None,
            "resolved_tickers": ["TSLA", "MBLY"],
            "company_mentions": [],
            "comparison": False,
            "ambiguity": False,
        }


class RealPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_deployment_corpus_includes_rivian(self):
        self.assertEqual(FILINGS["RIVN"], "2025-10-K")
        self.assertEqual(len(FILINGS), 11)

    def test_settings_disable_provider_streaming_explicitly(self):
        with patch.dict(
            "os.environ",
            {"AVA_PIPELINE_MODE": "real", "AVA_LLM_STREAMING": "false"},
            clear=False,
        ):
            settings = PipelineSettings.from_environment()

        self.assertFalse(settings.llm_streaming)

    def test_settings_reject_ambiguous_streaming_value(self):
        with patch.dict("os.environ", {"AVA_LLM_STREAMING": "off"}, clear=False):
            with self.assertRaisesRegex(ValueError, "AVA_LLM_STREAMING"):
                PipelineSettings.from_environment()

    def test_settings_read_typed_token_and_four_plus_budgets(self):
        with patch.dict(
            "os.environ",
            {
                "AVA_LLM_CONTEXT_WINDOW_TOKENS": "65536",
                "AVA_LLM_RESERVED_OUTPUT_TOKENS": "8192",
                "AVA_EVIDENCE_FOUR_PLUS_SUPPLEMENTAL": "9",
                "AVA_OBSERVABILITY_RETENTION_DAYS": "14",
            },
            clear=False,
        ):
            settings = PipelineSettings.from_environment()
        self.assertEqual(settings.context_window_tokens, 65_536)
        self.assertEqual(settings.reserved_output_tokens, 8_192)
        self.assertEqual(settings.four_plus_supplemental, 9)
        self.assertEqual(settings.observability_retention_days, 14)

    async def test_planner_subqueries_drive_shared_retrieval_before_streaming(self):
        retriever = FakeRetriever()
        generator = FakeGenerator("Answer [TSLA-2025-CHUNK-000001]")
        pipeline = RealPipeline(retriever, generator)

        async def connected():
            return False

        events = [event async for event in pipeline.stream("Original query", connected)]

        self.assertEqual(generator.planned_query, "Original query")
        self.assertEqual(generator.deterministic_resolution.original_query, "Original query")
        self.assertEqual(retriever.arguments[0], "Original query")
        self.assertEqual(
            retriever.arguments[1], ["Tesla revenue", "Tesla risk factors"]
        )
        self.assertEqual(retriever.arguments[2].resolved_tickers, ())
        self.assertEqual(retriever.arguments[3], [[], []])
        self.assertEqual(generator.answer_arguments[0], "Original query")
        self.assertTrue(generator.stream_answer_called)
        self.assertFalse(generator.answer_called)
        self.assertEqual(
            [event.event for event in events], ["delta", "sources", "done"]
        )

    async def test_fuzzy_company_uses_canonical_internal_retrieval_query(self):
        retriever = FakeRetriever()
        generator = FordTypoGenerator()
        pipeline = RealPipeline(retriever, generator, llm_streaming=False)

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "What are frod's principal segments?", connected
            )
        ]

        self.assertEqual(generator.deterministic_resolution.resolved_tickers, ("F",))
        self.assertEqual(generator.deterministic_resolution.methods, ("fuzzy",))
        self.assertEqual(retriever.arguments[0], "What are frod's principal segments?")
        self.assertEqual(
            retriever.arguments[1],
            ["What are frod's principal segments?\nCompany scope: Ford Motor Company (F)"],
        )
        self.assertEqual(events[-1].event, "done")

    async def test_ambiguous_out_of_corpus_company_clarifies_without_retrieval(self):
        retriever = FakeRetriever()
        generator = AmbiguousGenerator()
        pipeline = RealPipeline(retriever, generator)

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "What is Toyota's autonomous vehicle strategy?", connected
            )
        ]

        self.assertIsNone(retriever.arguments)
        self.assertFalse(generator.answer_called)
        self.assertFalse(generator.stream_answer_called)
        self.assertEqual([event.event for event in events], ["delta", "sources", "done"])
        self.assertIn("Toyota", events[0].data["text"])
        self.assertEqual(events[1].data["sources"], [])

    async def test_policy_failure_returns_clear_no_source_response(self):
        pipeline = RealPipeline(PolicyErrorRetriever(), FakeGenerator())

        async def connected():
            return False

        events = [event async for event in pipeline.stream("Original query", connected)]
        self.assertEqual([event.event for event in events], ["delta", "sources", "done"])
        self.assertIn("three or fewer companies", events[0].data["text"])
        self.assertEqual(events[1].data["sources"], [])

    async def test_each_company_plan_reaches_four_plus_policy_gate(self):
        generator = EachCompanyGenerator()
        pipeline = RealPipeline(PolicyErrorRetriever(), generator)

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "Who is the CEO of each company?", connected
            )
        ]

        self.assertEqual(
            generator.deterministic_resolution.resolved_tickers, tuple(FILINGS)
        )
        self.assertEqual([event.event for event in events], ["delta", "sources", "done"])
        self.assertIn("evidence budget is not configured", events[0].data["text"])
        self.assertEqual(events[1].data["sources"], [])

    async def test_independent_multi_company_facts_are_not_forced_to_comparison(self):
        retriever = FakeRetriever()
        generator = IndependentCeoGenerator()
        pipeline = RealPipeline(retriever, generator, llm_streaming=False)

        async def connected():
            return False

        query = "Who are CEOs of Tesla and Mobileye?"
        events = [event async for event in pipeline.stream(query, connected)]

        self.assertEqual(generator.deterministic_resolution.resolved_tickers, ("TSLA", "MBLY"))
        self.assertFalse(retriever.arguments[2].comparison)
        self.assertEqual(
            retriever.arguments[1],
            [
                "Tesla Chief Executive Officer\nCompany scope: Tesla, Inc. (TSLA)",
                "Mobileye Chief Executive Officer\nCompany scope: Mobileye Global Inc. (MBLY)",
            ],
        )
        self.assertEqual([event.event for event in events], ["delta", "sources", "done"])

    async def test_buffered_mode_emits_completed_answer_as_one_delta(self):
        retriever = FakeRetriever()
        generator = FakeGenerator()
        pipeline = RealPipeline(retriever, generator, llm_streaming=False)
        self.assertEqual(pipeline.answer_delivery, "buffered")

        async def connected():
            return False

        events = [event async for event in pipeline.stream("Original query", connected)]

        self.assertFalse(generator.stream_answer_called)
        self.assertTrue(generator.answer_called)
        self.assertEqual(
            [(event.event, event.data) for event in events],
            [
                (
                    "delta",
                    {"text": "Buffered answer [TSLA-2025-CHUNK-000001]"},
                ),
                (
                    "sources",
                    {
                        "sources": [
                            {
                                "company": "Tesla, Inc.",
                                "ticker": "TSLA",
                                "filing_year": 2025,
                                "section": "Item 1 — Business",
                                "content_type": "text",
                                "text": "Tesla evidence.",
                            }
                        ],
                        "source_status": "cited",
                        "malformed_source_count": 0,
                    },
                ),
                ("done", {}),
            ],
        )

    async def test_no_citation_emits_no_sources_without_fallback(self):
        pipeline = RealPipeline(
            FakeRetriever(),
            FakeGenerator("Answer with no citation."),
            llm_streaming=False,
        )

        async def connected():
            return False

        events = [event async for event in pipeline.stream("Original query", connected)]

        self.assertEqual(
            events[-2].data,
            {
                "sources": [],
                "source_status": "none_cited",
                "malformed_source_count": 0,
            },
        )

    async def test_cited_malformed_table_does_not_add_unrelated_sources(self):
        pipeline = RealPipeline(
            MalformedTableRetriever(),
            FakeGenerator("Answer [TSLA-2025-CHUNK-000099]."),
            llm_streaming=False,
        )

        async def connected():
            return False

        events = [event async for event in pipeline.stream("Original query", connected)]

        self.assertEqual(
            events[-2].data,
            {
                "sources": [],
                "source_status": "cited_with_unrenderable_items",
                "malformed_source_count": 1,
            },
        )

    async def test_one_complete_structured_trace_covers_the_evidence_chain(self):
        records = []
        pipeline = RealPipeline(
            FakeRetriever(), FakeGenerator(), llm_streaming=False,
            corpus_version_value="sha256:test", index_version="local:test",
            telemetry_sink=records.append,
        )

        async def connected():
            return False

        list_events = [
            event async for event in pipeline.stream(
                "Original query", connected, request_id="request-123"
            )
        ]
        self.assertEqual(list_events[-1].event, "done")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["request_id"], "request-123")
        self.assertEqual(record["turn_id"], "request-123")
        self.assertEqual(record["corpus_version"], "sha256:test")
        self.assertEqual(record["retrieval_subqueries"], ["Tesla revenue", "Tesla risk factors"])
        self.assertEqual(record["final_generation_evidence_ids"], ["TSLA-2025-CHUNK-000001"])
        self.assertEqual(record["generated_citation_ids"], ["TSLA-2025-CHUNK-000001"])
        self.assertEqual(record["resolved_used_ids"], ["TSLA-2025-CHUNK-000001"])
        self.assertEqual(record["source_status"], "cited")
        self.assertIsNone(record["safe_error_class"])
        self.assertIn("retrieval_selection", record["stage_latency_ms"])
        self.assertIn("generation", record["stage_latency_ms"])
        self.assertIsNotNone(record["time_to_first_token_ms"])

    async def test_disconnect_is_recorded_without_generation(self):
        records = []
        generator = FakeGenerator()
        pipeline = RealPipeline(FakeRetriever(), generator, telemetry_sink=records.append)

        async def disconnected():
            return True

        events = [event async for event in pipeline.stream("Original query", disconnected)]
        self.assertEqual(events, [])
        self.assertTrue(records[0]["cancelled"])
        self.assertFalse(generator.stream_answer_called)


if __name__ == "__main__":
    unittest.main()
