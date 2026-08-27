import unittest

from src.filings.corpus import ACTIVE_COMPANY_COUNT, ACTIVE_FILINGS, COMPANY_ALIASES
from src.filings.fetch_data import COMPANIES


class ActiveCorpusTests(unittest.TestCase):
    def test_active_registries_cover_the_same_eleven_tickers(self):
        acquisition_tickers = {company["ticker"] for company in COMPANIES.values()}
        self.assertEqual(ACTIVE_COMPANY_COUNT, 11)
        self.assertEqual(set(ACTIVE_FILINGS), acquisition_tickers)
        self.assertEqual(set(ACTIVE_FILINGS), set(COMPANY_ALIASES))

    def test_rivian_acquisition_and_retrieval_metadata(self):
        self.assertEqual(
            COMPANIES["rivian"],
            {
                "company": "Rivian Automotive, Inc.",
                "ticker": "RIVN",
                "cik": "0001874178",
            },
        )
        self.assertEqual(ACTIVE_FILINGS["RIVN"], "2025-10-K")
        self.assertIn("rivian", COMPANY_ALIASES["RIVN"])
        self.assertIn("rivn", COMPANY_ALIASES["RIVN"])


if __name__ == "__main__":
    unittest.main()
