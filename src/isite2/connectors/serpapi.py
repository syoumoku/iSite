from __future__ import annotations

import os

import httpx

from isite2.connectors.models import SearchResult


class SerpApiSearchProvider:
    """Small adapter around SerpAPI's Google search endpoint.

    The adapter is inert when no API key is configured. That keeps the discovery
    loop usable in public-source-only mode and lets deployments opt in by setting
    SERPAPI_API_KEY.
    """

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("SERPAPI_API_KEY")
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self.enabled:
            return []
        response = self.client.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "api_key": self.api_key,
                "num": limit,
            },
        )
        response.raise_for_status()
        results = response.json().get("organic_results", [])
        search_results: list[SearchResult] = []
        for item in results[:limit]:
            link = item.get("link")
            title = item.get("title")
            if not link or not title:
                continue
            search_results.append(
                SearchResult(
                    title=title,
                    url=link,
                    source_name=item.get("source") or "SerpAPI",
                    snippet=item.get("snippet") or "",
                )
            )
        return search_results
