from datetime import datetime, timezone
import json
import unittest

import httpx

from src.tools.web_search import (
    BRAVE_WEB_SEARCH_URL,
    BraveWebSearchTool,
    UnavailableWebSearchTool,
    WebSearchError,
    WebSearchUnavailableError,
)


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
                                    "url": "https://www.example.com/article#section",
                                    "description": "A <em>bounded</em> excerpt.",
                                },
                                {
                                    "title": "Unsafe local",
                                    "url": "https://127.0.0.1/private",
                                    "description": "Must be removed.",
                                },
                                {
                                    "title": "Second result",
                                    "url": "https://news.example.org/item",
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
        response = tool.search(" current   AV news ", max_results=2)
        self.assertEqual(response.query, "current AV news")
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.results[0].source_id, "web-1")
        self.assertEqual(response.results[0].title, "First result")
        self.assertEqual(response.results[0].url, "https://www.example.com/article")
        self.assertEqual(response.results[0].publisher, "example.com")
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
                    BraveWebSearchTool("secret", client=client).search("query")
        with self.assertRaises(WebSearchError):
            BraveWebSearchTool(
                "secret",
                client=httpx.Client(transport=httpx.MockTransport(lambda request: responses[0])),
            ).search("word " * 51)

    def test_unavailable_adapter_is_explicit(self):
        with self.assertRaises(WebSearchUnavailableError):
            UnavailableWebSearchTool().search("latest news")


if __name__ == "__main__":
    unittest.main()
