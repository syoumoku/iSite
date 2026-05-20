from __future__ import annotations

from isite2.connectors.compliance import RobotsPolicy, classify_source_tier
from isite2.connectors.models import FetchedPage, GeocodeResult, SearchResult


class FakePublicEvidenceProvider:
    def __init__(self, robots_policy: RobotsPolicy | None = None) -> None:
        self.robots_policy = robots_policy or RobotsPolicy()

    def search(self, query: str) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"{query} official annual report",
                url="https://example.gov/isite2/fake-annual-report",
                source_name="Example Government Open Data",
                snippet="Official public evidence fixture for iSite2 MVP.",
            )
        ]

    def fetch_page(self, url: str) -> FetchedPage:
        allowed = self.robots_policy.allowed(url)
        tier = classify_source_tier("Example Government Open Data", url)
        return FetchedPage(
            source_url=url,
            source_name="Example Government Open Data",
            source_tier=tier,
            source_date="2026",
            content_text="annual_passenger_throughput: 10000000",
            robots_allowed=allowed,
        )

    def geocode(self, query: str) -> GeocodeResult:
        return GeocodeResult(
            latitude=20.0,
            longitude=100.0,
            geocode_precision=f"MVP fake centroid for {query}",
            map_source="Fake geocoder",
        )
