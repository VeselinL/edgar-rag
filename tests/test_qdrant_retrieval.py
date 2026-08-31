import unittest
import warnings

import numpy as np
from qdrant_client import QdrantClient

from src.indexing.qdrant_index import (
    create_payload_indexes,
    ensure_collection,
    switch_alias,
    upload_bundle,
)
from src.retrieval.dense import (
    LocalArtifactRetriever,
    QdrantRetriever,
    ShadowDenseRetriever,
)
from tests.test_qdrant_index import synthetic_bundle, unit_vector


class FakeEmbedder:
    def encode(self, sentence, *, normalize_embeddings):
        return unit_vector(0 if "tesla" in sentence.casefold() else 1)


class QdrantRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.bundle = synthetic_bundle()
        self.chunks = [
            chunk for record in self.bundle.records for chunk in record.chunks
        ]
        self.matrix = np.vstack([record.vectors for record in self.bundle.records])
        self.client = QdrantClient(":memory:")
        ensure_collection(self.client, self.bundle.collection_name)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            create_payload_indexes(self.client, self.bundle.collection_name)
            upload_bundle(self.client, self.bundle, parallel=1)
        switch_alias(
            self.client,
            alias_name="ava_current",
            collection_name=self.bundle.collection_name,
        )
        self.local = LocalArtifactRetriever(
            model=FakeEmbedder(),
            query_prefix="",
            normalized_embeddings=self.matrix,
            all_chunks=self.chunks,
        )
        self.qdrant = QdrantRetriever(
            client=self.client,
            collection_name="ava_current",
            model=FakeEmbedder(),
            query_prefix="",
            all_chunks=self.chunks,
        )

    def test_exact_dense_order_matches_local_artifacts(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            local = self.local.search("Tesla", 2)
            remote = self.qdrant.search("Tesla", 2)
        self.assertEqual(
            [item.chunk_id for item in remote],
            [item.chunk_id for item in local],
        )

    def test_ticker_filter_cannot_leak_another_company(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            results = self.qdrant.search("Ford", 10, {"TSLA"})
        self.assertEqual([item.chunk_id for item in results], ["TSLA-TEST-1"])

    def test_shadow_returns_local_results_and_records_parity(self):
        shadow = ShadowDenseRetriever(primary=self.local, shadow=self.qdrant)
        shadow.begin_request()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            results = shadow.search("Tesla", 2)
        records = shadow.consume_report()
        self.assertEqual([item.chunk_id for item in results], ["TSLA-TEST-1", "F-TEST-1"])
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["exact_id_order"])
        self.assertTrue(records[0]["parity_accepted"])
        self.assertEqual(records[0]["id_overlap_ratio"], 1.0)
        self.assertIsInstance(records[0]["qdrant_latency_ms"], float)


if __name__ == "__main__":
    unittest.main()
