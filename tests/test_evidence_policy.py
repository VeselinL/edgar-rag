import unittest
from unittest.mock import patch

from src.resolution.companies import default_company_resolver
from src.retrieval.evidence_policy import (
    EvidenceBudgetPolicy,
    EvidencePackingError,
    EvidencePolicyError,
)
from src.retrieval.scope_aware import retrieve_generation_context


def corpus_for(tickers: tuple[str, ...]) -> list[dict]:
    chunks = []
    for ticker in tickers:
        for rank in range(10):
            chunks.append(
                {
                    "chunk_id": f"{ticker}-{rank}",
                    "ticker": ticker,
                    "company": ticker,
                    "text": f"Complete evidence for {ticker}, rank {rank}. " * 4,
                    "section": f"Item {rank % 3}",
                    "content_type": "table" if rank == 4 else "narrative",
                    "source_group": f"{ticker}-{rank}",
                    "source_token_start": rank * 100,
                    "source_token_end": rank * 100 + 80,
                }
            )
    return chunks


def fake_result(chunk: dict, index: int, rank: int) -> dict:
    return {
        "chunk_id": chunk["chunk_id"],
        "ticker": chunk["ticker"],
        "content_type": chunk["content_type"],
        "index": index,
        "rrf_score": 0.04 - rank / 10_000,
        "dense_rank": rank,
        "dense_score": 1.0 - rank / 100,
        "bm25_rank": rank,
        "bm25_score": 1.0 - rank / 100,
    }


class EvidenceBudgetPolicyTests(unittest.TestCase):
    def test_fixed_two_and_three_company_budgets(self):
        policy = EvidenceBudgetPolicy()
        self.assertEqual(policy.final_total(1), 10)
        self.assertEqual(policy.final_total(2), 15)
        self.assertEqual(policy.final_total(3), 22)
        self.assertEqual(policy.input_token_limit, 28_672)

    def test_four_plus_requires_explicit_configuration(self):
        with self.assertRaisesRegex(EvidencePolicyError, "four or more"):
            EvidenceBudgetPolicy().final_total(4)
        self.assertEqual(
            EvidenceBudgetPolicy(four_plus_supplemental=9).final_total(4), 29
        )

    def test_invalid_supplement_and_subquery_budgets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            EvidenceBudgetPolicy(two_company_supplemental=-1)
        with self.assertRaisesRegex(ValueError, "per-subquery minimum"):
            EvidenceBudgetPolicy(
                candidate_k_per_company=5, minimum_final_per_subquery=6
            )


class CompanyBalancedPackingTests(unittest.TestCase):
    def run_retrieval(
        self,
        tickers: tuple[str, ...],
        *,
        policy: EvidenceBudgetPolicy | None = None,
        token_counter=None,
    ):
        chunks = corpus_for(tickers)
        index_by_id = {
            chunk["chunk_id"]: index for index, chunk in enumerate(chunks)
        }
        observed_scopes = []

        def fake_hybrid(query, *args, allowed_tickers=None, **kwargs):
            ticker = next(iter(allowed_tickers))
            observed_scopes.append(allowed_tickers)
            company_chunks = [
                chunk for chunk in chunks if chunk["ticker"] == ticker
            ]
            return [
                fake_result(chunk, index_by_id[chunk["chunk_id"]], rank)
                for rank, chunk in enumerate(company_chunks, start=1)
            ]

        query = "Compare " + ", ".join(
            "ticker F" if ticker == "F" else ticker for ticker in tickers
        )
        resolution = default_company_resolver.resolve(query)
        subqueries = [f"{ticker} evidence" for ticker in tickers]
        targets = [[ticker] for ticker in tickers]
        with patch(
            "src.retrieval.scope_aware.hybrid_retrieve", side_effect=fake_hybrid
        ):
            diagnostics = retrieve_generation_context(
                original_query=query,
                subqueries=subqueries,
                subquery_targets=targets,
                company_resolution=resolution,
                model=object(),
                query_prefix="",
                normalized_embeddings=object(),
                bm25_retriever=object(),
                all_chunks=chunks,
                evidence_policy=policy or EvidenceBudgetPolicy(),
                token_counter=token_counter,
            )
        return diagnostics, observed_scopes, chunks

    def test_two_company_pools_are_independent_and_keep_five_each(self):
        diagnostics, scopes, _ = self.run_retrieval(("TSLA", "F"))
        self.assertEqual(scopes, [{"TSLA"}, {"F"}])
        self.assertEqual(
            diagnostics["candidate_counts_by_company"], {"TSLA": 10, "F": 10}
        )
        self.assertEqual(len(diagnostics["selected"]), 15)
        self.assertTrue(
            all(
                count >= 5
                for count in diagnostics["selected_counts_by_company"].values()
            )
        )
        self.assertEqual(
            sum(
                candidate["selection_reason"] == "supplemental_relevance"
                for candidate in diagnostics["selected"]
            ),
            5,
        )
        self.assertTrue(diagnostics["quota_satisfied"])
        self.assertTrue(
            all(
                match["dense_rank"] and match["bm25_rank"]
                for candidate in diagnostics["candidates"]
                for match in candidate["subquery_matches"]
            )
        )

    def test_three_company_budget_uses_seven_supplemental_slots(self):
        diagnostics, scopes, _ = self.run_retrieval(("TSLA", "MBLY", "F"))
        self.assertEqual(scopes, [{"TSLA"}, {"MBLY"}, {"F"}])
        self.assertEqual(len(diagnostics["selected"]), 22)
        self.assertTrue(
            all(
                count >= 5
                for count in diagnostics["selected_counts_by_company"].values()
            )
        )
        self.assertEqual(
            sum(
                candidate["selection_reason"] == "supplemental_relevance"
                for candidate in diagnostics["selected"]
            ),
            7,
        )

    def test_anchored_global_keeps_five_anchor_chunks_and_global_supplements(self):
        chunks = corpus_for(("TSLA", "F"))
        index_by_id = {
            chunk["chunk_id"]: index for index, chunk in enumerate(chunks)
        }
        observed_scopes = []

        def fake_hybrid(query, *args, allowed_tickers=None, **kwargs):
            observed_scopes.append(allowed_tickers)
            ticker = "TSLA" if allowed_tickers else "F"
            company_chunks = [
                chunk for chunk in chunks if chunk["ticker"] == ticker
            ]
            return [
                fake_result(chunk, index_by_id[chunk["chunk_id"]], rank)
                for rank, chunk in enumerate(company_chunks, start=1)
            ]

        query = "How does Tesla compare with other companies across the industry?"
        resolution = default_company_resolver.resolve(query)
        with patch(
            "src.retrieval.scope_aware.hybrid_retrieve", side_effect=fake_hybrid
        ):
            diagnostics = retrieve_generation_context(
                original_query=query,
                subqueries=[query],
                subquery_targets=[["TSLA"]],
                company_resolution=resolution,
                model=object(),
                query_prefix="",
                normalized_embeddings=object(),
                bm25_retriever=object(),
                all_chunks=chunks,
            )
        self.assertEqual(observed_scopes, [{"TSLA"}, None])
        self.assertEqual(len(diagnostics["selected"]), 10)
        self.assertGreaterEqual(diagnostics["selected_counts_by_company"]["TSLA"], 5)
        self.assertIn("F", diagnostics["selected_counts_by_company"])

    def test_complete_chunks_are_counted_and_never_truncated(self):
        counter = lambda query, evidence: 100 + sum(
            len(item["chunk"]["text"]) for item in evidence
        )
        policy = EvidenceBudgetPolicy(
            context_window_tokens=10_000, reserved_output_tokens=1_000
        )
        diagnostics, _, chunks = self.run_retrieval(
            ("TSLA", "F"), policy=policy, token_counter=counter
        )
        self.assertLessEqual(diagnostics["context_input_tokens"], 9_000)
        by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
        for selected in diagnostics["selected"]:
            self.assertEqual(
                _selected_text(selected, chunks), by_id[selected["chunk_id"]]["text"]
            )

    def test_impossible_token_quota_fails_instead_of_dropping_a_company(self):
        counter = lambda query, evidence: 100 + 100 * len(evidence)
        policy = EvidenceBudgetPolicy(
            context_window_tokens=1_000, reserved_output_tokens=200
        )
        with self.assertRaisesRegex(EvidencePackingError, "complete chunks"):
            self.run_retrieval(
                ("TSLA", "F"), policy=policy, token_counter=counter
            )


def _selected_text(candidate: dict, chunks: list[dict]) -> str:
    return chunks[candidate["index"]]["text"]


if __name__ == "__main__":
    unittest.main()
