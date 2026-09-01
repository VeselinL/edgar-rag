"""Provider-neutral bounded web search and a Brave Search adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import html
import ipaddress
import re
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx


BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_QUERY_CHARACTERS = 400
MAX_QUERY_WORDS = 50
MAX_RESULTS = 10
MAX_EXCERPT_CHARACTERS = 1_000
MAX_RESPONSE_BYTES = 1_048_576


class WebSearchError(RuntimeError):
    """A web provider failed without exposing its raw response."""


class WebSearchUnavailableError(WebSearchError):
    """No configured web-search provider can execute the request."""


@dataclass(frozen=True)
class WebSearchResult:
    source_id: str
    title: str
    url: str
    publisher: str
    retrieved_at: str
    excerpt: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class WebSearchResponse:
    query: str
    provider: str
    results: tuple[WebSearchResult, ...]


class WebSearchTool(Protocol):
    provider: str

    def search(self, query: str, *, max_results: int = 5) -> WebSearchResponse: ...

    def close(self) -> None: ...


class UnavailableWebSearchTool:
    provider = "disabled"

    def search(self, query: str, *, max_results: int = 5) -> WebSearchResponse:
        raise WebSearchUnavailableError("Web search is not configured.")

    def close(self) -> None:
        return None


def _safe_result_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    normalized_host = hostname.rstrip(".").casefold()
    if (
        normalized_host == "localhost"
        or normalized_host.endswith((".localhost", ".local", ".internal"))
    ):
        return None
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            return None
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def _clean_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    without_markup = re.sub(r"<[^>]{0,500}>", " ", value)
    normalized = " ".join(html.unescape(without_markup).split())
    return normalized[:maximum].rstrip()


class BraveWebSearchTool:
    """One-call Brave web adapter; returned page text is never fetched or executed."""

    provider = "brave"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 8.0,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Brave Search requires a non-empty API key.")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Web-search timeout must be between 0 and 30 seconds.")
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._now = now or (lambda: datetime.now(timezone.utc))

    def search(self, query: str, *, max_results: int = 5) -> WebSearchResponse:
        normalized_query = " ".join(query.split())
        if (
            not normalized_query
            or len(normalized_query) > MAX_QUERY_CHARACTERS
            or len(normalized_query.split()) > MAX_QUERY_WORDS
        ):
            raise WebSearchError("Web-search query is empty or exceeds provider limits.")
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= MAX_RESULTS:
            raise ValueError(f"Web search returns between 1 and {MAX_RESULTS} results.")
        try:
            response = self._client.get(
                BRAVE_WEB_SEARCH_URL,
                params={
                    "q": normalized_query,
                    "count": max_results,
                    "safesearch": "moderate",
                    "text_decorations": "false",
                    "spellcheck": "true",
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._api_key,
                },
            )
        except httpx.HTTPError as error:
            raise WebSearchError("The web-search provider request failed.") from error
        if response.status_code != 200:
            raise WebSearchError("The web-search provider returned an error status.")
        content_type = response.headers.get("content-type", "").casefold()
        if not content_type.startswith("application/json"):
            raise WebSearchError("The web-search provider returned an invalid content type.")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise WebSearchError("The web-search provider response exceeded the byte limit.")
        try:
            payload = response.json()
        except ValueError as error:
            raise WebSearchError("The web-search provider returned invalid JSON.") from error
        raw_results = payload.get("web", {}).get("results", []) if isinstance(payload, dict) else []
        if not isinstance(raw_results, list):
            raise WebSearchError("The web-search provider returned an invalid result list.")
        retrieved_at = self._now().astimezone(timezone.utc).isoformat()
        results: list[WebSearchResult] = []
        seen_urls: set[str] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = _safe_result_url(item.get("url"))
            title = _clean_text(item.get("title"), maximum=300)
            excerpt = _clean_text(item.get("description"), maximum=MAX_EXCERPT_CHARACTERS)
            if not url or url in seen_urls or not title or not excerpt:
                continue
            seen_urls.add(url)
            publisher = urlsplit(url).hostname or "unknown"
            if publisher.startswith("www."):
                publisher = publisher[4:]
            results.append(
                WebSearchResult(
                    source_id=f"web-{len(results) + 1}",
                    title=title,
                    url=url,
                    publisher=publisher,
                    retrieved_at=retrieved_at,
                    excerpt=excerpt,
                )
            )
            if len(results) >= max_results:
                break
        return WebSearchResponse(normalized_query, self.provider, tuple(results))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
