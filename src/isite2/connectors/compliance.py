from __future__ import annotations

import hashlib
import urllib.robotparser
from collections import deque
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

from isite2.connectors.models import FetchedPage
from isite2.domain.enums import SourceTier


class RobotsPolicy:
    """MVP robots policy interface.

    The default implementation is conservative and only blocks explicit deny-list domains.
    A real implementation should fetch and cache robots.txt before page fetching.
    """

    def __init__(self, blocked_domains: set[str] | None = None) -> None:
        self.blocked_domains = blocked_domains or set()

    def allowed(self, url: str, user_agent: str = "isite2") -> bool:
        domain = urlparse(url).netloc.lower()
        return domain not in self.blocked_domains


class HttpRobotsPolicy:
    def __init__(
        self,
        client: httpx.Client | None = None,
        allow_on_error: bool = False,
        timeout_seconds: float = 10,
    ) -> None:
        self.client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)
        self.allow_on_error = allow_on_error
        self.parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str, user_agent: str = "isite2") -> bool:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        parser = self.parsers.get(base_url)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{base_url}/robots.txt"
            try:
                response = self.client.get(
                    robots_url,
                    headers={"User-Agent": user_agent},
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return self.allow_on_error
            parser.parse(response.text.splitlines())
            self.parsers[base_url] = parser
        return parser.can_fetch(user_agent, url)


class RateLimiter:
    def __init__(self, max_calls: int = 5, period_seconds: int = 60) -> None:
        self.max_calls = max_calls
        self.period = timedelta(seconds=period_seconds)
        self.calls: deque[datetime] = deque()

    def allow(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        while self.calls and current - self.calls[0] > self.period:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(current)
        return True


class InMemorySourceCache:
    def __init__(self) -> None:
        self._cache: dict[str, FetchedPage] = {}
        self._hashes: dict[str, str] = {}

    def put(self, page: FetchedPage) -> FetchedPage:
        url = str(page.source_url)
        self._cache[url] = page
        self._hashes[url] = hashlib.sha256(page.content_text.encode("utf-8")).hexdigest()
        return page

    def get(self, url: str) -> FetchedPage | None:
        return self._cache.get(url)

    def content_hash(self, url: str) -> str | None:
        return self._hashes.get(url)


def classify_source_tier(source_name: str, url: str) -> SourceTier:
    text = f"{source_name} {url}".lower()
    if any(token in text for token in ("wikipedia.org", "wikidata.org")):
        return SourceTier.TIER_3
    if any(token in text for token in (".gov", ".edu", "official", "annual report")):
        return SourceTier.TIER_1
    if any(token in text for token in ("association", "exchange", "consulting", "authority")):
        return SourceTier.TIER_2
    return SourceTier.TIER_3
