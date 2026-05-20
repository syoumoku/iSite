from __future__ import annotations

from typing import Protocol

from isite2.connectors.models import FetchedPage, GeocodeResult, SearchResult


class PublicEvidenceProvider(Protocol):
    def search(self, query: str) -> list[SearchResult]:
        ...

    def fetch_page(self, url: str) -> FetchedPage:
        ...

    def geocode(self, query: str) -> GeocodeResult:
        ...


class RobotsChecker(Protocol):
    def allowed(self, url: str, user_agent: str = "isite2") -> bool:
        ...
