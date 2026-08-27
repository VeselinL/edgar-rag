import unittest
from types import SimpleNamespace

from src.generation.rag import (
    PLANNER_INSTRUCTION,
    SYSTEM_PROMPT,
    GenerationService,
    citation_ids,
    format_context,
)


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

    def test_buffered_answer_uses_non_streaming_provider_call(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Complete answer"))]
        )
        completions = FakeCompletions(response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        answer = GenerationService(client, model="test").answer(
            "Question", self.evidence()
        )

        self.assertEqual(answer, "Complete answer")
        self.assertFalse(completions.arguments.get("stream", False))

    def test_context_uses_exact_internal_identifier(self):
        context = format_context(self.evidence())
        self.assertIn('<source id="TSLA-2025-CHUNK-000001"', context)
        self.assertIn("Evidence text.", context)

    def test_ceo_and_coo_are_expanded_correctly(self):
        for prompt in (SYSTEM_PROMPT, PLANNER_INSTRUCTION):
            self.assertIn("CEO means Chief Executive Officer", prompt)
            self.assertIn("COO means Chief Operating Officer", prompt)
            self.assertNotIn("CEO, that means Chief Operating Officer", prompt)

    def test_citation_ids_accept_grouped_ids_without_matching_prose(self):
        answer = (
            "Supported [TSLA-2025-CHUNK-000001; TSLA-2025-CHUNK-000002] "
            "and [TSLA-2025-CHUNK-000002, TSLA-2025-CHUNK-000003], "
            "but ignore [not a citation]."
        )

        self.assertEqual(
            citation_ids(answer),
            [
                "TSLA-2025-CHUNK-000001",
                "TSLA-2025-CHUNK-000002",
                "TSLA-2025-CHUNK-000003",
            ],
        )

    def test_planner_uses_notebook_contract_and_preserves_subqueries(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"needs_multiple_retrievals": true, '
                            '"subqueries": [{"query": "Tesla revenue", "tickers": ["TSLA"]}, '
                            '{"query": "Ouster revenue", "tickers": ["OUST"]}], '
                            '"operation": "difference", '
                            '"resolved_tickers": ["TSLA", "OUST"], '
                            '"company_mentions": [], "comparison": true, '
                            '"ambiguity": false}'
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

        self.assertEqual(
            plan["subqueries"],
            [
                {"query": "Tesla revenue", "tickers": ["TSLA"]},
                {"query": "Ouster revenue", "tickers": ["OUST"]},
            ],
        )
        self.assertEqual(plan["operation"], "difference")
        self.assertFalse(completions.arguments.get("stream", False))
        self.assertEqual(completions.arguments["temperature"], 0.0)
        planner_messages = completions.arguments["messages"]
        self.assertIn("Allowed corpus tickers", planner_messages[1]["content"])
        self.assertIn("deterministic_resolved_tickers", planner_messages[2]["content"])
        self.assertIn("unresolved_mentions", planner_messages[2]["content"])

    def test_planner_rejects_invalid_contract(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"subqueries": []}'))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions(response))
        )
        with self.assertRaisesRegex(ValueError, "invalid retrieval plan"):
            GenerationService(client, model="test").plan_retrieval("Question")

    def test_planner_rejects_out_of_corpus_ticker(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"needs_multiple_retrievals": false, '
                '"subqueries": [{"query": "Toyota", "tickers": ["TM"]}], '
                '"operation": null, "resolved_tickers": ["TM"], '
                '"company_mentions": [], "comparison": false, "ambiguity": false}'
            )))]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))
        with self.assertRaisesRegex(ValueError, "invalid retrieval plan"):
            GenerationService(client, model="test").plan_retrieval("Toyota")

    def test_planner_rejects_non_string_ticker_without_type_error(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"needs_multiple_retrievals": false, '
                '"subqueries": [{"query": "Tesla", "tickers": [{}]}], '
                '"operation": null, "resolved_tickers": [], '
                '"company_mentions": [], "comparison": false, "ambiguity": false}'
            )))]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))
        with self.assertRaisesRegex(ValueError, "invalid retrieval plan"):
            GenerationService(client, model="test").plan_retrieval("Tesla")

    def test_planner_normalizes_provider_string_null_operation(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"needs_multiple_retrievals": false, '
                '"subqueries": [{"query": "Tesla", "tickers": ["TSLA"]}], '
                '"operation": "null", "resolved_tickers": ["TSLA"], '
                '"company_mentions": [], "comparison": false, "ambiguity": false}'
            )))]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))
        plan = GenerationService(client, model="test").plan_retrieval("Tesla")
        self.assertIsNone(plan["operation"])

    def test_planner_normalizes_provider_comparison_labels(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"needs_multiple_retrievals": true, '
                '"subqueries": [{"query": "Tesla", "tickers": ["TSLA"]}, '
                '{"query": "Ford", "tickers": ["F"]}], '
                '"operation": "comparison", "resolved_tickers": ["TSLA", "F"], '
                '"company_mentions": [], "comparison": "comparison", "ambiguity": false}'
            )))]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))
        plan = GenerationService(client, model="test").plan_retrieval("Compare Tesla and Ford")
        self.assertIsNone(plan["operation"])
        self.assertTrue(plan["comparison"])


if __name__ == "__main__":
    unittest.main()
