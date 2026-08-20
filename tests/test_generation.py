import unittest
from types import SimpleNamespace

from src.generation.rag import GenerationService, format_context


class FakeStream:
    def __init__(self, chunks, content_type="text/event-stream"):
        self.chunks = chunks
        self.response = SimpleNamespace(headers={"content-type": content_type})
        self.closed = False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.arguments = None

    def create(self, **kwargs):
        self.arguments = kwargs
        return self.response


def chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


class GenerationTests(unittest.TestCase):
    def evidence(self):
        return [{
            "chunk": {
                "chunk_id": "TSLA-2025-CHUNK-000001",
                "company": "Tesla, Inc.",
                "ticker": "TSLA",
                "filing_date": "2026-01-29",
                "section": "Item 1 — Business",
                "content_type": "narrative",
                "text": "Evidence text.",
            }
        }]

    def test_stream_yields_provider_fragments_without_splitting(self):
        stream = FakeStream([chunk("First "), chunk("fragment"), chunk("")])
        completions = FakeCompletions(stream)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        service = GenerationService(client, model="test-model")
        self.assertEqual(list(service.stream_answer("Question", self.evidence())), ["First ", "fragment"])
        self.assertTrue(stream.closed)
        self.assertTrue(completions.arguments["stream"])

    def test_non_streaming_gateway_response_fails_instead_of_simulating(self):
        stream = FakeStream([], content_type="application/json; charset=utf-8")
        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(stream)))
        with self.assertRaisesRegex(RuntimeError, "did not provide a streaming response"):
            list(GenerationService(client, model="test").stream_answer("Question", self.evidence()))
        self.assertTrue(stream.closed)

    def test_context_uses_exact_internal_identifier(self):
        context = format_context(self.evidence())
        self.assertIn('<source id="TSLA-2025-CHUNK-000001"', context)
        self.assertIn("Evidence text.", context)

    def test_planner_uses_notebook_contract_and_preserves_subqueries(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"needs_multiple_retrievals": true, '
                            '"subqueries": ["Tesla revenue", "Ouster revenue"], '
                            '"operation": "difference"}'
                        )
                    )
                )
            ]
        )
        completions = FakeCompletions(response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        plan = GenerationService(client, model="test-model").plan_retrieval(
            "Compare Tesla and Ouster revenue."
        )

        self.assertEqual(plan["subqueries"], ["Tesla revenue", "Ouster revenue"])
        self.assertEqual(plan["operation"], "difference")
        self.assertFalse(completions.arguments.get("stream", False))
        self.assertEqual(completions.arguments["temperature"], 0.0)

    def test_planner_rejects_invalid_contract(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"subqueries": []}'))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions(response))
        )
        with self.assertRaisesRegex(ValueError, "invalid retrieval plan"):
            GenerationService(client, model="test").plan_retrieval("Question")


if __name__ == "__main__":
    unittest.main()
