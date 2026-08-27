import unittest
from unittest.mock import patch

from src.generation.rag import resolve_cited_evidence
from src.retrieval.evidence_policy import EvidenceBudgetPolicy, EvidencePackingError
from src.retrieval.scope_aware import (
    deduplicate_results,
    detect_companies,
    detect_scope,
    retrieve_generation_context,
    scope_aware_hybrid_retrieve,
    select_final_evidence,
)


def result(chunk_id: str, ticker: str, score: float = 1.0) -> dict:
    return {
        "chunk_id": chunk_id,
        "ticker": ticker,
        "index": 0,
        "rrf_score": score,
        "dense_rank": 1,
        "bm25_rank": 1,
    }


def hydrated(chunk_id: str, ticker: str, rerank: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "rerank": rerank,
        "chunk": {
            "chunk_id": chunk_id,
            "ticker": ticker,
            "text": chunk_id,
        },
    }


class CompanyDetectionTests(unittest.TestCase):
    def test_supported_company_name(self):
        self.assertEqual(detect_scope("What are Tesla's main risks?"), ("single_company", ["TSLA"]))

    def test_ticker(self):
        self.assertEqual(detect_companies("Summarize NVDA revenue risks."), ["NVDA"])

    def test_existing_alias(self):
        self.assertEqual(detect_companies("How is EyeQ used?"), ["MBLY"])

    def test_rivian_name_and_ticker(self):
        self.assertEqual(detect_companies("What vehicles does Rivian make?"), ["RIVN"])
        self.assertEqual(detect_companies("Summarize RIVN revenue."), ["RIVN"])

    def test_rivian_automotive_alias(self):
        self.assertEqual(
            detect_scope("What are Rivian Automotive's main risks?"),
            ("single_company", ["RIVN"]),
        )

    def test_two_company_comparison(self):
        scope, companies = detect_scope("Compare Tesla with Ouster revenue.")
        self.assertEqual(scope, "explicit_subset")
        self.assertEqual(companies, ["TSLA", "OUST"])

    def test_existing_comparison_cue(self):
        self.assertEqual(
            detect_scope("How does Tesla compare with other companies across the industry?"),
            ("anchored_global", ["TSLA"]),
        )

    def test_global_query_without_company(self):
        self.assertEqual(detect_scope("What risks are common in these filings?"), ("global", []))


class EvidenceSelectionTests(unittest.TestCase):
    def test_candidate_deduplication(self):
        first = result("A", "TSLA")
        duplicate = result("A", "TSLA", 0.5)
        second = result("B", "OUST")
        self.assertEqual(
            [item["chunk_id"] for item in deduplicate_results([first, duplicate, second])],
            ["A", "B"],
        )

    def test_final_context_budget(self):
        candidates = [hydrated(f"C-{index}", "TSLA", index) for index in range(20)]
        selected = select_final_evidence(candidates, 10)
        self.assertEqual(len(selected), 10)
        self.assertEqual(len({item["chunk"]["chunk_id"] for item in selected}), 10)

    def test_comparison_keeps_every_available_target(self):
        reranked = [
            *[hydrated(f"TSLA-{index}", "TSLA", index) for index in range(1, 11)],
            hydrated("OUST-1", "OUST", 11),
        ]
        selected = select_final_evidence(reranked, 10, ("TSLA", "OUST"))
        self.assertEqual({item["chunk"]["ticker"] for item in selected}, {"TSLA", "OUST"})
        self.assertEqual(len(selected), 10)

    def test_explicit_comparison_uses_combined_company_scope(self):
        observed_scopes = []

        def fake_hybrid(*args, allowed_tickers=None, **kwargs):
            observed_scopes.append(allowed_tickers)
            return [
                result("TSLA-1", "TSLA"),
                result("OUST-1", "OUST"),
            ]

        with patch("src.retrieval.scope_aware.hybrid_retrieve", side_effect=fake_hybrid):
            retrieved, scope, companies = scope_aware_hybrid_retrieve(
                "Compare Tesla and Ouster",
                model=object(),
                query_prefix="",
                normalized_embeddings=object(),
                bm25_retriever=object(),
                all_chunks=[],
                top_k=2,
            )
        self.assertEqual(scope, "explicit_subset")
        self.assertEqual(companies, ["TSLA", "OUST"])
        self.assertEqual({item["ticker"] for item in retrieved}, {"TSLA", "OUST"})
        self.assertEqual(observed_scopes, [{"TSLA", "OUST"}])

    def test_planned_context_keeps_five_per_company_and_five_supplemental(self):
        def fake_hybrid_retrieve(query, *args, allowed_tickers=None, **kwargs):
            ticker = "TSLA" if "Tesla" in query else "OUST"
            self.assertEqual(allowed_tickers, {ticker})
            candidates = [
                result(f"{ticker}-{position}", ticker, 0.03 - position / 10_000)
                for position in range(10)
            ]
            return candidates

        with patch(
            "src.retrieval.scope_aware.hybrid_retrieve",
            side_effect=fake_hybrid_retrieve,
        ):
            diagnostics = retrieve_generation_context(
                original_query="Compare Tesla and Ouster revenue.",
                subqueries=["Tesla revenue", "Ouster revenue"],
                model=object(),
                query_prefix="",
                normalized_embeddings=object(),
                bm25_retriever=object(),
                all_chunks=[],
            )

        self.assertEqual(len(diagnostics["selected_chunk_ids"]), 15)
        self.assertGreaterEqual(diagnostics["coverage_by_subquery"][0], 5)
        self.assertGreaterEqual(diagnostics["coverage_by_subquery"][1], 5)
        self.assertTrue(all(value >= 2 for value in diagnostics["coverage_by_subquery"]))
        self.assertEqual(len(set(diagnostics["selected_chunk_ids"])), 15)
        self.assertEqual(
            sum(diagnostics["selected_counts_by_company"].values()), 15
        )
        self.assertTrue(
            all(
                count >= 5
                for count in diagnostics["selected_counts_by_company"].values()
            )
        )

    def test_planned_context_deduplicates_and_rewards_multi_subquery_matches(self):
        shared = result("SHARED", "TSLA", 0.01)

        def fake_scope_retrieve(query, *args, **kwargs):
            ticker = "TSLA" if query == "first" else "OUST"
            return [
                shared,
                result(f"{ticker}-1", ticker, 0.009),
                result(f"{ticker}-2", ticker, 0.008),
            ], "global", []

        with patch(
            "src.retrieval.scope_aware.scope_aware_hybrid_retrieve",
            side_effect=fake_scope_retrieve,
        ):
            diagnostics = retrieve_generation_context(
                original_query="A global multi-fact question",
                subqueries=["first", "second"],
                model=object(),
                query_prefix="",
                normalized_embeddings=object(),
                bm25_retriever=object(),
                all_chunks=[],
            )

        shared_candidate = next(
            item for item in diagnostics["candidates"] if item["chunk_id"] == "SHARED"
        )
        self.assertEqual(shared_candidate["subquery_count"], 2)
        self.assertAlmostEqual(shared_candidate["selection_score"], 0.02)
        self.assertEqual(diagnostics["selected_chunk_ids"].count("SHARED"), 1)

    def test_planned_context_rejects_more_subqueries_than_budget_can_cover(self):
        with self.assertRaisesRegex(ValueError, "minimum evidence"):
            retrieve_generation_context(
                original_query="Too many independent facts",
                subqueries=[f"fact {index}" for index in range(6)],
                model=object(),
                query_prefix="",
                normalized_embeddings=object(),
                bm25_retriever=object(),
                all_chunks=[],
            )

    def test_global_context_fails_closed_when_complete_evidence_exceeds_tokens(self):
        candidates = [result(f"GLOBAL-{index}", "TSLA") for index in range(10)]

        with patch(
            "src.retrieval.scope_aware.scope_aware_hybrid_retrieve",
            return_value=(candidates, "global", []),
        ):
            with self.assertRaisesRegex(EvidencePackingError, "complete global"):
                retrieve_generation_context(
                    original_query="What risks are common in these filings?",
                    subqueries=["common risks"],
                    model=object(),
                    query_prefix="",
                    normalized_embeddings=object(),
                    bm25_retriever=object(),
                    all_chunks=[],
                    evidence_policy=EvidenceBudgetPolicy(
                        context_window_tokens=500, reserved_output_tokens=100
                    ),
                    token_counter=lambda query, evidence: 1_000,
                )

    def test_citation_resolution_is_limited_to_final_evidence(self):
        evidence = [hydrated("TSLA-1", "TSLA", 1), hydrated("OUST-1", "OUST", 2)]
        resolution = resolve_cited_evidence(
            "Supported [OUST-1], invented [GM-999].", evidence
        )
        self.assertEqual(resolution.resolved_ids, ("OUST-1",))
        self.assertEqual(resolution.rejected_ids, ("GM-999",))
        self.assertEqual(
            [item["chunk"]["chunk_id"] for item in resolution.evidence],
            ["OUST-1"],
        )

    def test_no_citation_returns_no_evidence(self):
        evidence = [hydrated("TSLA-1", "TSLA", 1)]
        resolution = resolve_cited_evidence("No identifier here.", evidence)
        self.assertEqual(resolution.evidence, ())
        self.assertEqual(resolution.diagnostic_reason, "no_resolved_citations")


if __name__ == "__main__":
    unittest.main()
