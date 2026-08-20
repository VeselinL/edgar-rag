import unittest
from types import SimpleNamespace

from src.backend.pipeline import RealPipeline


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

    def plan_retrieval(self, query):
        self.planned_query = query
        return {
            "needs_multiple_retrievals": True,
            "subqueries": ["Tesla revenue", "Tesla risk factors"],
            "operation": None,
        }

    def stream_answer(self, query, evidence):
        self.answer_arguments = (query, evidence)
        yield "Answer [TSLA-2025-CHUNK-000001]"


class RealPipelineTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual(
            [event.event for event in events], ["delta", "sources", "done"]
        )


if __name__ == "__main__":
    unittest.main()
