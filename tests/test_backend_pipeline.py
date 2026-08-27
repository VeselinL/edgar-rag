import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.backend.pipeline import FILINGS, PipelineSettings, RealPipeline


class FakeRetriever:
    def __init__(self):
        self.arguments = None

    def retrieve(self, query, subqueries):
        self.arguments = (query, subqueries)
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
            )
        )


class FakeGenerator:
    def __init__(self):
        self.planned_query = None
        self.answer_arguments = None
        self.stream_answer_called = False
        self.answer_called = False

    def plan_retrieval(self, query):
        self.planned_query = query
        return {
            "needs_multiple_retrievals": True,
            "subqueries": ["Tesla revenue", "Tesla risk factors"],
            "operation": None,
        }

    def stream_answer(self, query, evidence):
        self.stream_answer_called = True
        self.answer_arguments = (query, evidence)
        yield "Answer [TSLA-2025-CHUNK-000001]"

    def answer(self, query, evidence):
        self.answer_called = True
        self.answer_arguments = (query, evidence)
        return "Buffered answer [TSLA-2025-CHUNK-000001]"


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

    async def test_planner_subqueries_drive_shared_retrieval_before_streaming(self):
        retriever = FakeRetriever()
        generator = FakeGenerator()
        pipeline = RealPipeline(retriever, generator)

        async def connected():
            return False

        events = [event async for event in pipeline.stream("Original query", connected)]

        self.assertEqual(generator.planned_query, "Original query")
        self.assertEqual(
            retriever.arguments,
            ("Original query", ["Tesla revenue", "Tesla risk factors"]),
        )
        self.assertEqual(generator.answer_arguments[0], "Original query")
        self.assertTrue(generator.stream_answer_called)
        self.assertFalse(generator.answer_called)
        self.assertEqual(
            [event.event for event in events], ["delta", "sources", "done"]
        )

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
                        "citation_fallback": False,
                        "malformed_source_count": 0,
                    },
                ),
                ("done", {}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
