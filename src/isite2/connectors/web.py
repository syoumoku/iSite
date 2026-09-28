from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

import yaml

from isite2.connectors.models import FetchedPage, SearchResult


class SearchProvider(Protocol):
    @property
    def enabled(self) -> bool: ...

    def search(self, query: str, limit: int = 5) -> list[SearchResult]: ...


class PageFetchProvider(Protocol):
    @property
    def enabled(self) -> bool: ...

    def fetch_page(self, url: str) -> FetchedPage: ...


REQUIRED_MANIFEST_FIELDS = {
    "language",
    "search_channel",
    "query",
    "title",
    "url",
    "snippet",
    "adopted",
    "skipped_reason",
    "target_scene",
}


class SearchManifestValidationError(ValueError):
    pass


class SearchManifestProvider:
    """Replay retained search manifests as a zero-credit search provider.

    Scrapling fetches pages but does not provide an internet-wide search API.
    This provider lets sweep flows consume ChatGPT/Web/browser/local-search
    manifests with the same `SearchResult` shape used by existing discovery and
    backfill code.
    """

    def __init__(
        self,
        paths: Iterable[str | Path] | None = None,
        *,
        strict: bool = True,
        source_name: str = "Search Manifest",
    ) -> None:
        self.paths = [Path(path) for path in paths or []]
        self.strict = strict
        self.source_name = source_name
        self.rows = self._load_rows(self.paths)
        self.search_requests = 0

    @classmethod
    def from_path_value(
        cls,
        value: str | None,
        *,
        strict: bool = True,
    ) -> "SearchManifestProvider":
        paths: list[Path] = []
        for part in str(value or "").replace(";", ",").split(","):
            part = part.strip()
            if part:
                paths.append(Path(part))
        return cls(paths, strict=strict)

    @property
    def enabled(self) -> bool:
        return bool(self.rows)

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        self.search_requests += 1
        normalized_query = _norm(query)
        results: list[SearchResult] = []
        for row in self.rows:
            if _norm(str(row.get("query") or "")) != normalized_query:
                continue
            if not _truthy(row.get("adopted")):
                continue
            results.append(
                SearchResult(
                    title=str(row["title"]),
                    url=str(row["url"]),
                    source_name=str(row.get("source_name") or self.source_name),
                    snippet=str(row.get("snippet") or ""),
                )
            )
            if len(results) >= limit:
                break
        return results

    def _load_rows(self, paths: list[Path]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in paths:
            for file_path in _expand_manifest_path(path):
                payloads = _read_manifest_payloads(file_path)
                for payload in payloads:
                    for row in _manifest_rows(payload):
                        self._validate_row(row, file_path)
                        rows.append(row)
        return rows

    def _validate_row(self, row: dict[str, Any], path: Path) -> None:
        missing = sorted(field for field in REQUIRED_MANIFEST_FIELDS if field not in row)
        if not missing:
            return
        message = f"search manifest row in {path} missing required fields: {', '.join(missing)}"
        if self.strict:
            raise SearchManifestValidationError(message)


def _expand_manifest_path(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    patterns = ("*.json", "*.jsonl", "*.yaml", "*.yml")
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(path.rglob(pattern)))
    return files


def _read_manifest_payloads(path: Path) -> list[Any]:
    if path.suffix.casefold() == ".jsonl":
        payloads = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payloads.append(json.loads(line))
        return payloads
    text = path.read_text(encoding="utf-8")
    if path.suffix.casefold() in {".yaml", ".yml"}:
        return [yaml.safe_load(text) or {}]
    return [json.loads(text)]


def _manifest_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "items", "rows", "search_results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        web = data.get("web") or data.get("results") or data.get("items")
        if isinstance(web, list):
            return [item for item in web if isinstance(item, dict)]
    return [payload] if {"query", "url", "title"}.issubset(payload.keys()) else []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "adopted"}


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())
