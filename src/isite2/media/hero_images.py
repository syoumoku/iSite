from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, quote, unquote, urlparse

WIKIMEDIA_THUMB_WIDTH = 960
_DIRECT_THUMB_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def stable_hero_image_url(url: str, *, wikimedia_width: int = WIKIMEDIA_THUMB_WIDTH) -> str:
    """Return a UI-stable public image URL without changing non-Wikimedia URLs."""
    return hero_image_url_variants(url, wikimedia_width=wikimedia_width)[0]


def hero_image_url_variants(url: str, *, wikimedia_width: int = WIKIMEDIA_THUMB_WIDTH) -> list[str]:
    """Return candidate URLs to try for one real image, with stable URLs first."""
    raw_url = str(url or "").strip()
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").casefold()
    filename: str | None = None

    if host == "commons.wikimedia.org":
        filename = _commons_filename(parsed)
    elif host == "upload.wikimedia.org":
        filename = _upload_wikimedia_filename(parsed.path)

    if not filename:
        return [raw_url]
    width = max(320, int(wikimedia_width or WIKIMEDIA_THUMB_WIDTH))
    normalized_filename = unquote(filename).strip().replace(" ", "_")
    if not normalized_filename:
        return [raw_url]

    direct_thumb = _wikimedia_thumb_url(normalized_filename, width)
    redirect = _wikimedia_redirect_url(normalized_filename, width)
    variants: list[str] = []
    if host == "upload.wikimedia.org" and "/thumb/" in parsed.path:
        variants.append(raw_url)
    variants.extend([direct_thumb, redirect])
    if host == "upload.wikimedia.org" and "/thumb/" not in parsed.path:
        variants.append(raw_url)
    return _dedupe_urls(variants)


def _wikimedia_thumb_url(filename: str, width: int) -> str:
    encoded = quote(filename, safe="!$&'()*+,;=:@-._~")
    suffix = f"{width}px-{encoded}"
    lowered = filename.casefold()
    if lowered.endswith(".svg"):
        suffix = f"{suffix}.png"
    elif not any(lowered.endswith(extension) for extension in _DIRECT_THUMB_EXTENSIONS):
        return _wikimedia_redirect_url(filename, width)
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    return (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/"
        f"{digest[0]}/{digest[:2]}/{encoded}/{suffix}"
    )


def _wikimedia_redirect_url(filename: str, width: int) -> str:
    encoded = quote(filename, safe="")
    return f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{encoded}?width={width}"


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped or [""]


def _commons_filename(parsed) -> str | None:
    path = unquote(parsed.path or "")
    prefixes = (
        "/wiki/Special:FilePath/",
        "/wiki/Special:Redirect/file/",
        "/wiki/File:",
    )
    for prefix in prefixes:
        if path.startswith(prefix):
            return path[len(prefix) :]

    if path == "/w/index.php":
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        title = unquote(str(query.get("title") or ""))
        for prefix in ("Special:FilePath/", "Special:Redirect/file/", "File:"):
            if title.startswith(prefix):
                return title[len(prefix) :]
    return None


def _upload_wikimedia_filename(path: str) -> str | None:
    parts = [unquote(part) for part in (path or "").split("/") if part]
    if "thumb" in parts:
        index = parts.index("thumb")
        if len(parts) > index + 3:
            return parts[index + 3]
    if len(parts) >= 4 and parts[0] == "wikipedia":
        return parts[-1]
    return None
