from __future__ import annotations

import re
from io import BytesIO
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup

from isite2.connectors.compliance import (
    HttpRobotsPolicy,
    InMemorySourceCache,
    RateLimiter,
    classify_source_tier,
)
from isite2.connectors.models import FetchedPage, GeocodeResult, SearchResult


class HttpPublicEvidenceProvider:
    """Compliant real public-data provider.

    Search uses the official Wikipedia API instead of scraping search result pages.
    Fetching arbitrary pages checks robots.txt first, applies a rate limiter, and caches content.
    Geocoding uses the public Nominatim API with an explicit User-Agent.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: InMemorySourceCache | None = None,
        rate_limiter: RateLimiter | None = None,
        robots_policy: HttpRobotsPolicy | None = None,
        user_agent: str = "isite2-mvp/0.1 public-evidence-research",
    ) -> None:
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)
        self.cache = cache or InMemorySourceCache()
        self.rate_limiter = rate_limiter or RateLimiter(max_calls=10, period_seconds=60)
        self.robots_policy = robots_policy or HttpRobotsPolicy(client=self.client)
        self.user_agent = user_agent

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        self._require_rate_limit()
        response = self.client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": limit,
            },
            headers={"User-Agent": self.user_agent},
        )
        response.raise_for_status()
        items = response.json().get("query", {}).get("search", [])
        return [
            SearchResult(
                title=item["title"],
                url=f"https://en.wikipedia.org/wiki/{quote(item['title'].replace(' ', '_'))}",
                source_name="Wikipedia API",
                snippet=_clean_html(item.get("snippet", "")),
            )
            for item in items
        ]

    def fetch_page(self, url: str) -> FetchedPage:
        cached = self.cache.get(url)
        if cached is not None:
            return cached

        robots_allowed = self.robots_policy.allowed(url, self.user_agent)
        if not robots_allowed:
            page = FetchedPage(
                source_url=url,
                source_name=_source_name_from_url(url),
                source_tier=classify_source_tier(_source_name_from_url(url), url),
                content_text="",
                robots_allowed=False,
            )
            return self.cache.put(page)

        self._require_rate_limit()
        response = self.client.get(url, headers={"User-Agent": self.user_agent})
        response.raise_for_status()
        source_name = _source_name_from_url(str(response.url))
        content_text = _response_to_text(response)
        page = FetchedPage(
            source_url=str(response.url),
            source_name=source_name,
            source_tier=classify_source_tier(source_name, str(response.url)),
            source_date=response.headers.get("last-modified"),
            content_text=content_text,
            robots_allowed=True,
        )
        return self.cache.put(page)

    def geocode(self, query: str) -> GeocodeResult:
        self._require_rate_limit()
        response = self.client.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "addressdetails": 1,
            },
            headers={"User-Agent": self.user_agent},
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            raise ValueError(f"geocode result not found: {query}")
        result = results[0]
        address = result.get("address", {}) or {}
        return GeocodeResult(
            latitude=float(result["lat"]),
            longitude=float(result["lon"]),
            geocode_precision=result.get("type") or result.get("class") or "nominatim result",
            map_source="OpenStreetMap Nominatim",
            city=_city_from_address(address),
            country=address.get("country"),
            display_name=result.get("display_name"),
            boundingbox=_parse_boundingbox(result.get("boundingbox")),
        )

    def _require_rate_limit(self) -> None:
        if not self.rate_limiter.allow():
            raise RuntimeError("rate limit exceeded for public evidence provider")


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def _response_to_text(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "application/pdf" in content_type or str(response.url).lower().endswith(".pdf"):
        return _pdf_to_text(response.content)
    return _html_to_text(response.text)


def _pdf_to_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    text_parts: list[str] = []
    for page in reader.pages:
        text_parts.append(page.extract_text() or "")
    return re.sub(r"\s+", " ", " ".join(text_parts)).strip()


def _clean_html(html: str) -> str:
    return _html_to_text(html)


def _source_name_from_url(url: str) -> str:
    return urlparse(url).netloc or "Unknown Source"


def _city_from_address(address: dict[str, str]) -> str | None:
    for key in ["city", "town", "municipality", "village", "county", "state"]:
        value = address.get(key)
        if value:
            return value
    return None


def _parse_boundingbox(values: list[str] | None) -> list[float] | None:
    if not values or len(values) != 4:
        return None
    try:
        south, north, west, east = [float(value) for value in values]
    except ValueError:
        return None
    return [south, north, west, east]
