from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    published_at: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class SearchProvider(Protocol):
    name: str

    def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
    ) -> list[SearchResult]:
        ...


class DisabledSearchProvider:
    name = "disabled"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
    ) -> list[SearchResult]:
        raise RuntimeError(
            "Web search provider is disabled. Set WEB_SEARCH_PROVIDER and WEB_SEARCH_API_KEY "
            "to enable live search."
        )


class MockSearchProvider:
    name = "mock"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
    ) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"Mock result for {query}",
                url="https://example.com/mock-search-result",
                snippet=f"Deterministic mock web search result for query: {query}",
                source=self.name,
            )
        ][:max_results]


class BingSearchProvider:
    name = "bing"
    default_base_url = "https://api.bing.microsoft.com/v7.0/search"

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.web_search_api_key
        self.base_url = settings.web_search_base_url or self.default_base_url
        self.timeout_seconds = settings.web_search_timeout_seconds

    def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
    ) -> list[SearchResult]:
        if not self.api_key:
            raise RuntimeError("WEB_SEARCH_API_KEY is required when WEB_SEARCH_PROVIDER=bing")
        params: dict[str, Any] = {"q": query, "count": max_results, "responseFilter": "Webpages"}
        freshness = _bing_freshness(recency_days)
        if freshness:
            params["freshness"] = freshness
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                self.base_url,
                params=params,
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
            )
            response.raise_for_status()
        values = response.json().get("webPages", {}).get("value") or []
        return [
            SearchResult(
                title=str(item.get("name") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("snippet") or ""),
                source=self.name,
                published_at=item.get("dateLastCrawled"),
            )
            for item in values[:max_results]
            if item.get("url")
        ]


class TavilySearchProvider:
    name = "tavily"
    default_base_url = "https://api.tavily.com/search"

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.web_search_api_key
        self.base_url = settings.web_search_base_url or self.default_base_url
        self.timeout_seconds = settings.web_search_timeout_seconds

    def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
    ) -> list[SearchResult]:
        if not self.api_key:
            raise RuntimeError("WEB_SEARCH_API_KEY is required when WEB_SEARCH_PROVIDER=tavily")
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        if recency_days is not None:
            payload["days"] = max(1, recency_days)
            payload["topic"] = "news"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                self.base_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
        return _parse_common_results(response.json(), provider=self.name, max_results=max_results)


class HttpJsonSearchProvider:
    name = "http-json"

    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.web_search_api_key
        self.base_url = settings.web_search_base_url
        self.timeout_seconds = settings.web_search_timeout_seconds

    def search(
        self,
        query: str,
        *,
        max_results: int,
        recency_days: int | None = None,
    ) -> list[SearchResult]:
        if not self.base_url:
            raise RuntimeError("WEB_SEARCH_BASE_URL is required when WEB_SEARCH_PROVIDER=http-json")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "query": query,
            "max_results": max_results,
            "recency_days": recency_days,
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
        return _parse_common_results(response.json(), provider=self.name, max_results=max_results)


def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    settings = settings or get_settings()
    provider = settings.web_search_provider.strip().lower()
    if provider in {"", "disabled", "none", "off"}:
        return DisabledSearchProvider()
    if provider == "mock":
        return MockSearchProvider()
    if provider == "bing":
        return BingSearchProvider(settings)
    if provider == "tavily":
        return TavilySearchProvider(settings)
    if provider in {"http", "http-json", "generic"}:
        return HttpJsonSearchProvider(settings)
    raise RuntimeError(f"Unsupported WEB_SEARCH_PROVIDER: {settings.web_search_provider}")


def search_web(
    query: str,
    *,
    max_results: int | None = None,
    recency_days: int | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    clean_query = " ".join(query.strip().split())
    if not clean_query:
        raise ValueError("Query is required")
    limit = _clamp_limit(max_results, settings.web_search_max_results)
    provider = get_search_provider(settings)
    try:
        results = provider.search(clean_query, max_results=limit, recency_days=recency_days)
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_http_error(provider.name, exc)) from exc
    return {
        "query": clean_query,
        "provider": provider.name,
        "results": [result.to_dict() for result in results],
    }


def _clamp_limit(requested: int | None, configured_max: int) -> int:
    default = max(1, configured_max)
    if requested is None:
        return default
    return max(1, min(requested, default))


def _bing_freshness(recency_days: int | None) -> str | None:
    if recency_days is None:
        return None
    if recency_days <= 1:
        return "Day"
    if recency_days <= 7:
        return "Week"
    if recency_days <= 31:
        return "Month"
    return None


def _format_http_error(provider: str, exc: httpx.HTTPError) -> str:
    detail = str(exc) or exc.__class__.__name__
    return (
        f"Web search request failed for provider '{provider}': {detail}. "
        "Check WEB_SEARCH_BASE_URL, network/proxy access, and TLS compatibility."
    )


def _parse_common_results(
    payload: dict[str, Any],
    *,
    provider: str,
    max_results: int,
) -> list[SearchResult]:
    raw_results = payload.get("results") or payload.get("items") or payload.get("data") or []
    if not isinstance(raw_results, list):
        return []
    results: list[SearchResult] = []
    for item in raw_results[:max_results]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or item.get("href") or "")
        if not url:
            continue
        results.append(
            SearchResult(
                title=str(item.get("title") or item.get("name") or url),
                url=url,
                snippet=str(item.get("snippet") or item.get("summary") or item.get("content") or ""),
                source=str(item.get("source") or provider),
                published_at=item.get("published_at") or item.get("date") or item.get("publishedDate"),
            )
        )
    return results
