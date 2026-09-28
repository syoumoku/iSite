from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from isite2.connectors.scrapling import (
    ScraplingPublicEvidenceProvider,
    ScraplingRestrictedPage,
    extract_image_candidates_from_html,
)


@dataclass
class FakeResponse:
    body: bytes
    url: str = "https://venue.example/facts"
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    encoding: str = "utf-8"


class FakeRobotsPolicy:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def allowed(self, url: str, user_agent: str) -> bool:
        return self._allowed


def _html(body: str) -> bytes:
    return f"<html><head><title>Official facts</title></head><body>{body}</body></html>".encode()


def test_scrapling_static_fetch_writes_fetched_page_artifact(tmp_path) -> None:
    text = "The airport handled 12,000,000 passengers in 2024. " * 8

    def static_fetcher(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(_html(text), url=url, headers={"last-modified": "Wed, 01 Jan 2025 00:00:00 GMT"})

    provider = ScraplingPublicEvidenceProvider(
        static_fetcher=static_fetcher,
        robots_policy=FakeRobotsPolicy(True),
        artifact_dir=tmp_path,
        allow_dynamic=False,
        allow_stealth=False,
    )

    page = provider.fetch_page("https://venue.example/facts")

    assert page.robots_allowed is True
    assert "12,000,000 passengers" in page.content_text
    assert page.source_date == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert provider.stats.static_fetch_count == 1
    artifacts = list(tmp_path.rglob("*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["provider"] == "scrapling"
    assert payload["fetcher_mode"] == "static"
    assert payload["robots_allowed"] is True


def test_scrapling_respects_robots_disallow_without_fetching(tmp_path) -> None:
    called = False

    def static_fetcher(url: str, **kwargs) -> FakeResponse:
        nonlocal called
        called = True
        return FakeResponse(_html("Should not fetch"), url=url)

    provider = ScraplingPublicEvidenceProvider(
        static_fetcher=static_fetcher,
        robots_policy=FakeRobotsPolicy(False),
        artifact_dir=tmp_path,
    )

    page = provider.fetch_page("https://venue.example/private")

    assert page.robots_allowed is False
    assert called is False
    assert provider.stats.robots_disallowed_count == 1


def test_scrapling_upgrades_short_static_page_to_dynamic(tmp_path) -> None:
    def static_fetcher(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(b"<html><body><div id='app-root'></div></body></html>", url=url)

    def dynamic_fetcher(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(
            _html("The venue has 50,000 square meters of exhibition area. " * 8),
            url=url,
        )

    provider = ScraplingPublicEvidenceProvider(
        static_fetcher=static_fetcher,
        dynamic_fetcher=dynamic_fetcher,
        robots_policy=FakeRobotsPolicy(True),
        artifact_dir=tmp_path,
        allow_dynamic=True,
        allow_stealth=False,
    )

    page = provider.fetch_page("https://venue.example/app")

    assert "50,000 square meters" in page.content_text
    assert provider.stats.static_fetch_count == 1
    assert provider.stats.dynamic_fetch_count == 1


def test_scrapling_does_not_bypass_restricted_pages(tmp_path) -> None:
    def static_fetcher(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(_html("Please complete the CAPTCHA to continue. " * 8), url=url)

    def stealth_fetcher(url: str, **kwargs) -> FakeResponse:
        raise AssertionError("restricted pages must not be bypassed")

    provider = ScraplingPublicEvidenceProvider(
        static_fetcher=static_fetcher,
        stealth_fetcher=stealth_fetcher,
        robots_policy=FakeRobotsPolicy(True),
        artifact_dir=tmp_path,
        allow_stealth=True,
    )

    with pytest.raises(ScraplingRestrictedPage):
        provider.fetch_page("https://venue.example/captcha")

    assert provider.stats.restricted_page_count == 1
    assert provider.stats.stealth_fetch_count == 0


def test_extract_image_candidates_from_metadata_and_jsonld() -> None:
    html = """
    <html>
      <head>
        <title>Grand Hotel Accra</title>
        <meta property="og:image" content="/og.jpg">
        <script type="application/ld+json">
          {"@type":"Hotel","image":["https://cdn.example/hotel.jpg"]}
        </script>
      </head>
      <body><img alt="Grand Hotel Accra exterior" src="/exterior.webp"></body>
    </html>
    """

    images = extract_image_candidates_from_html("https://hotel.example/grand", html)
    urls = [item["imageUrl"] for item in images]

    assert "https://hotel.example/og.jpg" in urls
    assert "https://cdn.example/hotel.jpg" in urls
    assert "https://hotel.example/exterior.webp" in urls
