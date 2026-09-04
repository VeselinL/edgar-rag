from datetime import datetime, timezone
import json
import unittest

import httpx

from src.tools.web_search import (
    BRAVE_WEB_SEARCH_URL,
    TRUSTED_WEB_SOURCES,
    BraveWebSearchTool,
    UnavailableWebSearchTool,
    WebSearchError,
    WebSearchUnavailableError,
    allowed_domains_for,
)
from src.orchestration.models import TrustedSourceKey


class WebSearchToolTests(unittest.TestCase):
    def test_brave_adapter_returns_bounded_safe_provenance(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url).split("?", 1)[0], BRAVE_WEB_SEARCH_URL)
            self.assertEqual(request.headers["x-subscription-token"], "secret")
            self.assertEqual(request.url.params["count"], "2")
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps(
                    {
                        "web": {
                            "results": [
                                {
                                    "title": "<strong>First</strong> result",
                                    "url": "https://www.reuters.com/article#section",
                                    "description": "A <em>bounded</em> excerpt.",
                                },
                                {
                                    "title": "Unsafe local",
                                    "url": "https://127.0.0.1/private",
                                    "description": "Must be removed.",
                                },
                                {
                                    "title": "Second result",
                                    "url": "https://www.reuters.com/item",
                                    "description": "Another excerpt.",
                                },
                            ]
                        }
                    }
                ).encode(),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        tool = BraveWebSearchTool(
            "secret",
            client=client,
            now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        response = tool.search(
            " current   AV news ",
            max_results=2,
            source_keys=(TrustedSourceKey.NEWS_INDEPENDENT,),
        )
        self.assertEqual(response.query, "current AV news")
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.results[0].source_id, "web-1")
        self.assertEqual(response.results[0].title, "First result")
        self.assertEqual(response.results[0].url, "https://www.reuters.com/article")
        self.assertEqual(response.results[0].publisher, "reuters.com")
        self.assertEqual(response.results[0].retrieved_at, "2026-09-01T00:00:00+00:00")

    def test_adapter_rejects_bad_status_type_size_and_query(self):
        responses = (
            httpx.Response(429, headers={"content-type": "application/json"}),
            httpx.Response(200, headers={"content-type": "text/html"}, text="no"),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b" " * 1_048_577,
            ),
        )
        for response in responses:
            with self.subTest(status=response.status_code, size=len(response.content)):
                client = httpx.Client(
                    transport=httpx.MockTransport(lambda request: response)
                )
                with self.assertRaises(WebSearchError):
                    BraveWebSearchTool("secret", client=client).search(
                        "query", source_keys=(TrustedSourceKey.NEWS_INDEPENDENT,)
                    )
        with self.assertRaises(WebSearchError):
            BraveWebSearchTool(
                "secret",
                client=httpx.Client(transport=httpx.MockTransport(lambda request: responses[0])),
            ).search("word " * 51, source_keys=(TrustedSourceKey.NEWS_INDEPENDENT,))

    def test_unavailable_adapter_is_explicit(self):
        with self.assertRaises(WebSearchUnavailableError):
            UnavailableWebSearchTool().search(
                "latest news",
                source_keys=(TrustedSourceKey.NEWS_INDEPENDENT,),
            )

    def test_registry_and_ticker_scope_are_the_only_domain_authority(self):
        self.assertEqual(
            tuple(source.key for source in TRUSTED_WEB_SOURCES),
            tuple(TrustedSourceKey),
        )
        self.assertEqual(
            allowed_domains_for((TrustedSourceKey.ISSUER_OFFICIAL,), ("AUR",)),
            ("aurora.tech", "ir.aurora.tech"),
        )
        self.assertNotIn(
            "tesla.com",
            allowed_domains_for((TrustedSourceKey.ISSUER_OFFICIAL,), ("AUR",)),
        )
        with self.assertRaisesRegex(ValueError, "ticker scope"):
            allowed_domains_for((TrustedSourceKey.ISSUER_OFFICIAL,), ())

    def test_search_rejects_missing_or_unknown_source_keys_before_provider_call(self):
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(500)

        tool = BraveWebSearchTool(
            "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with self.assertRaisesRegex(ValueError, "source key"):
            tool.search("Tesla news", source_keys=())
        with self.assertRaisesRegex(ValueError, "source key"):
            tool.search("Tesla news", source_keys=("arbitrary",))
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
