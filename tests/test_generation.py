import json
import unittest
from types import SimpleNamespace

from src.filings.corpus import ACTIVE_FILINGS
from src.resolution.companies import default_company_resolver
from src.generation.rag import (
    PLANNER_INSTRUCTION,
    ProviderCircuitBreaker,
    ProviderCircuitOpenError,
    SYSTEM_PROMPT,
    GenerationService,
    citation_ids,
    count_generation_input_tokens,
    format_context,
    generation_messages,
    provider_usage,
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
        self.assertEqual(completions.arguments["max_tokens"], 4096)

    def test_non_streaming_gateway_response_fails_instead_of_simulating(self):
        stream = FakeStream([], content_type="application/json; charset=utf-8")
        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(stream)))
        with self.assertRaisesRegex(RuntimeError, "did not provide a streaming response"):
            list(GenerationService(client, model="test").stream_answer("Question", self.evidence()))
        self.assertTrue(stream.closed)

    def test_buffered_answer_uses_non_streaming_provider_call(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Complete answer"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
        )
        completions = FakeCompletions(response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        answer = GenerationService(client, model="test").answer(
            "Question", self.evidence()
        )

        self.assertEqual(answer, "Complete answer")
        self.assertFalse(completions.arguments.get("stream", False))
        self.assertEqual(completions.arguments["max_tokens"], 4096)

        result = GenerationService(client, model="test").answer_with_metadata(
            "Question", self.evidence()
        )
        self.assertEqual(result.usage, {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})

    def test_stream_metadata_captures_terminal_provider_usage(self):
        terminal = SimpleNamespace(
            choices=[],
            usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        )
        stream = FakeStream([chunk("answer"), terminal])
        service = GenerationService(
            SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(stream))),
            model="test",
        )
        measured = service.stream_answer_with_metadata("Question", self.evidence())
        self.assertEqual(list(measured), ["answer"])
        self.assertEqual(measured.usage["total_tokens"], 25)

    def test_provider_usage_ignores_non_numeric_and_unknown_fields(self):
        self.assertEqual(
            provider_usage({"prompt_tokens": 5, "secret": "never", "total_tokens": "5"}),
            {"prompt_tokens": 5},
        )

    def test_provider_circuit_opens_after_consecutive_failures_and_resets(self):
        breaker = ProviderCircuitBreaker(failure_threshold=2, recovery_seconds=60)
        breaker.before_request()
        breaker.record_failure()
        breaker.before_request()
        breaker.record_failure()
        with self.assertRaises(ProviderCircuitOpenError):
            breaker.before_request()
        breaker.record_success()
        breaker.before_request()

    def test_context_uses_exact_internal_identifier(self):
        context = format_context(self.evidence())
        self.assertIn('<source id="TSLA-2025-CHUNK-000001"', context)
        self.assertIn("Evidence text.", context)

    def test_generation_token_count_covers_complete_formatted_messages(self):
        without_evidence = count_generation_input_tokens("Question", [])
        with_evidence = count_generation_input_tokens("Question", self.evidence())
        self.assertGreater(with_evidence, without_evidence)
        self.assertGreater(
            with_evidence, len(format_context(self.evidence()).split())
        )

    def test_conversation_context_is_separate_and_included_in_token_count(self):
        history = "Recent conversation turns (not filing evidence):\nUser: Tell me about Tesla."
        without_history = count_generation_input_tokens(
            "What about its risks?", self.evidence()
        )
        with_history = count_generation_input_tokens(
            "What about its risks?",
            self.evidence(),
            conversation_context=history,
        )
        messages = generation_messages(
            "What about its risks?",
            self.evidence(),
            conversation_context=history,
        )

        self.assertGreater(with_history, without_history)
        self.assertIn("Conversation context (not SEC evidence", messages[1]["content"])
        self.assertIn("Retrieved filing excerpts", messages[1]["content"])

    def test_ceo_and_coo_are_expanded_correctly(self):
        for prompt in (SYSTEM_PROMPT, PLANNER_INSTRUCTION):
            self.assertIn("CEO means Chief Executive Officer", prompt)
            self.assertIn("COO means Chief Operating Officer", prompt)
            self.assertNotIn("CEO, that means Chief Operating Officer", prompt)
        self.assertIn("Ford Chief Executive Officer name", PLANNER_INSTRUCTION)

    def test_prompt_requires_exact_citations_on_concluding_synthesis(self):
        self.assertIn("concluding comparison or synthesis", SYSTEM_PROMPT)
        self.assertIn("never add `$`", SYSTEM_PROMPT)

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
        self.assertIn("detected_ticker_hints", planner_messages[2]["content"])
        self.assertIn("unresolved_mentions", planner_messages[2]["content"])
        self.assertIn("semantic comparison, not company count", PLANNER_INSTRUCTION)
        self.assertIn("CEO of Tesla", PLANNER_INSTRUCTION)

    def test_planner_rejects_invalid_contract(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"subqueries": []}'))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions(response))
        )
        with self.assertRaisesRegex(ValueError, "invalid retrieval plan"):
            GenerationService(client, model="test").plan_retrieval("Question")

    def test_planner_rejects_target_coverage_disagreement(self):
        invalid_plans = (
            (
                '{"needs_multiple_retrievals": true, "subqueries": ['
                '{"query": "Tesla CEO", "tickers": ["TSLA"]}, '
                '{"query": "Mobileye CEO", "tickers": []}], '
                '"operation": null, "resolved_tickers": ["TSLA", "MBLY"], '
                '"company_mentions": [], "comparison": false, "ambiguity": false}'
            ),
        )
        for payload in invalid_plans:
            with self.subTest(payload=payload):
                response = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
                )
                client = SimpleNamespace(
                    chat=SimpleNamespace(completions=FakeCompletions(response))
                )
                with self.assertRaisesRegex(ValueError, "invalid retrieval plan"):
                    GenerationService(client, model="test").plan_retrieval(
                        "Who is the CEO of Tesla and Mobileye?"
                    )

    def test_planner_normalizes_redundant_multiplicity_from_subquery_count(self):
        cases = (
            (
                '{"needs_multiple_retrievals": true, '
                '"subqueries": [{"query": "Ford revenue", "tickers": ["F"]}], '
                '"operation": null, "resolved_tickers": ["F"], '
                '"company_mentions": [{"raw_text": "Ford", "ticker": "F"}], '
                '"comparison": false, "ambiguity": false}',
                False,
            ),
            (
                '{"needs_multiple_retrievals": false, "subqueries": ['
                '{"query": "Tesla CEO", "tickers": ["TSLA"]}, '
                '{"query": "Mobileye CEO", "tickers": ["MBLY"]}], '
                '"operation": null, "resolved_tickers": ["TSLA", "MBLY"], '
                '"company_mentions": [], "comparison": false, "ambiguity": false}',
                True,
            ),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                response = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
                )
                client = SimpleNamespace(
                    chat=SimpleNamespace(completions=FakeCompletions(response))
                )

                plan = GenerationService(client, model="test").plan_retrieval(
                    "Planner multiplicity test"
                )

                self.assertIs(plan["needs_multiple_retrievals"], expected)
                self.assertEqual(
                    plan["_normalizations"], ["retrieval_multiplicity"]
                )

    def test_planner_repairs_empty_single_query_with_original_text(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"needs_multiple_retrievals": false, "subqueries": [], '
                '"operation": null, "resolved_tickers": [], "company_mentions": [], '
                '"comparison": false, "ambiguity": false}'
            )))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions(response))
        )
        plan = GenerationService(client, model="test").plan_retrieval(
            "What risks are common in these filings?"
        )
        self.assertEqual(
            plan["subqueries"],
            [{"query": "What risks are common in these filings?", "tickers": []}],
        )
        self.assertEqual(
            plan["_normalizations"], ["single_query_empty_subqueries"]
        )

    def test_planner_can_choose_empty_scope_despite_deterministic_hint(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"needs_multiple_retrievals": false, "subqueries": [], '
                '"operation": null, "resolved_tickers": [], "company_mentions": [], '
                '"comparison": false, "ambiguity": false}'
            )))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions(response))
        )
        resolution = default_company_resolver.resolve("What are Tesla's risks?")
        plan = GenerationService(client, model="test").plan_retrieval(
            "What are Tesla's risks?", resolution
        )
        self.assertEqual(plan["resolved_tickers"], [])
        self.assertEqual(
            plan["subqueries"], [{"query": "What are Tesla's risks?", "tickers": []}]
        )

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

    def test_planner_removes_empty_full_corpus_mention_echo(self):
        tickers = list(ACTIVE_FILINGS)
        payload = json.dumps(
            {
                "needs_multiple_retrievals": False,
                "subqueries": [{"query": "CEO names", "tickers": tickers}],
                "operation": None,
                "resolved_tickers": tickers,
                "company_mentions": [
                    {"raw_text": "all companies", "ticker": ""}
                ],
                "comparison": False,
                "ambiguity": False,
            }
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions(response))
        )

        plan = GenerationService(client, model="test").plan_retrieval(
            "Who are the CEOs of all companies?"
        )

        self.assertEqual(plan["company_mentions"], [])
        self.assertIn("empty_full_corpus_mention", plan["_normalizations"])

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
