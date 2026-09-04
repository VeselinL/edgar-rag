"""Provider-neutral bounded web search and a Tavily Search adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import html
import ipaddress
import re
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.orchestration.models import TrustedSourceKey


TAVILY_WEB_SEARCH_URL = "https://api.tavily.com/search"
MAX_QUERY_CHARACTERS = 400
MAX_QUERY_WORDS = 50
MAX_RESULTS = 10
MAX_EXCERPT_CHARACTERS = 1_000
MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True)
class TrustedWebSource:
    key: TrustedSourceKey
    urls: tuple[str, ...]
    use_for: tuple[str, ...]
    priority: int


TRUSTED_WEB_SOURCES = (
    TrustedWebSource(
        TrustedSourceKey.SEC_EDGAR,
        ("https://www.sec.gov/", "https://data.sec.gov/"),
        ("latest filings", "8-K", "proxy statements", "XBRL facts", "official filer metadata"),
        1,
    ),
    TrustedWebSource(
        TrustedSourceKey.ISSUER_OFFICIAL,
        (
            "https://www.aptiv.com/", "https://ir.aptiv.com/", "https://aurora.tech/", "https://ir.aurora.tech/",
            "https://www.ford.com/", "https://shareholder.ford.com/", "https://www.gm.com/", "https://investors.gm.com/",
            "https://abc.xyz/", "https://abc.xyz/investor/", "https://www.mobileye.com/", "https://ir.mobileye.com/",
            "https://www.nvidia.com/", "https://investor.nvidia.com/", "https://ouster.com/", "https://investors.ouster.com/",
            "https://www.qualcomm.com/", "https://investor.qualcomm.com/", "https://rivian.com/", "https://rivian.com/investors/",
            "https://www.tesla.com/", "https://ir.tesla.com/",
        ),
        ("current leadership", "current products", "earnings releases", "official company announcements"),
        2,
    ),
    TrustedWebSource(
        TrustedSourceKey.VEHICLE_REGULATOR,
        ("https://www.nhtsa.gov/", "https://api.nhtsa.gov/"),
        ("recalls", "vehicle safety", "official regulatory data"),
        2,
    ),
    TrustedWebSource(
        TrustedSourceKey.MARKET_PRIMARY,
        ("https://www.nasdaq.com/market-activity/stocks/", "https://www.nyse.com/quote/"),
        ("exchange listing", "market status", "delayed or exchange-sourced quote data"),
        3,
    ),
    TrustedWebSource(
        TrustedSourceKey.MARKET_SECONDARY,
        ("https://robinhood.com/us/en/stocks/",),
        ("current retail quote", "market summary", "secondary corroboration"),
        4,
    ),
    TrustedWebSource(
        TrustedSourceKey.NEWS_INDEPENDENT,
        ("https://www.reuters.com/",),
        ("recent independent company news", "leadership-change corroboration"),
        4,
    ),
)

_ISSUER_DOMAINS = {
    "APTV": ("aptiv.com", "ir.aptiv.com"), "AUR": ("aurora.tech", "ir.aurora.tech"),
    "F": ("ford.com", "shareholder.ford.com"), "GM": ("gm.com", "investors.gm.com"),
    "GOOGL": ("abc.xyz",), "MBLY": ("mobileye.com", "ir.mobileye.com"),
    "NVDA": ("nvidia.com", "investor.nvidia.com"), "OUST": ("ouster.com", "investors.ouster.com"),
    "QCOM": ("qualcomm.com", "investor.qualcomm.com"), "RIVN": ("rivian.com",),
    "TSLA": ("tesla.com", "ir.tesla.com"),
}
_EXCHANGE_DOMAINS = {
    "APTV": "nyse.com", "AUR": "nasdaq.com", "F": "nyse.com", "GM": "nyse.com",
    "GOOGL": "nasdaq.com", "MBLY": "nasdaq.com", "NVDA": "nasdaq.com", "OUST": "nyse.com",
    "QCOM": "nasdaq.com", "RIVN": "nasdaq.com", "TSLA": "nasdaq.com",
}


def _registry_domains(key: TrustedSourceKey) -> tuple[str, ...]:
    source = next(source for source in TRUSTED_WEB_SOURCES if source.key is key)
    return tuple(
        dict.fromkeys((urlsplit(url).hostname or "").removeprefix("www.") for url in source.urls)
    )


def allowed_domains_for(
    source_keys: tuple[TrustedSourceKey, ...], tickers: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Map reviewed source keys to the only hosts a search may return."""
    if not source_keys or len(source_keys) != len(set(source_keys)):
        raise ValueError("Web search requires unique trusted source keys.")
    try:
        keys = tuple(TrustedSourceKey(key) for key in source_keys)
    except (TypeError, ValueError) as error:
        raise ValueError("Web search contains an unknown trusted source key.") from error
    normalized_tickers = tuple(ticker.upper() for ticker in tickers)
    if len(normalized_tickers) != len(set(normalized_tickers)) or any(
        ticker not in _ISSUER_DOMAINS for ticker in normalized_tickers
    ):
        raise ValueError("Web search contains an invalid ticker scope.")
    domains: list[str] = []
    for key in keys:
        if key is TrustedSourceKey.ISSUER_OFFICIAL:
            if not normalized_tickers:
                raise ValueError("Issuer web search requires a ticker scope.")
            domains.extend(domain for ticker in normalized_tickers for domain in _ISSUER_DOMAINS[ticker])
        elif key is TrustedSourceKey.MARKET_PRIMARY:
            if not normalized_tickers:
                raise ValueError("Market web search requires a ticker scope.")
            domains.extend(_EXCHANGE_DOMAINS[ticker] for ticker in normalized_tickers)
        else:
            domains.extend(_registry_domains(key))
    return tuple(dict.fromkeys(domains))


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

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        source_keys: tuple[TrustedSourceKey, ...],
        tickers: tuple[str, ...] = (),
    ) -> WebSearchResponse: ...

    def close(self) -> None: ...


class UnavailableWebSearchTool:
    provider = "disabled"

    def search(
        self, query: str, *, max_results: int = 5,
        source_keys: tuple[TrustedSourceKey, ...], tickers: tuple[str, ...] = (),
    ) -> WebSearchResponse:
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


class TavilyWebSearchTool:
    """One-call Tavily adapter; returned page text is never fetched or executed."""

    provider = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 8.0,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
        api_url: str = TAVILY_WEB_SEARCH_URL,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Tavily Search requires a non-empty API key.")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Web-search timeout must be between 0 and 30 seconds.")
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._api_url = api_url.rstrip("/") + "/search" if not api_url.rstrip("/").endswith("/search") else api_url

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        source_keys: tuple[TrustedSourceKey, ...],
        tickers: tuple[str, ...] = (),
    ) -> WebSearchResponse:
        normalized_query = " ".join(query.split())
        if (
            not normalized_query
            or len(normalized_query) > MAX_QUERY_CHARACTERS
            or len(normalized_query.split()) > MAX_QUERY_WORDS
        ):
            raise WebSearchError("Web-search query is empty or exceeds provider limits.")
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= MAX_RESULTS:
            raise ValueError(f"Web search returns between 1 and {MAX_RESULTS} results.")
        allowed_domains = allowed_domains_for(source_keys, tickers)
        domain_query = " OR ".join(f"site:{domain}" for domain in allowed_domains)
        provider_query = f"({normalized_query}) ({domain_query})"
        try:
            response = self._client.post(
                self._api_url,
                json={
                    "query": normalized_query,
                    "search_depth": "basic",
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_domains": list(allowed_domains),
                },
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
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
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
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
            excerpt = _clean_text(item.get("content"), maximum=MAX_EXCERPT_CHARACTERS)
            if not url or url in seen_urls or not title or not excerpt:
                continue
            seen_urls.add(url)
            publisher = urlsplit(url).hostname or "unknown"
            if publisher.startswith("www."):
                publisher = publisher[4:]
            if not any(
                publisher == domain or publisher.endswith("." + domain)
                for domain in allowed_domains
            ):
                continue
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
