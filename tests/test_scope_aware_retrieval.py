import unittest
from unittest.mock import patch

from src.generation.rag import resolve_cited_evidence
from src.retrieval.scope_aware import (
    deduplicate_results,
    detect_companies,
    detect_scope,
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
        selected = select_final_evidence(candidates, 12)
        self.assertEqual(len(selected), 12)
        self.assertEqual(len({item["chunk"]["chunk_id"] for item in selected}), 12)

    def test_comparison_keeps_every_available_target(self):
        reranked = [
            *[hydrated(f"TSLA-{index}", "TSLA", index) for index in range(1, 13)],
            hydrated("OUST-1", "OUST", 13),
        ]
        selected = select_final_evidence(reranked, 12, ("TSLA", "OUST"))
        self.assertEqual({item["chunk"]["ticker"] for item in selected}, {"TSLA", "OUST"})
        self.assertEqual(len(selected), 12)

    def test_explicit_comparison_retrieves_each_company_scope(self):
        def fake_hybrid(*args, allowed_tickers=None, **kwargs):
            ticker = next(iter(allowed_tickers))
            return [result(f"{ticker}-{position}", ticker) for position in range(1, 5)]

        with patch("src.retrieval.scope_aware.hybrid_retrieve", side_effect=fake_hybrid):
            retrieved, scope, companies = scope_aware_hybrid_retrieve(
                "Compare Tesla and Ouster",
                model=object(),
                query_prefix="",
                normalized_embeddings=object(),
                bm25_retriever=object(),
                all_chunks=[],
                top_k=6,
            )
        self.assertEqual(scope, "explicit_subset")
        self.assertEqual(companies, ["TSLA", "OUST"])
        self.assertEqual({item["ticker"] for item in retrieved}, {"TSLA", "OUST"})
        self.assertEqual([item["ticker"] for item in retrieved], ["TSLA", "OUST"] * 3)

    def test_citation_resolution_is_limited_to_final_evidence(self):
        evidence = [hydrated("TSLA-1", "TSLA", 1), hydrated("OUST-1", "OUST", 2)]
        resolved, fallback = resolve_cited_evidence(
            "Supported [OUST-1], invented [GM-999].", evidence
        )
        self.assertFalse(fallback)
        self.assertEqual([item["chunk"]["chunk_id"] for item in resolved], ["OUST-1"])

    def test_no_citation_returns_final_evidence_fallback(self):
        evidence = [hydrated("TSLA-1", "TSLA", 1)]
        resolved, fallback = resolve_cited_evidence("No identifier here.", evidence)
        self.assertTrue(fallback)
        self.assertEqual(resolved, evidence)


if __name__ == "__main__":
    unittest.main()
