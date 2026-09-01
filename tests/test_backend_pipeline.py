import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.backend.pipeline import FILINGS, PipelineSettings, RealPipeline
from src.orchestration.models import EvidenceCalculationPlan, EvidenceOperand
from src.orchestration.routing import RequestRoute, RouteKind, RouteReason
from src.retrieval.evidence_policy import EvidencePolicyError
from src.tools.web_search import WebSearchResponse, WebSearchResult


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
            policy_name="company-balanced-token-aware-v2",
            candidate_counts_by_company=(("TSLA", 10),),
            candidate_counts_by_company_subquery=(("TSLA:0", 10),),
            selected_counts_by_company=(("TSLA", 10),),
            target_counts_by_company=(("TSLA", 10),),
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
            policy_name="company-balanced-token-aware-v2",
            candidate_counts_by_company=(("TSLA", 10),),
            candidate_counts_by_company_subquery=(("TSLA:0", 10),),
            selected_counts_by_company=(("TSLA", 10),),
            target_counts_by_company=(("TSLA", 10),),
            quota_satisfied=True,
            context_input_tokens=100,
            context_input_limit=28_672,
            candidates=(),
            chunk_ids=("TSLA-2025-CHUNK-000099",),
        )


class PolicyErrorRetriever:
    def retrieve(self, *args, **kwargs):
        raise EvidencePolicyError("evidence policy is invalid")


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


class PartialAllCompanyGenerator(FakeGenerator):
    def plan_retrieval(self, query, deterministic_resolution=None):
        self.planned_query = query
        self.deterministic_resolution = deterministic_resolution
        return {
            "needs_multiple_retrievals": False,
            "subqueries": [
                {"query": "Tesla Chief Executive Officer name", "tickers": ["TSLA"]}
            ],
            "operation": None,
            "resolved_tickers": ["TSLA"],
            "company_mentions": [],
            "comparison": False,
            "ambiguity": False,
        }


class ContextAwareGenerator(FakeGenerator):
    def __init__(self):
        super().__init__("Follow-up answer [TSLA-2025-CHUNK-000001]")
        self.planner_context = None
        self.answer_context = None

    def plan_retrieval(self, query, deterministic_resolution=None, conversation_context=""):
        self.planner_context = conversation_context
        return {
            "needs_multiple_retrievals": False,
            "subqueries": [{"query": "Tesla risk factors", "tickers": ["TSLA"]}],
            "operation": None,
            "resolved_tickers": ["TSLA"],
            "company_mentions": [],
            "comparison": False,
            "ambiguity": False,
        }

    def answer(self, query, evidence, *, conversation_context=""):
        self.answer_context = conversation_context
        return self.answer_text


class ContextAwareRetriever(FakeRetriever):
    def retrieve(
        self,
        query,
        subqueries,
        company_resolution=None,
        subquery_targets=None,
        conversation_context="",
    ):
        self.conversation_context = conversation_context
        return super().retrieve(query, subqueries, company_resolution, subquery_targets)


class RoutedGenerator(FakeGenerator):
    def __init__(self, route):
        super().__init__()
        self.route = route
        self.route_context = None

    def route_request(self, query, deterministic_resolution=None, conversation_context=""):
        self.deterministic_resolution = deterministic_resolution
        self.route_context = conversation_context
        return self.route

    def plan_retrieval(self, query, deterministic_resolution=None):
        raise AssertionError("Non-filing routes must not invoke the filing planner.")


class FilingCalculationGenerator(FakeGenerator):
    def __init__(self, *, ready=True):
        super().__init__()
        self.ready = ready

    def route_request(self, query, deterministic_resolution=None, conversation_context=""):
        return RequestRoute(
            RouteKind.FILING_AND_CALCULATOR,
            RouteReason.EVIDENCE_ARITHMETIC,
            arithmetic_required=True,
        )

    def plan_retrieval(self, query, deterministic_resolution=None):
        return {
            "needs_multiple_retrievals": False,
            "subqueries": [{"query": "Tesla values", "tickers": ["TSLA"]}],
            "operation": "difference",
            "resolved_tickers": ["TSLA"],
            "company_mentions": [],
            "comparison": True,
            "ambiguity": False,
        }

    def plan_evidence_calculation(self, query, evidence, operation):
        if not self.ready:
            return EvidenceCalculationPlan(
                "missing", operation, (), None, None, "missing_operand"
            )
        source_id = "TSLA-2025-CHUNK-000001"
        return EvidenceCalculationPlan(
            "ready",
            operation,
            (
                EvidenceOperand("first", "100", "100", "USD millions", (source_id,)),
                EvidenceOperand("second", "80", "80", "USD millions", (source_id,)),
            ),
            "USD millions",
            None,
            None,
        )

    def answer(self, query, evidence):
        raise AssertionError("Evidence calculations must not use answer generation.")

    def stream_answer(self, query, evidence):
        raise AssertionError("Evidence calculations must not use answer generation.")


class FakeWebSearch:
    provider = "test-web"

    def __init__(self):
        self.query = None

    def search(self, query, *, max_results=5):
        self.query = query
        return WebSearchResponse(
            query,
            self.provider,
            (
                WebSearchResult(
                    "web-1",
                    "Current report",
                    "https://example.com/report",
                    "example.com",
                    "2026-09-01T00:00:00+00:00",
                    "TSLA was 250 and GM was 60 at the stated time.",
                ),
            ),
        )

    def close(self):
        return None


class WebGenerator(RoutedGenerator):
    def __init__(self, route=None):
        super().__init__(
            route
            or RequestRoute(RouteKind.WEB_SEARCH, RouteReason.CURRENT_OR_EXTERNAL)
        )

    def web_answer_with_metadata(self, query, evidence):
        return SimpleNamespace(
            text="The current report provides the requested update [web-1].",
            usage={"total_tokens": 10},
        )

    def plan_evidence_calculation(self, query, evidence, operation, source_kind):
        self.source_kind = source_kind
        return EvidenceCalculationPlan(
            "ready",
            operation,
            (
                EvidenceOperand("TSLA", "250", "250", "USD", ("web-1",)),
                EvidenceOperand("GM", "60", "60", "USD", ("web-1",)),
            ),
            "USD",
            None,
            None,
        )


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

    def test_settings_can_disable_calculator(self):
        with patch.dict(
            "os.environ", {"AVA_CALCULATOR_ENABLED": "false"}, clear=False
        ):
            settings = PipelineSettings.from_environment()
        self.assertFalse(settings.calculator_enabled)

    def test_settings_reject_ambiguous_calculator_toggle(self):
        with patch.dict(
            "os.environ", {"AVA_CALCULATOR_ENABLED": "off"}, clear=False
        ):
            with self.assertRaisesRegex(ValueError, "AVA_CALCULATOR_ENABLED"):
                PipelineSettings.from_environment()

    def test_settings_require_explicit_configured_web_provider(self):
        with patch.dict(
            "os.environ",
            {
                "AVA_WEB_SEARCH_ENABLED": "true",
                "AVA_WEB_SEARCH_PROVIDER": "brave",
                "BRAVE_SEARCH_API_KEY": "test-key",
                "AVA_WEB_SEARCH_TIMEOUT_SECONDS": "6",
                "AVA_WEB_SEARCH_MAX_RESULTS": "4",
            },
            clear=False,
        ):
            settings = PipelineSettings.from_environment()
        self.assertTrue(settings.web_search_enabled)
        self.assertEqual(settings.web_search_provider, "brave")
        self.assertEqual(settings.web_search_max_results, 4)

        with patch.dict(
            "os.environ",
            {
                "AVA_WEB_SEARCH_ENABLED": "true",
                "AVA_WEB_SEARCH_PROVIDER": "disabled",
                "BRAVE_SEARCH_API_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "requires"):
                PipelineSettings.from_environment()

    def test_settings_load_project_dotenv_before_reading_values(self):
        with patch("src.backend.pipeline.dotenv.load_dotenv") as load_dotenv:
            PipelineSettings.from_environment()

        load_dotenv.assert_called_once()

    def test_settings_reject_ambiguous_streaming_value(self):
        with patch.dict("os.environ", {"AVA_LLM_STREAMING": "off"}, clear=False):
            with self.assertRaisesRegex(ValueError, "AVA_LLM_STREAMING"):
                PipelineSettings.from_environment()

    def test_settings_read_typed_token_and_observability_budgets(self):
        with patch.dict(
            "os.environ",
            {
                "AVA_LLM_CONTEXT_WINDOW_TOKENS": "65536",
                "AVA_LLM_RESERVED_OUTPUT_TOKENS": "8192",
                "AVA_OBSERVABILITY_RETENTION_DAYS": "14",
            },
            clear=False,
        ):
            settings = PipelineSettings.from_environment()
        self.assertEqual(settings.context_window_tokens, 65_536)
        self.assertEqual(settings.reserved_output_tokens, 8_192)
        self.assertEqual(settings.observability_retention_days, 14)

    def test_settings_read_qdrant_shadow_configuration(self):
        with patch.dict(
            "os.environ",
            {
                "AVA_QDRANT_MODE": "shadow",
                "QDRANT_URL": "http://127.0.0.1:6333",
                "QDRANT_COLLECTION_ALIAS": "ava_test_current",
                "QDRANT_TIMEOUT_SECONDS": "12",
            },
            clear=False,
        ):
            settings = PipelineSettings.from_environment()
        self.assertEqual(settings.qdrant_mode, "shadow")
        self.assertEqual(settings.qdrant_collection_alias, "ava_test_current")
        self.assertEqual(settings.qdrant_timeout_seconds, 12)

    def test_settings_reject_unknown_qdrant_mode(self):
        with patch.dict(
            "os.environ", {"AVA_QDRANT_MODE": "fallback"}, clear=False
        ):
            with self.assertRaisesRegex(ValueError, "AVA_QDRANT_MODE"):
                PipelineSettings.from_environment()

    def test_configured_unavailable_qdrant_makes_real_pipeline_unready(self):
        chunks = [
            {
                "chunk_id": "TSLA-1",
                "ticker": "TSLA",
                "text": "Tesla evidence.",
            }
        ]
        with (
            patch(
                "src.backend.pipeline.load_corpus",
                return_value=(np.eye(1, 768, dtype="float32"), chunks),
            ),
            patch("src.backend.pipeline.build_bm25_index", return_value=object()),
            patch("src.backend.pipeline.SentenceTransformer", return_value=object()),
            patch("src.backend.pipeline.make_llm_client", return_value=object()),
            patch(
                "src.backend.pipeline.make_client",
                side_effect=ConnectionError("qdrant is down"),
            ),
        ):
            pipeline = RealPipeline.build(
                PipelineSettings(qdrant_mode="shadow")
            )
        self.assertFalse(pipeline.ready)
        self.assertEqual(pipeline.mode, "real")
        self.assertEqual(pipeline.qdrant_health["status"], "unavailable")
        self.assertEqual(
            pipeline.qdrant_health["safe_error_class"], "provider_transport_error"
        )

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

    async def test_greeting_route_returns_no_sources_without_retrieval(self):
        retriever = FakeRetriever()
        generator = RoutedGenerator(
            RequestRoute(
                RouteKind.CONVERSATION_ONLY,
                RouteReason.GREETING,
                decided_by="deterministic",
            )
        )
        records = []
        pipeline = RealPipeline(retriever, generator, telemetry_sink=records.append)

        async def connected():
            return False

        events = [event async for event in pipeline.stream("Hello", connected)]

        self.assertIsNone(retriever.arguments)
        self.assertFalse(generator.answer_called)
        self.assertEqual([event.event for event in events], ["delta", "sources", "done"])
        self.assertIn("Hello", events[0].data["text"])
        self.assertEqual(events[1].data["sources"], [])
        self.assertEqual(records[0]["route"]["route"], "conversation_only")
        self.assertNotIn("retrieval_selection", records[0]["stage_latency_ms"])

    async def test_web_route_does_not_receive_arbitrary_filing_chunks(self):
        retriever = FakeRetriever()
        generator = RoutedGenerator(
            RequestRoute(RouteKind.WEB_SEARCH, RouteReason.CURRENT_OR_EXTERNAL)
        )
        pipeline = RealPipeline(retriever, generator)

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "What is Tesla's stock price today?", connected
            )
        ]

        self.assertIsNone(retriever.arguments)
        self.assertIn("web search is disabled", events[0].data["text"])
        self.assertEqual(events[1].data["sources"], [])

    async def test_enabled_web_route_generates_only_from_cited_search_results(self):
        retriever = FakeRetriever()
        web_search = FakeWebSearch()
        records = []
        pipeline = RealPipeline(
            retriever,
            WebGenerator(),
            llm_streaming=False,
            web_search=web_search,
            web_search_enabled=True,
            telemetry_sink=records.append,
        )

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream("What happened today?", connected)
        ]
        self.assertIsNone(retriever.arguments)
        self.assertEqual(web_search.query, "What happened today?")
        self.assertEqual(events[1].data["sources"][0]["content_type"], "web")
        self.assertEqual(events[1].data["sources"][0]["publisher"], "example.com")
        self.assertEqual(records[0]["tool_executions"][0]["tool"], "web_search")

    async def test_web_calculation_searches_then_executes_calculator(self):
        route = RequestRoute(
            RouteKind.WEB_AND_CALCULATOR,
            RouteReason.EVIDENCE_ARITHMETIC,
            arithmetic_required=True,
        )
        generator = WebGenerator(route)
        records = []
        pipeline = RealPipeline(
            FakeRetriever(),
            generator,
            web_search=FakeWebSearch(),
            web_search_enabled=True,
            telemetry_sink=records.append,
        )

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "Using today's prices, calculate the difference between TSLA and GM.",
                connected,
            )
        ]
        self.assertIn("difference is 190 USD", events[0].data["text"])
        self.assertEqual(generator.source_kind, "web")
        self.assertEqual(
            [record["tool"] for record in records[0]["tool_executions"]],
            ["web_search", "calculator"],
        )

    async def test_calculation_route_executes_calculator_without_retrieval_or_model(self):
        retriever = FakeRetriever()
        generator = RoutedGenerator(
            RequestRoute(
                RouteKind.CALCULATOR,
                RouteReason.PURE_ARITHMETIC,
                arithmetic_required=True,
            )
        )
        pipeline = RealPipeline(retriever, generator)

        async def connected():
            return False

        events = [event async for event in pipeline.stream("12 * 4", connected)]

        self.assertIsNone(retriever.arguments)
        self.assertFalse(generator.answer_called)
        self.assertEqual(events[0].data["text"], "12 * 4 = 48")
        self.assertEqual(events[1].data["sources"], [])

    async def test_disabled_calculator_fails_closed_without_model_arithmetic(self):
        retriever = FakeRetriever()
        generator = RoutedGenerator(
            RequestRoute(
                RouteKind.CALCULATOR,
                RouteReason.PURE_ARITHMETIC,
                arithmetic_required=True,
            )
        )
        records = []
        pipeline = RealPipeline(
            retriever,
            generator,
            calculator_enabled=False,
            telemetry_sink=records.append,
        )

        async def connected():
            return False

        events = [event async for event in pipeline.stream("12 * 4", connected)]
        self.assertIn("disabled", events[0].data["text"])
        self.assertIn("won't guess", events[0].data["text"])
        self.assertEqual(records[0]["tool_executions"], [])

    async def test_filing_calculation_retrieves_then_executes_cited_calculator(self):
        records = []
        pipeline = RealPipeline(
            FakeRetriever(),
            FilingCalculationGenerator(),
            telemetry_sink=records.append,
        )

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "Calculate the difference between Tesla's two values.", connected
            )
        ]
        self.assertIn("difference is 20 USD millions", events[0].data["text"])
        self.assertEqual(len(events[1].data["sources"]), 1)
        self.assertEqual(records[0]["tool_executions"][0]["status"], "succeeded")
        self.assertTrue(records[0]["tool_executions"][0]["evidence_derived"])
        self.assertNotIn("generation", records[0]["stage_latency_ms"])

    async def test_filing_calculation_abstains_when_operand_is_missing(self):
        records = []
        pipeline = RealPipeline(
            FakeRetriever(),
            FilingCalculationGenerator(ready=False),
            telemetry_sink=records.append,
        )

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "Calculate the difference between Tesla's two values.", connected
            )
        ]
        self.assertIn("does not provide unambiguous", events[0].data["text"])
        self.assertEqual(events[1].data["sources"], [])
        self.assertEqual(
            records[0]["tool_executions"][0]["status"], "not_executed"
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
        self.assertIn("configured filing-evidence policy", events[0].data["text"])
        self.assertEqual(events[1].data["sources"], [])

    async def test_each_company_plan_preserves_scope_before_policy_failure(self):
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
        self.assertIn("configured filing-evidence policy", events[0].data["text"])
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
                "Tesla Chief Executive Officer",
                "Mobileye Chief Executive Officer",
            ],
        )
        self.assertEqual([event.event for event in events], ["delta", "sources", "done"])

    async def test_planner_subset_of_all_companies_continues_to_generation(self):
        retriever = FakeRetriever()
        generator = PartialAllCompanyGenerator()
        pipeline = RealPipeline(retriever, generator, llm_streaming=False)

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "Who are the CEOs of all companies?", connected
            )
        ]

        self.assertEqual(
            generator.deterministic_resolution.resolved_tickers, tuple(FILINGS)
        )
        self.assertEqual(retriever.arguments[2].resolved_tickers, ("TSLA",))
        self.assertEqual(retriever.arguments[3], [["TSLA"]])
        self.assertEqual([event.event for event in events], ["delta", "sources", "done"])

    async def test_follow_up_context_reaches_planner_packing_and_generation_separately(self):
        retriever = ContextAwareRetriever()
        generator = ContextAwareGenerator()
        traces = []
        pipeline = RealPipeline(
            retriever, generator, llm_streaming=False, telemetry_sink=traces.append
        )
        context = SimpleNamespace(
            prompt_text=lambda: "Recent conversation turns (not filing evidence):\nUser: Tell me about Tesla.",
            short_term_ids=("message-1", "message-2"),
            long_term_ids=("memory-1",),
        )

        async def connected():
            return False

        events = [
            event
            async for event in pipeline.stream(
                "What about its risks?",
                connected,
                conversation_context=context,
                conversation_id="conversation-1",
                turn_id="turn-2",
            )
        ]

        self.assertIn("Tell me about Tesla", generator.planner_context)
        self.assertEqual(retriever.conversation_context, generator.planner_context)
        self.assertEqual(generator.answer_context, generator.planner_context)
        self.assertEqual(traces[0]["conversation_id"], "conversation-1")
        self.assertEqual(traces[0]["turn_id"], "turn-2")
        self.assertEqual(traces[0]["short_term_memory_ids"], ["message-1", "message-2"])
        self.assertEqual(traces[0]["long_term_memory_ids"], ["memory-1"])
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
