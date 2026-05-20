from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from isite2.connectors.compliance import InMemorySourceCache, classify_source_tier
from isite2.connectors.models import FetchedPage, SearchResult

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "WS_PROXY",
    "WSS_PROXY",
    "ws_proxy",
    "wss_proxy",
)


@dataclass
class FirecrawlUsageStats:
    search_requests: int = 0
    scrape_requests: int = 0
    credits_used: float = 0
    warnings: list[str] = field(default_factory=list)


class FirecrawlPublicEvidenceProvider:
    """Firecrawl-backed public evidence adapter.

    Firecrawl is used as a provider, not as a replacement for iSite2 evidence
    curation. Search/scrape results are normalized into the same connector
    models as the existing HTTP provider so downstream cache, raw evidence,
    curation, and review logic stay in control.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        enabled: bool | None = None,
        base_url: str = "https://api.firecrawl.dev/v2",
        client: httpx.Client | None = None,
        cache: InMemorySourceCache | None = None,
        timeout_ms: int = 30_000,
        scrape_search_results: bool = True,
        search_country: str | None = None,
        search_location: str | None = None,
        proxy: str | None = None,
        max_attempts: int | None = None,
        sleep_func: Any = time.sleep,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("FIRECRAWL_API_KEY") or _api_key_from_cli_credentials()
        )
        self.enabled = _enabled_from_env(enabled) and bool(self.api_key)
        env_base_url = os.getenv("FIRECRAWL_BASE_URL")
        self.base_url = (env_base_url or base_url).rstrip("/")
        env_timeout = os.getenv("FIRECRAWL_TIMEOUT_MS")
        if env_timeout and timeout_ms == 30_000:
            timeout_ms = int(env_timeout)
        self.client = client or httpx.Client(
            timeout=timeout_ms / 1000,
            follow_redirects=True,
            trust_env=_firecrawl_trust_env_proxy(),
        )
        self.cache = cache or InMemorySourceCache()
        self.timeout_ms = timeout_ms
        self.scrape_search_results = scrape_search_results
        self._search_page_cache: dict[str, FetchedPage] = {}
        self.search_country = (
            search_country
            if search_country is not None
            else os.getenv("FIRECRAWL_COUNTRY", "EG")
        )
        self.search_location = (
            search_location
            if search_location is not None
            else os.getenv("FIRECRAWL_LOCATION", "Egypt")
        )
        self.proxy = proxy if proxy is not None else os.getenv("FIRECRAWL_PROXY")
        self.max_attempts = max_attempts or int(os.getenv("FIRECRAWL_MAX_ATTEMPTS", "3"))
        self.sleep_func = sleep_func
        self.stats = FirecrawlUsageStats()

    @classmethod
    def from_env(
        cls,
        *,
        cache: InMemorySourceCache | None = None,
        client: httpx.Client | None = None,
    ) -> FirecrawlPublicEvidenceProvider:
        return cls(cache=cache, client=client)

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        location: str | None = None,
    ) -> list[SearchResult]:
        if not self.enabled:
            return []
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "sources": ["web"],
            "timeout": self.timeout_ms,
        }
        geo_country = country if country is not None else self.search_country
        geo_location = location if location is not None else self.search_location
        if geo_country:
            payload["country"] = geo_country
        if geo_location:
            payload["location"] = geo_location
        if include_domains:
            payload["includeDomains"] = include_domains
        if exclude_domains:
            payload["excludeDomains"] = exclude_domains
        if self.scrape_search_results:
            payload["scrapeOptions"] = self._scrape_options()

        payload = _without_none(payload)
        response = self._post("/search", payload)
        self.stats.search_requests += 1
        response_payload = response.json()
        self._record_response_usage(response_payload)
        web_results = _web_results(response_payload)
        results: list[SearchResult] = []
        for item in web_results[:limit]:
            url = str(item.get("url") or item.get("sourceURL") or "").strip()
            if not url:
                continue
            title = str(item.get("title") or item.get("metadata", {}).get("title") or url)
            source_name = _source_name(url)
            markdown = str(item.get("markdown") or "")
            if markdown:
                page = _page_from_firecrawl_item(item, url, source_name)
                self._search_page_cache[url] = self.cache.put(page)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    source_name="Firecrawl Search",
                    snippet=str(item.get("description") or item.get("snippet") or ""),
                )
            )
        return results

    def fetch_page(self, url: str) -> FetchedPage:
        cached = self.cache.get(url) or self._search_page_cache.get(url)
        if cached is not None:
            return cached
        if not self.enabled:
            raise RuntimeError("Firecrawl provider is disabled or FIRECRAWL_API_KEY is not set")
        response = self._post(
            "/scrape",
            {
                "url": url,
                **self._scrape_options(),
                "timeout": self.timeout_ms,
            },
        )
        self.stats.scrape_requests += 1
        payload = response.json()
        self._record_response_usage(payload)
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            data = payload if isinstance(payload, dict) else {}
        source_url = _metadata_value(data, "sourceURL") or url
        page = _page_from_firecrawl_item(data, str(source_url), _source_name(str(source_url)))
        return self.cache.put(page)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _scrape_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "formats": [{"type": "markdown"}],
            "onlyMainContent": True,
            "removeBase64Images": True,
            "blockAds": True,
        }
        if self.proxy:
            options["proxy"] = self.proxy
        return options

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        attempts = max(1, self.max_attempts)
        last_response: httpx.Response | None = None
        for attempt in range(attempts):
            try:
                response = self.client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=self._headers(),
                )
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    response.raise_for_status()
                    return response
                last_response = response
                if attempt == attempts - 1:
                    response.raise_for_status()
                self._sleep_before_retry(response)
            except httpx.HTTPStatusError:
                raise
            except httpx.HTTPError:
                if attempt == attempts - 1:
                    raise
                self._sleep_before_retry(None)
        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("Firecrawl request failed without an HTTP response")

    def _sleep_before_retry(self, response: httpx.Response | None) -> None:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        delay_seconds = _retry_after_seconds(retry_after) if retry_after else 1.0
        self.sleep_func(min(delay_seconds, 30.0))

    def _record_response_usage(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        credits = payload.get("creditsUsed") or payload.get("credits_used") or 0
        try:
            self.stats.credits_used += float(credits or 0)
        except (TypeError, ValueError):
            pass
        warning = payload.get("warning") or payload.get("warnings")
        if isinstance(warning, list):
            self.stats.warnings.extend(str(item) for item in warning if item)
        elif warning:
            self.stats.warnings.append(str(warning))


class FakeFirecrawlPublicEvidenceProvider(FirecrawlPublicEvidenceProvider):
    def __init__(
        self,
        *,
        search_pages: list[dict[str, Any]] | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(api_key="fc-fake", enabled=enabled, client=httpx.Client())
        self.search_pages = search_pages or [
            {
                "title": "Airport official passenger report",
                "url": "https://firecrawl.example/airport-report",
                "description": "Official passenger report.",
                "markdown": "The airport handled 8,000,000 passengers in 2024.",
                "metadata": {"sourceURL": "https://firecrawl.example/airport-report"},
            }
        ]

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[SearchResult]:
        if not self.enabled:
            return []
        results: list[SearchResult] = []
        for page in self.search_pages[:limit]:
            url = str(page["url"])
            source_name = _source_name(url)
            fetched = _page_from_firecrawl_item(page, url, source_name)
            self._search_page_cache[url] = self.cache.put(fetched)
            results.append(
                SearchResult(
                    title=str(page.get("title") or url),
                    url=url,
                    source_name="Firecrawl Search",
                    snippet=str(page.get("description") or ""),
                )
            )
        return results


def _enabled_from_env(enabled: bool | None) -> bool:
    if enabled is not None:
        return enabled
    return os.getenv("FIRECRAWL_ENABLED", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def firecrawl_subprocess_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build an environment for Firecrawl CLI calls.

    Automation shells can inherit stale local proxy settings, for example
    `HTTP_PROXY=http://127.0.0.1:7890`. Firecrawl does not require those
    proxies, so keep CLI calls direct unless an operator explicitly opts in.
    """

    cleaned = dict(os.environ if env is None else env)
    if _firecrawl_trust_env_proxy():
        return cleaned
    for key in PROXY_ENV_VARS:
        cleaned.pop(key, None)
    return cleaned


def _firecrawl_trust_env_proxy() -> bool:
    return os.getenv("FIRECRAWL_TRUST_ENV_PROXY", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _api_key_from_cli_credentials() -> str | None:
    path = os.getenv("FIRECRAWL_CLI_CREDENTIALS_PATH")
    credentials_path = (
        Path(path)
        if path
        else Path.home() / "Library" / "Application Support" / "firecrawl-cli" / "credentials.json"
    )
    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    api_key = payload.get("apiKey") or payload.get("api_key")
    if not isinstance(api_key, str):
        return None
    return api_key.strip() or None


def _web_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        web = data.get("web", data.get("data", data.get("results", [])))
        if isinstance(web, list):
            return [item for item in web if isinstance(item, dict)]
    return []


def _page_from_firecrawl_item(
    item: dict[str, Any],
    url: str,
    source_name: str,
) -> FetchedPage:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    source_url = str(metadata.get("sourceURL") or metadata.get("source_url") or url)
    source_date = (
        str(metadata.get("publishedTime") or metadata.get("published_time") or "")
        or None
    )
    markdown = item.get("markdown")
    if isinstance(markdown, dict):
        markdown = markdown.get("rawMarkdown") or markdown.get("content")
    content = str(markdown or item.get("html") or item.get("text") or "")
    return FetchedPage(
        source_url=source_url,
        source_name=source_name,
        source_tier=classify_source_tier(source_name, source_url),
        source_date=source_date,
        fetched_at=datetime.now(UTC),
        content_text=content,
        robots_allowed=True,
    )


def _metadata_value(item: dict[str, Any], key: str) -> Any:
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key) or metadata.get(_camel_to_snake(key))
    return None


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper() and chars:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def _source_name(url: str) -> str:
    return urlparse(url).netloc or "Firecrawl Source"


def _retry_after_seconds(value: str | None) -> float:
    if not value:
        return 1.0
    try:
        return max(float(value), 0.0)
    except ValueError:
        return 1.0


def _without_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
