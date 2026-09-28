from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from isite2.connectors.compliance import HttpRobotsPolicy, InMemorySourceCache, classify_source_tier
from isite2.connectors.models import FetchedPage


RESTRICTED_PAGE_PATTERNS = re.compile(
    r"(captcha|turnstile|verify you are human|sign in to continue|login required|"
    r"paywall|subscribe to continue|subscription required)",
    re.IGNORECASE,
)
ANTI_BOT_STATUS_CODES = {403, 408, 409, 425, 429, 500, 502, 503, 504}
JS_PLACEHOLDER_PATTERNS = re.compile(
    r"(enable javascript|requires javascript|app-root|__next|window\.__INITIAL_STATE__)",
    re.IGNORECASE,
)


class ScraplingProviderUnavailable(RuntimeError):
    pass


class ScraplingRestrictedPage(RuntimeError):
    pass


@dataclass
class ScraplingUsageStats:
    fetch_requests: int = 0
    static_fetch_count: int = 0
    dynamic_fetch_count: int = 0
    stealth_fetch_count: int = 0
    cache_hits: int = 0
    robots_disallowed_count: int = 0
    restricted_page_count: int = 0
    artifact_write_count: int = 0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ScraplingPublicEvidenceProvider:
    """Scrapling-backed page fetcher for public evidence URLs.

    Scrapling replaces Firecrawl's page extraction role only. Search discovery
    stays in dedicated search providers or retained manifests.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        cache: InMemorySourceCache | None = None,
        robots_policy: HttpRobotsPolicy | None = None,
        artifact_dir: str | Path = ".web_evidence/scrapling/pages",
        user_agent: str = "isite2-scrapling/0.1 public-evidence-research",
        timeout_ms: int = 30_000,
        min_content_chars: int = 240,
        allow_dynamic: bool | None = None,
        allow_stealth: bool | None = None,
        static_fetcher: Any | None = None,
        dynamic_fetcher: Any | None = None,
        stealth_fetcher: Any | None = None,
    ) -> None:
        self.cache = cache or InMemorySourceCache()
        self.robots_policy = robots_policy or HttpRobotsPolicy()
        self.artifact_dir = Path(artifact_dir)
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self.min_content_chars = min_content_chars
        self.allow_dynamic = _env_bool("SCRAPLING_ALLOW_DYNAMIC", True, allow_dynamic)
        self.allow_stealth = _env_bool("SCRAPLING_ALLOW_STEALTH", True, allow_stealth)
        self.static_fetcher = static_fetcher
        self.dynamic_fetcher = dynamic_fetcher
        self.stealth_fetcher = stealth_fetcher
        requested_enabled = _env_bool("SCRAPLING_ENABLED", True, enabled)
        self.enabled = requested_enabled and (
            bool(static_fetcher or dynamic_fetcher or stealth_fetcher) or _scrapling_available()
        )
        self.stats = ScraplingUsageStats()
        self._html_cache: dict[str, str] = {}
        if requested_enabled and not self.enabled:
            self.stats.warnings.append(
                "scrapling is not installed; install the optional sweep dependency with .[sweep]"
            )

    def fetch_page(self, url: str) -> FetchedPage:
        cached = self.cache.get(url)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached
        self.stats.fetch_requests += 1
        if not self.enabled:
            raise ScraplingProviderUnavailable(
                "Scrapling provider is disabled or scrapling[fetchers] is not installed"
            )
        if not self.robots_policy.allowed(url, self.user_agent):
            self.stats.robots_disallowed_count += 1
            page = FetchedPage(
                source_url=url,
                source_name=_source_name(url),
                source_tier=classify_source_tier(_source_name(url), url),
                content_text="",
                robots_allowed=False,
            )
            self._write_artifact(
                url=url,
                mode="robots_disallowed",
                page=page,
                status_code=None,
                html="",
                failure_reason="robots.txt disallowed",
            )
            return self.cache.put(page)

        attempts: list[tuple[str, Any, str, int | None]] = []
        response, html, status = self._attempt_fetch("static", url)
        attempts.append(("static", response, html, status))
        decision = self._decision(html, status)
        if decision == "restricted":
            self.stats.restricted_page_count += 1
            self._write_failure(url, attempts, "restricted public page; no bypass attempted")
            raise ScraplingRestrictedPage("restricted public page; no bypass attempted")
        if decision == "good":
            return self._page_from_attempt(url, "static", response, html, status)

        if decision == "anti_bot" and self.allow_stealth:
            response, html, status = self._attempt_fetch("stealth", url)
            attempts.append(("stealth", response, html, status))
            decision = self._decision(html, status)
            if decision == "restricted":
                self.stats.restricted_page_count += 1
                self._write_failure(url, attempts, "restricted public page after stealth fetch")
                raise ScraplingRestrictedPage("restricted public page after stealth fetch")
            if decision == "good":
                return self._page_from_attempt(url, "stealth", response, html, status)

        if decision in {"needs_dynamic", "short"} and self.allow_dynamic:
            response, html, status = self._attempt_fetch("dynamic", url)
            attempts.append(("dynamic", response, html, status))
            decision = self._decision(html, status)
            if decision == "restricted":
                self.stats.restricted_page_count += 1
                self._write_failure(url, attempts, "restricted public page after dynamic fetch")
                raise ScraplingRestrictedPage("restricted public page after dynamic fetch")
            if decision == "good":
                return self._page_from_attempt(url, "dynamic", response, html, status)
            if decision == "anti_bot" and self.allow_stealth:
                response, html, status = self._attempt_fetch("stealth", url)
                attempts.append(("stealth", response, html, status))
                decision = self._decision(html, status)
                if decision == "restricted":
                    self.stats.restricted_page_count += 1
                    self._write_failure(url, attempts, "restricted public page after stealth fetch")
                    raise ScraplingRestrictedPage("restricted public page after stealth fetch")
                if decision == "good":
                    return self._page_from_attempt(url, "stealth", response, html, status)

        mode, response, html, status = attempts[-1]
        return self._page_from_attempt(url, mode, response, html, status)

    def extract_image_candidates(self, url: str) -> list[dict[str, Any]]:
        page = self.fetch_page(url)
        html = self._html_cache.get(str(page.source_url)) or self._html_cache.get(url) or ""
        return extract_image_candidates_from_html(str(page.source_url), html)

    def _attempt_fetch(self, mode: str, url: str) -> tuple[Any, str, int | None]:
        if mode == "static":
            self.stats.static_fetch_count += 1
            fetcher = self.static_fetcher or _scrapling_fetcher("Fetcher", "get")
            kwargs = {
                "timeout": max(1, int(self.timeout_ms / 1000)),
                "stealthy_headers": True,
                "follow_redirects": "safe",
                "headers": {"User-Agent": self.user_agent},
            }
        elif mode == "dynamic":
            self.stats.dynamic_fetch_count += 1
            fetcher = self.dynamic_fetcher or _scrapling_fetcher("DynamicFetcher", "fetch")
            kwargs = {
                "timeout": self.timeout_ms,
                "headless": True,
                "network_idle": True,
                "disable_resources": True,
                "block_ads": True,
                "useragent": self.user_agent,
            }
        else:
            self.stats.stealth_fetch_count += 1
            fetcher = self.stealth_fetcher or _scrapling_fetcher("StealthyFetcher", "fetch")
            kwargs = {
                "timeout": self.timeout_ms,
                "headless": True,
                "network_idle": True,
                "disable_resources": True,
                "block_ads": True,
            }
        response = _call_fetcher(fetcher, url, kwargs)
        html = _response_html(response)
        return response, html, _status_code(response)

    def _decision(self, html: str, status_code: int | None) -> str:
        text = _html_to_text(html)
        if RESTRICTED_PAGE_PATTERNS.search(f"{html[:4000]} {text[:4000]}"):
            return "restricted"
        if status_code in ANTI_BOT_STATUS_CODES:
            return "anti_bot"
        if len(text) < self.min_content_chars:
            return "short"
        if JS_PLACEHOLDER_PATTERNS.search(html) and len(text) < self.min_content_chars * 3:
            return "needs_dynamic"
        return "good"

    def _page_from_attempt(
        self,
        requested_url: str,
        mode: str,
        response: Any,
        html: str,
        status_code: int | None,
    ) -> FetchedPage:
        source_url = _response_url(response) or requested_url
        source_name = _source_name(source_url)
        content_text = _html_to_text(html)
        page = FetchedPage(
            source_url=source_url,
            source_name=source_name,
            source_tier=classify_source_tier(source_name, source_url),
            source_date=_source_date(response),
            fetched_at=datetime.now(UTC),
            content_text=content_text,
            robots_allowed=True,
        )
        self._html_cache[source_url] = html
        self._html_cache[requested_url] = html
        self._write_artifact(
            url=requested_url,
            mode=mode,
            page=page,
            status_code=status_code,
            html=html,
            failure_reason=None,
        )
        return self.cache.put(page)

    def _write_failure(
        self,
        url: str,
        attempts: list[tuple[str, Any, str, int | None]],
        failure_reason: str,
    ) -> None:
        mode, response, html, status = attempts[-1]
        page = FetchedPage(
            source_url=_response_url(response) or url,
            source_name=_source_name(_response_url(response) or url),
            source_tier=classify_source_tier(_source_name(_response_url(response) or url), url),
            content_text=_html_to_text(html),
            robots_allowed=True,
        )
        self._write_artifact(
            url=url,
            mode=mode,
            page=page,
            status_code=status,
            html=html,
            failure_reason=failure_reason,
        )
        self.stats.failures.append(f"{url}: {failure_reason}")

    def _write_artifact(
        self,
        *,
        url: str,
        mode: str,
        page: FetchedPage,
        status_code: int | None,
        html: str,
        failure_reason: str | None,
    ) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256((page.content_text or "").encode("utf-8")).hexdigest()
        artifact = {
            "provider": "scrapling",
            "url": url,
            "source_url": str(page.source_url),
            "fetcher_mode": mode,
            "status_code": status_code,
            "source_date": page.source_date,
            "fetched_at": page.fetched_at.isoformat(),
            "content_sha256": content_hash,
            "content_text": page.content_text,
            "links": extract_links_from_html(str(page.source_url), html),
            "image_candidates": extract_image_candidates_from_html(str(page.source_url), html),
            "robots_allowed": page.robots_allowed,
            "failure_reason": failure_reason,
        }
        key = hashlib.sha256(f"{url}|{mode}|{content_hash}".encode("utf-8")).hexdigest()
        path = self.artifact_dir / key[:2] / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stats.artifact_write_count += 1


def extract_image_candidates_from_html(source_url: str, html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or source_url
    urls: list[dict[str, Any]] = []

    for selector, attr in [
        ('meta[property="og:image"]', "content"),
        ('meta[property="og:image:secure_url"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('link[rel="image_src"]', "href"),
    ]:
        for node in soup.select(selector):
            value = str(node.get(attr) or "").strip()
            if value:
                urls.append({"imageUrl": urljoin(source_url, value), "title": title, "url": source_url})

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.get_text(strip=True))
        except json.JSONDecodeError:
            continue
        for image in _jsonld_images(payload):
            urls.append({"imageUrl": urljoin(source_url, image), "title": title, "url": source_url})

    for image in soup.find_all("img", src=True):
        src = str(image.get("src") or "").strip()
        if src:
            urls.append(
                {
                    "imageUrl": urljoin(source_url, src),
                    "title": str(image.get("alt") or title),
                    "url": source_url,
                }
            )
    return _dedupe_image_candidates(urls)


def extract_links_from_html(source_url: str, html: str) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for node in soup.find_all("a", href=True):
        href = str(node.get("href") or "").strip()
        if href:
            links.append(urljoin(source_url, href))
    return sorted(set(links))


def _jsonld_images(payload: Any) -> list[str]:
    images: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            images.extend(_jsonld_images(item))
    elif isinstance(payload, dict):
        image = payload.get("image")
        if isinstance(image, str):
            images.append(image)
        elif isinstance(image, list):
            images.extend(str(item) for item in image if isinstance(item, str))
        elif isinstance(image, dict):
            url = image.get("url") or image.get("contentUrl")
            if isinstance(url, str):
                images.append(url)
        graph = payload.get("@graph")
        if graph is not None:
            images.extend(_jsonld_images(graph))
    return images


def _dedupe_image_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        url = str(item.get("imageUrl") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(item)
    return result


def _scrapling_available() -> bool:
    try:
        import scrapling.fetchers  # noqa: F401
    except Exception:
        return False
    return True


def _scrapling_fetcher(class_name: str, method_name: str) -> Any:
    try:
        module = __import__("scrapling.fetchers", fromlist=[class_name])
        cls = getattr(module, class_name)
        return getattr(cls, method_name)
    except Exception as exc:  # pragma: no cover - depends on optional dependency
        raise ScraplingProviderUnavailable(
            "scrapling[fetchers] is required for live Scrapling fetches"
        ) from exc


def _call_fetcher(fetcher: Any, url: str, kwargs: dict[str, Any]) -> Any:
    try:
        return fetcher(url, **kwargs)
    except TypeError:
        return fetcher(url)


def _response_html(response: Any) -> str:
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body.decode(getattr(response, "encoding", None) or "utf-8", errors="replace")
    text = getattr(response, "text", None)
    if callable(text):
        try:
            return str(text())
        except TypeError:
            pass
    if isinstance(text, str):
        return text
    html = getattr(response, "html", None)
    if isinstance(html, str):
        return html
    return str(response or "")


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _status_code(response: Any) -> int | None:
    for attr in ("status", "status_code"):
        value = getattr(response, attr, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _response_url(response: Any) -> str | None:
    value = getattr(response, "url", None)
    return str(value) if value else None


def _source_date(response: Any) -> str | None:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, dict):
        try:
            headers = dict(headers or {})
        except Exception:
            headers = {}
    return headers.get("last-modified") or headers.get("Last-Modified")


def _source_name(url: str) -> str:
    return urlparse(url).netloc or "Scrapling Source"


def _env_bool(name: str, default: bool, override: bool | None = None) -> bool:
    if override is not None:
        return override
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}
