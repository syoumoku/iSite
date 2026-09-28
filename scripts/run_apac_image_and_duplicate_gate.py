from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import yaml
from sqlalchemy import select

from isite2.connectors.firecrawl import firecrawl_subprocess_env
from isite2.connectors.scrapling import ScraplingPublicEvidenceProvider
from isite2.db.models import PropertyDB
from isite2.growth.evidence_intake import DEFAULT_OVERLAY_PATH, write_registry_overlay
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import normalize_property_name, property_identity_key
from isite2.media.hero_images import hero_image_url_variants, stable_hero_image_url
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_URL = "sqlite+pysqlite:///outputs/isite2_dev.db"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
DEFAULT_CACHE_DIR = Path(".web_evidence") / "candidate_image_gate" / "image_search"
IMAGE_OPEN_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
PROXY_HERO_IMAGE_TTL_SECONDS = 30 * 24 * 60 * 60
PROXY_HERO_IMAGE_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_COUNTRIES = ("Sri Lanka", "Cambodia", "Maldives")
SOURCE_DATE = "2026-05-14"

BAD_IMAGE_TOKENS = (
    "placeholder",
    "logo",
    "icon",
    "googleads",
    "blank",
)
LOW_VALUE_IMAGE_HOST_TOKENS = (
    "kupi.com",
    "doubleclick",
    "googleads",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "pbs.twimg.com",
    "youtube.com",
    "youtu.be",
    "ytimg.com",
    "lookaside.fbsbx",
    "lookaside.instagram",
    "alamy",
    "gettyimages",
    "shutterstock",
    "dreamstime",
    "depositphotos",
    "yandex",
    "leonardo.ai",
)
PREFERRED_HOST_TOKENS = (
    "tripadvisor",
    "marriott.com",
    "hyatt.com",
    "hilton.com",
    "accor",
    "ihg.com",
    "shangri-la.com",
    "tajhotels.com",
    "cinnamonhotels.com",
    "jetwinghotels.com",
    "aeonmall",
    "airport",
    "stadium",
    "official",
)
FALLBACK_IMAGE_HOST_TOKENS = (
    "wikimedia.org",
    "wikipedia.org",
)

GENERIC_PROPERTY_IDENTITY_TOKENS = {
    "academy",
    "administrative",
    "airport",
    "building",
    "capital",
    "center",
    "centre",
    "complex",
    "conference",
    "cruise",
    "government",
    "headquarters",
    "hospital",
    "hotel",
    "international",
    "mall",
    "medical",
    "metro",
    "ministry",
    "mosque",
    "new",
    "office",
    "palace",
    "port",
    "railway",
    "resort",
    "spa",
    "specialized",
    "stadium",
    "station",
    "terminal",
    "theatre",
    "tower",
    "train",
    "university",
}

SCENE_IMAGE_IDENTITY_TOKENS = {
    "airport_terminal": {"airport", "terminal"},
    "convention_center": {"conference", "convention", "centre", "center", "theatre"},
    "cruise_port": {"cruise", "port", "terminal"},
    "hospital": {"hospital", "medical"},
    "luxury_hotel_mice": {"hotel", "resort", "palace"},
    "mall_mixed_use": {"mall", "shopping", "plaza"},
    "mosque": {"mosque", "masjid"},
    "office_government": {"office", "headquarters", "tower", "building"},
    "stadium": {"stadium", "arena"},
    "transport_hub": {"station", "railway", "train", "metro", "terminal"},
    "university": {"university", "campus", "college"},
}

DUPLICATE_CANONICAL_NAMES = {
    ("Cambodia", "airport_terminal", "Techo International Airport (KTI)"): (
        "Techo International Airport"
    ),
    ("Cambodia", "mall_mixed_use", "Aeon Mall Mean Chey (Aeon 3)"): "AEON Mall Mean Chey",
    ("Cambodia", "office_government", "Exchange Square"): "Exchange Square Phnom Penh",
    ("Maldives", "airport_terminal", "Velana International Airport"): (
        "Velana International Airport"
    ),
    ("Maldives", "airport_terminal", "Gan International Airport"): "Gan International Airport",
}


class _SourcePageImageCache:
    """Share one Scrapling provider and one in-flight fetch per URL within a run."""

    def __init__(self, provider: ScraplingPublicEvidenceProvider) -> None:
        self.provider = provider
        self._lock = threading.Lock()
        self._futures: dict[str, concurrent.futures.Future[list[dict[str, Any]]]] = {}

    def extract(self, url: str) -> list[dict[str, Any]]:
        normalized = url.rstrip("/").casefold()
        with self._lock:
            future = self._futures.get(normalized)
            owner = future is None
            if future is None:
                future = concurrent.futures.Future()
                self._futures[normalized] = future
        if owner:
            try:
                future.set_result(self.provider.extract_image_candidates(url))
            except Exception as exc:
                future.set_exception(exc)
        return future.result()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Merge known duplicate variants, replace placeholder hero images, "
            "and sync active candidates."
        )
    )
    parser.add_argument("--countries", nargs="+", default=list(DEFAULT_COUNTRIES))
    parser.add_argument(
        "--all-countries",
        action="store_true",
        help="Process every country in the overlay instead of the --countries list.",
    )
    parser.add_argument("--overlay-path", type=Path, default=DEFAULT_OVERLAY_PATH)
    parser.add_argument("--db-url", default=DB_URL)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--duplicate-alias-map",
        type=Path,
        default=None,
        help=(
            "Optional JSON/YAML aliases. Accepts a list of records with "
            "country/scene_type/from/to or a mapping of 'country|scene|from' to 'to'."
        ),
    )
    parser.add_argument("--source-date", default=SOURCE_DATE)
    parser.add_argument("--image-limit", type=int, default=5)
    parser.add_argument(
        "--image-query-profile",
        choices=("default", "local_official"),
        default="default",
        help="Image discovery query vocabulary; local_official uses the country's preferred language.",
    )
    parser.add_argument(
        "--image-attempt-id",
        default=None,
        help="Stable identifier used to retain this independent image-search attempt on each candidate.",
    )
    parser.add_argument(
        "--max-image-searches",
        type=int,
        default=None,
        help="Maximum invalid/broken/missing-image candidates to search in this run.",
    )
    parser.add_argument(
        "--firecrawl-credit-budget",
        type=int,
        default=10000,
        help=(
            "Conservative Firecrawl credit budget for explicit fallback image search. "
            "Ignored unless --allow-firecrawl-fallback is set."
        ),
    )
    parser.add_argument(
        "--allow-firecrawl-fallback",
        action="store_true",
        help="Allow Firecrawl image search if Scrapling cannot find a usable image on candidate pages.",
    )
    parser.add_argument(
        "--skip-source-page-image-extraction",
        action="store_true",
        help=(
            "Skip Scrapling image extraction for multi-property directory pages and go "
            "directly to the retained local Firecrawl image-search fallback."
        ),
    )
    parser.add_argument(
        "--estimated-image-search-credit-cost",
        type=float,
        default=4.0,
        help="Conservative estimated Firecrawl credits per image search for budget capping.",
    )
    parser.add_argument(
        "--fill-missing-images",
        action="store_true",
        help=(
            "Compatibility flag; missing images are filled by default when image search runs."
        ),
    )
    parser.add_argument(
        "--skip-missing-image-fill",
        action="store_true",
        help=(
            "Only repair broken/invalid existing image URLs and do not spend Firecrawl "
            "on candidates with no hero_image."
        ),
    )
    parser.add_argument(
        "--remove-no-image",
        action="store_true",
        help=(
            "Remove candidates when no replacement image is found. "
            "Default keeps them without hero_image."
        ),
    )
    parser.add_argument(
        "--validate-existing-images",
        action="store_true",
        help="HTTP-check existing hero images and treat broken URLs as invalid.",
    )
    parser.add_argument("--image-check-timeout", type=float, default=6.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--skip-image-search", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with args.overlay_path.open("r", encoding="utf-8") as handle:
        overlay = yaml.safe_load(handle) or {"version": "runtime-0.1", "countries": {}}

    selected_countries = _selected_countries(overlay, args.countries, args.all_countries)
    duplicate_aliases = _load_duplicate_alias_map(args.duplicate_alias_map)
    merge_result = _merge_duplicate_variants(overlay, selected_countries, duplicate_aliases)
    image_open_checks: list[dict[str, Any]] = []
    broken_image_keys: set[str] = set()
    if args.validate_existing_images:
        image_open_checks = _check_existing_images(
            overlay,
            selected_countries,
            args.cache_dir.parent / "open_check",
            args.image_check_timeout,
            args.max_workers,
        )
        broken_image_keys = {
            item["candidate_key"]
            for item in image_open_checks
            if not item.get("ok") and not _image_check_is_retryable(item)
        }
    missing_image_total = _missing_image_candidate_count(overlay, selected_countries)
    include_missing_images = args.fill_missing_images or not args.skip_missing_image_fill
    invalid = _invalid_image_candidates(
        overlay,
        selected_countries,
        broken_image_keys,
        include_missing=include_missing_images,
    )
    invalid_total = len(invalid)
    invalid = _prioritize_image_candidates(invalid)
    requested_image_search_count = len(invalid)
    budget_search_cap = (
        _image_search_budget_cap(
            args.firecrawl_credit_budget,
            args.estimated_image_search_credit_cost,
        )
        if args.allow_firecrawl_fallback
        else None
    )
    search_caps = []
    if budget_search_cap is not None:
        search_caps.append(budget_search_cap)
    if args.max_image_searches is not None:
        search_caps.append(max(0, args.max_image_searches))
    if search_caps:
        invalid = invalid[: min(search_caps)]
    invalid_keys = {_candidate_key(country, candidate) for country, candidate in invalid}

    image_search_results: list[dict[str, Any]] = []
    source_page_cache = None
    if invalid and not args.skip_image_search:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        if not args.skip_source_page_image_extraction:
            source_page_cache = _SourcePageImageCache(
                ScraplingPublicEvidenceProvider(
                    artifact_dir=args.cache_dir.parent / "scrapling_pages",
                )
            )
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = [
                executor.submit(
                    _search_image_for_candidate,
                    country,
                    candidate,
                    args.image_limit,
                    args.cache_dir,
                    args.source_date,
                    args.allow_firecrawl_fallback,
                    args.skip_source_page_image_extraction,
                    source_page_cache,
                    args.image_query_profile,
                )
                for country, candidate in invalid
            ]
            for future in concurrent.futures.as_completed(futures):
                image_search_results.append(future.result())

    replacement_by_key = {
        item["candidate_key"]: item
        for item in image_search_results
        if item.get("accepted_image")
    }
    search_result_by_key = {item["candidate_key"]: item for item in image_search_results}
    removed: list[dict[str, Any]] = []
    replaced: list[dict[str, Any]] = []
    for country in selected_countries:
        country_registry = overlay.get("countries", {}).get(country)
        if not country_registry:
            continue
        retained: list[dict[str, Any]] = []
        for candidate in country_registry.get("candidates", []) or []:
            key = _candidate_key(country, candidate)
            if key not in invalid_keys:
                retained.append(candidate)
                continue
            replacement = replacement_by_key.get(key)
            if args.image_attempt_id and key in search_result_by_key:
                _record_image_search_attempt(
                    candidate,
                    args.image_attempt_id,
                    search_result_by_key[key],
                )
            if replacement:
                candidate["hero_image"] = replacement["accepted_image"]
                accepted_image = replacement["accepted_image"]
                audit = accepted_image.get("audit") or {}
                replaced.append(
                    {
                        "country": country,
                        "city": candidate.get("city"),
                        "property_name": candidate.get("property_name"),
                        "scene_type": candidate.get("scene_type"),
                        "image_url": accepted_image.get("url"),
                        "image_source_name": accepted_image.get("source_name"),
                        "image_source_url": accepted_image.get("source_url"),
                        "image_source_title": audit.get("source_title"),
                        "original_image_url": audit.get("original_image_url"),
                        "match_reason": audit.get("match_reason"),
                        "match_property_tokens": audit.get("property_tokens"),
                        "match_geo_tokens": audit.get("geo_tokens"),
                        "result_rank": audit.get("result_rank"),
                        "image_width": audit.get("image_width"),
                        "score": audit.get("score"),
                        "query": replacement.get("query"),
                        "cache_path": replacement.get("cache_path"),
                    }
                )
                retained.append(candidate)
            else:
                candidate.pop("hero_image", None)
                removed_item = {
                    "country": country,
                    "city": candidate.get("city"),
                    "property_name": candidate.get("property_name"),
                    "scene_type": candidate.get("scene_type"),
                    "reason": "no real property image found",
                    "action": "removed" if args.remove_no_image else "kept_without_image",
                }
                removed.append(removed_item)
                if not args.remove_no_image:
                    retained.append(candidate)
        country_registry["candidates"] = retained

    _refresh_identity_keys(overlay, selected_countries)

    sync = None
    active_image_cleanup = None
    if not args.dry_run:
        backup_path = args.overlay_path.with_suffix(
            args.overlay_path.suffix
            + f".before_candidate_image_gate_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        backup_path.write_text(args.overlay_path.read_text(encoding="utf-8"), encoding="utf-8")
        write_registry_overlay(args.overlay_path, overlay)
        if not args.skip_sync:
            repository = SQLAlchemyScanRunRepository.from_url(args.db_url, storage_mode="sqlite")
            sync = sync_overlay_to_active_repository(repository)
            active_image_cleanup = _clear_low_value_active_hero_images(
                repository,
                selected_countries,
            )
    else:
        backup_path = None

    summary = {
        "mode": "candidate_image_and_duplicate_gate",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "countries": sorted(selected_countries),
        "all_countries": args.all_countries,
        "duplicate_alias_map": str(args.duplicate_alias_map) if args.duplicate_alias_map else None,
        "duplicate_merge": merge_result,
        "existing_image_open_check_count": len(image_open_checks),
        "broken_existing_image_count": len(broken_image_keys),
        "temporary_rate_limited_image_count": sum(
            1 for item in image_open_checks if _image_check_is_retryable(item)
        ),
        "missing_image_candidate_count": missing_image_total,
        "fill_missing_images": include_missing_images,
        "skip_missing_image_fill": args.skip_missing_image_fill,
        "initial_invalid_image_total_count": invalid_total,
        "max_image_searches": args.max_image_searches,
        "firecrawl_credit_budget": args.firecrawl_credit_budget,
        "firecrawl_fallback_enabled": args.allow_firecrawl_fallback,
        "image_query_profile": args.image_query_profile,
        "image_attempt_id": args.image_attempt_id,
        "image_search_provider": (
            "scrapling_page_images_with_firecrawl_fallback"
            if args.allow_firecrawl_fallback
            else "scrapling_page_images"
        ),
        "estimated_image_search_credit_cost": args.estimated_image_search_credit_cost,
        "budget_search_cap": budget_search_cap,
        "requested_image_search_count_before_cap": requested_image_search_count,
        "broken_existing_images": [
            {
                "country": item.get("country"),
                "property_name": item.get("property_name"),
                "scene_type": item.get("scene_type"),
                "reason": item.get("reason"),
                "status_code": item.get("status_code"),
                "content_type": item.get("content_type"),
            }
            for item in image_open_checks
            if not item.get("ok")
        ][:100],
        "initial_invalid_image_count": len(invalid),
        "image_search_count": len(image_search_results),
        "scrapling_image_provider_stats": (
            _scrapling_image_stats(source_page_cache.provider)
            if source_page_cache is not None
            else _sum_scrapling_image_stats(image_search_results)
        ),
        "firecrawl_image_fallback_count": sum(
            1 for item in image_search_results if item.get("provider") == "firecrawl_image_search"
        ),
        "image_replaced_count": len(replaced),
        "removed_no_image_count": sum(1 for item in removed if item["action"] == "removed"),
        "kept_without_image_count": sum(
            1 for item in removed if item["action"] == "kept_without_image"
        ),
        "replaced": replaced,
        "removed_no_image": removed,
        "image_search_failures": [
            {
                "country": item.get("country"),
                "property_name": item.get("property_name"),
                "scene_type": item.get("scene_type"),
                "reason": item.get("reason"),
            }
            for item in image_search_results
            if not item.get("accepted_image")
        ],
        "remaining_invalid_images": _remaining_invalid_counts(overlay, selected_countries),
        "overlay_counts": _counts(overlay, selected_countries),
        "backup_path": str(backup_path) if backup_path else None,
        "overlay_sync": _sync_summary(sync),
        "active_low_value_hero_image_cleanup": active_image_cleanup,
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / f"candidate_image_duplicate_gate_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _selected_countries(
    overlay: dict[str, Any],
    countries: list[str],
    all_countries: bool,
) -> set[str]:
    if all_countries:
        return set(overlay.get("countries", {}).keys())
    return set(countries)


def _load_duplicate_alias_map(path: Path | None) -> dict[tuple[str, str, str], str]:
    aliases = dict(DUPLICATE_CANONICAL_NAMES)
    if path is None:
        return aliases
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(payload, dict) and "aliases" in payload:
        payload = payload["aliases"]
    if isinstance(payload, dict):
        for key, canonical in payload.items():
            parts = [part.strip() for part in str(key).split("|")]
            if len(parts) == 3 and canonical:
                aliases[(parts[0], parts[1], parts[2])] = str(canonical)
        return aliases
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            country = str(item.get("country") or "").strip()
            scene_type = str(item.get("scene_type") or "").strip()
            from_name = str(item.get("from") or item.get("alias") or "").strip()
            to_name = str(item.get("to") or item.get("canonical") or "").strip()
            if country and scene_type and from_name and to_name:
                aliases[(country, scene_type, from_name)] = to_name
    return aliases


def _merge_duplicate_variants(
    overlay: dict[str, Any],
    countries: set[str],
    duplicate_aliases: dict[tuple[str, str, str], str],
) -> dict[str, Any]:
    merged_records: list[dict[str, Any]] = []
    for country in countries:
        country_registry = overlay.get("countries", {}).get(country)
        if not country_registry:
            continue
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        retained_order: list[tuple[str, str]] = []
        for candidate in country_registry.get("candidates", []) or []:
            original_name = str(candidate.get("property_name") or "")
            scene_type = str(candidate.get("scene_type") or "")
            canonical_name = duplicate_aliases.get(
                (country, scene_type, original_name),
                original_name,
            )
            group_key = (scene_type, normalize_property_name(canonical_name))
            if group_key not in groups:
                candidate = dict(candidate)
                candidate["property_name"] = canonical_name
                groups[group_key] = candidate
                retained_order.append(group_key)
                continue
            target = groups[group_key]
            _merge_candidate(target, candidate, country, canonical_name)
            merged_records.append(
                {
                    "country": country,
                    "from": original_name,
                    "into": target.get("property_name"),
                    "scene_type": scene_type,
                }
            )
        country_registry["candidates"] = [groups[key] for key in retained_order]
    return {
        "merged_count": len(merged_records),
        "merged": merged_records,
    }


def _merge_candidate(
    target: dict[str, Any],
    incoming: dict[str, Any],
    country: str,
    canonical_name: str,
) -> None:
    target["property_name"] = canonical_name
    if not target.get("city") and incoming.get("city"):
        target["city"] = incoming["city"]
    if not target.get("coordinate") and incoming.get("coordinate"):
        target["coordinate"] = incoming["coordinate"]
    if _image_is_invalid(target) and not _image_is_invalid(incoming):
        target["hero_image"] = incoming.get("hero_image")
    existing_evidence = target.setdefault("evidence", [])
    existing_keys = {_evidence_key(evidence) for evidence in existing_evidence}
    for evidence in incoming.get("evidence", []) or []:
        key = _evidence_key(evidence)
        if key in existing_keys:
            continue
        existing_evidence.append(evidence)
        existing_keys.add(key)
    aliases = set(target.get("aliases") or [])
    original = incoming.get("property_name")
    if original and original != canonical_name:
        aliases.add(original)
    target["aliases"] = sorted(aliases)
    target["property_identity_key"] = property_identity_key(
        country=country,
        city=str(target.get("city") or ""),
        property_name=str(target.get("property_name") or ""),
        scene_type=str(target.get("scene_type") or ""),
    )


def _invalid_image_candidates(
    overlay: dict[str, Any],
    countries: set[str],
    broken_image_keys: set[str] | None = None,
    *,
    include_missing: bool = True,
) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    broken_image_keys = broken_image_keys or set()
    for country in countries:
        country_registry = overlay.get("countries", {}).get(country)
        if not country_registry:
            continue
        for candidate in country_registry.get("candidates", []) or []:
            key = _candidate_key(country, candidate)
            if key in broken_image_keys:
                result.append((country, candidate))
                continue
            if _image_is_missing(candidate):
                if include_missing:
                    result.append((country, candidate))
                continue
            if _image_is_invalid(candidate):
                result.append((country, candidate))
    return result


def _missing_image_candidate_count(overlay: dict[str, Any], countries: set[str]) -> int:
    count = 0
    for country in countries:
        for candidate in overlay.get("countries", {}).get(country, {}).get("candidates", []) or []:
            if _image_is_missing(candidate):
                count += 1
    return count


def _image_search_budget_cap(
    firecrawl_credit_budget: int | None,
    estimated_image_search_credit_cost: float,
) -> int | None:
    if firecrawl_credit_budget is None or firecrawl_credit_budget <= 0:
        return None
    per_search = max(float(estimated_image_search_credit_cost), 0.1)
    return max(0, int(firecrawl_credit_budget // per_search))


def _prioritize_image_candidates(
    candidates: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    scene_priority = {
        "airport_terminal": 0,
        "transport_hub": 1,
        "stadium": 2,
        "convention_center": 3,
        "luxury_hotel_mice": 4,
        "mall_mixed_use": 5,
        "office_government": 6,
        "hospital": 8,
        "university": 9,
    }
    return sorted(
        candidates,
        key=lambda item: (
            scene_priority.get(str(item[1].get("scene_type") or ""), 7),
            str(item[0] or ""),
            str(item[1].get("city") or ""),
            str(item[1].get("property_name") or ""),
        ),
    )


def _image_is_invalid(candidate: dict[str, Any]) -> bool:
    hero = candidate.get("hero_image") or {}
    url = str(hero.get("url") or "").strip()
    source_name = str(hero.get("source_name") or "").strip()
    source_url = str(hero.get("source_url") or "").strip()
    if not _http_url(url):
        return True
    lowered = f"{url} {source_name} {source_url}".casefold()
    return _contains_low_value_image_token(lowered)


def _hero_image_payload_is_low_value(hero_image: Any) -> bool:
    if hero_image is None:
        return False
    if isinstance(hero_image, str):
        lowered = hero_image.casefold()
    else:
        lowered = json.dumps(hero_image, ensure_ascii=False).casefold()
    return _contains_low_value_image_token(lowered)


def _contains_low_value_image_token(lowered_text: str) -> bool:
    bad_substring_tokens = tuple(token for token in BAD_IMAGE_TOKENS if token != "icon")
    if any(token in lowered_text for token in bad_substring_tokens + LOW_VALUE_IMAGE_HOST_TOKENS):
        return True
    return bool(re.search(r"(^|[^a-z0-9])icons?([^a-z0-9]|$)", lowered_text))


def _clear_low_value_active_hero_images(
    repository: SQLAlchemyScanRunRepository,
    countries: set[str],
) -> dict[str, Any]:
    cleared: list[dict[str, Any]] = []
    with repository.session_factory() as session:
        rows = session.scalars(
            select(PropertyDB).where(
                PropertyDB.country.in_(sorted(countries)),
                PropertyDB.hero_image.is_not(None),
            )
        ).all()
        for row in rows:
            if not _hero_image_payload_is_low_value(row.hero_image):
                continue
            cleared.append(
                {
                    "property_id": row.id,
                    "country": row.country,
                    "city": row.city,
                    "property_name": row.canonical_name,
                    "scene_type": row.scene_type,
                }
            )
            row.hero_image = None
            row.updated_at = datetime.now(UTC)
        session.commit()
    return {
        "cleared_count": len(cleared),
        "cleared": cleared[:100],
    }


def _image_is_missing(candidate: dict[str, Any]) -> bool:
    hero = candidate.get("hero_image") or {}
    return not str(hero.get("url") or "").strip()


def _record_image_search_attempt(
    candidate: dict[str, Any],
    attempt_id: str,
    result: dict[str, Any],
) -> None:
    attempts = [
        item
        for item in candidate.get("image_search_attempts", []) or []
        if isinstance(item, dict) and item.get("attempt_id") != attempt_id
    ]
    accepted = bool(result.get("accepted_image"))
    attempts.append(
        {
            "attempt_id": attempt_id,
            "provider": result.get("provider"),
            "query": result.get("query"),
            "status": "accepted" if accepted else "not_found",
            "reason": result.get("reason"),
            "cache_path": result.get("cache_path"),
        }
    )
    candidate["image_search_attempts"] = attempts
    candidate["image_search_attempt_count"] = len(attempts)
    if accepted:
        candidate["image_status"] = "verified"
    elif len(attempts) >= 3:
        candidate["image_status"] = "unavailable_after_three_searches"
    else:
        candidate["image_status"] = "search_pending"


def _check_existing_images(
    overlay: dict[str, Any],
    countries: set[str],
    cache_dir: Path,
    timeout_seconds: float,
    max_workers: int,
) -> list[dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[str, dict[str, Any]]] = []
    for country in countries:
        for candidate in overlay.get("countries", {}).get(country, {}).get("candidates", []) or []:
            if _image_is_invalid(candidate):
                continue
            candidates.append((country, candidate))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [
            executor.submit(
                _check_candidate_image_open,
                country,
                candidate,
                cache_dir,
                timeout_seconds,
            )
            for country, candidate in candidates
        ]
        return [future.result() for future in concurrent.futures.as_completed(futures)]


def _check_candidate_image_open(
    country: str,
    candidate: dict[str, Any],
    cache_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    hero = candidate.get("hero_image") or {}
    url = str(hero.get("url") or "").strip()
    key = _candidate_key(country, candidate)
    cached = _check_image_url_variants_open(url, cache_dir, timeout_seconds)
    return {
        "country": country,
        "property_name": candidate.get("property_name"),
        "scene_type": candidate.get("scene_type"),
        "candidate_key": key,
        "url": url,
        "effective_url": stable_hero_image_url(url),
        **cached,
    }


def _check_image_url_variants_open(
    url: str,
    cache_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    stable_url = stable_hero_image_url(url)
    for effective_url in hero_image_url_variants(url):
        cached = _read_image_open_cache(cache_dir, effective_url)
        if cached is None or (
            cached.get("ok") and not _proxy_hero_image_cache_ok(stable_url)
        ):
            cached = _check_image_url_open(
                effective_url,
                timeout_seconds,
                proxy_cache_url=stable_url,
            )
            _write_image_open_cache(cache_dir, effective_url, cached)
        results.append(cached)
        if cached.get("ok"):
            return cached
    retryable = next((item for item in results if _image_check_is_retryable(item)), None)
    if retryable is not None:
        return retryable
    return (
        results[-1]
        if results
        else _check_image_url_open(
            url,
            timeout_seconds,
            proxy_cache_url=stable_url,
        )
    )


def _check_image_url_open(
    url: str,
    timeout_seconds: float,
    *,
    proxy_cache_url: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": "iSite2/0.1 candidate-image-gate",
    }
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_seconds) as client:
            response = client.get(url, headers=headers)
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        ok = response.status_code in {200, 206} and content_type.startswith("image/")
        if ok:
            _write_proxy_hero_image_cache(
                proxy_cache_url or url,
                response.content,
                content_type,
                str(response.url),
            )
        retryable = response.status_code == 429
        return {
            "ok": ok,
            "status_code": response.status_code,
            "content_type": content_type,
            "final_url": str(response.url),
            "checked_url": url,
            "proxy_cache_url": proxy_cache_url or url,
            "retryable": retryable,
            "reason": None if ok else "url did not return image content",
        }
    except Exception as exc:  # noqa: BLE001 - store exact open-check failure for report
        return {
            "ok": False,
            "status_code": None,
            "content_type": None,
            "final_url": None,
            "checked_url": url,
            "proxy_cache_url": proxy_cache_url or url,
            "retryable": _image_open_exception_is_retryable(exc),
            "reason": str(exc),
        }


def _proxy_hero_image_cache_ok(url: str) -> bool:
    image_path, _, _ = _proxy_hero_image_cache_paths(url)
    return (
        image_path.exists()
        and datetime.now(UTC).timestamp() - image_path.stat().st_mtime
        <= PROXY_HERO_IMAGE_TTL_SECONDS
    )


def _write_proxy_hero_image_cache(
    url: str,
    content: bytes,
    content_type: str,
    final_url: str,
) -> None:
    if not content or len(content) > PROXY_HERO_IMAGE_MAX_BYTES:
        return
    image_path, meta_path, fail_path = _proxy_hero_image_cache_paths(url)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(content)
    meta_path.write_text(
        json.dumps({"content_type": content_type, "final_url": final_url}),
        encoding="utf-8",
    )
    fail_path.unlink(missing_ok=True)


def _proxy_hero_image_cache_paths(url: str) -> tuple[Path, Path, Path]:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    base = _proxy_hero_image_cache_dir() / key[:2] / key[2:4]
    return base / f"{key}.image", base / f"{key}.json", base / f"{key}.fail"


def _proxy_hero_image_cache_dir() -> Path:
    return Path(
        os.getenv(
            "ISITE2_HERO_IMAGE_CACHE_DIR",
            str(Path(".tmp") / "isite2_hero_image_cache"),
        )
    )


def _image_check_is_retryable(item: dict[str, Any]) -> bool:
    if bool(item.get("retryable")) or item.get("status_code") == 429:
        return True
    reason = str(item.get("reason") or "").casefold()
    retryable_reason_tokens = (
        "connection reset",
        "connection aborted",
        "connection refused",
        "temporarily unavailable",
        "timed out",
        "timeout",
        "readerror",
        "remoteprotocolerror",
        "server disconnected",
        "network is unreachable",
    )
    return any(token in reason for token in retryable_reason_tokens)


def _image_open_exception_is_retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return _image_check_is_retryable({"reason": text})


def _read_image_open_cache(cache_dir: Path, url: str) -> dict[str, Any] | None:
    path = _image_open_cache_path(cache_dir, url)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    checked_at = payload.get("checked_at_epoch")
    if not isinstance(checked_at, (int, float)):
        return None
    if datetime.now(UTC).timestamp() - checked_at > IMAGE_OPEN_CACHE_TTL_SECONDS:
        return None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    if result and _image_check_is_retryable(result):
        return None
    return result


def _write_image_open_cache(cache_dir: Path, url: str, result: dict[str, Any]) -> None:
    path = _image_open_cache_path(cache_dir, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "checked_at_epoch": datetime.now(UTC).timestamp(),
                "url": url,
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _image_open_cache_path(cache_dir: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / key[:2] / f"{key}.json"


def _search_image_for_candidate(
    country: str,
    candidate: dict[str, Any],
    image_limit: int,
    cache_dir: Path,
    source_date: str,
    allow_firecrawl_fallback: bool = False,
    skip_source_page_image_extraction: bool = False,
    source_page_cache: _SourcePageImageCache | None = None,
    image_query_profile: str = "default",
) -> dict[str, Any]:
    key = _candidate_key(country, candidate)
    query = _image_query(country, candidate, profile=image_query_profile)
    scrapling_result = (
        {
            "country": country,
            "property_name": candidate.get("property_name"),
            "scene_type": candidate.get("scene_type"),
            "candidate_key": key,
            "provider": "scrapling_page_images",
            "query": query,
            "reason": "source-page extraction skipped for multi-property directory",
        }
        if skip_source_page_image_extraction
        else _search_image_from_candidate_pages(
            country,
            candidate,
            image_limit,
            cache_dir,
            source_date,
            source_page_cache,
        )
    )
    if scrapling_result.get("accepted_image") or not allow_firecrawl_fallback:
        return scrapling_result

    cache_path = cache_dir / "firecrawl" / f"{_safe_filename(key)}.json"
    payload: dict[str, Any] | None = None
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
    if payload is None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "scripts/run_firecrawl_cli.py",
            "--",
            "search",
            query,
            "--sources",
            "images",
            "--limit",
            str(image_limit),
            "--json",
            "-o",
            str(cache_path),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                cwd=Path.cwd(),
                env=firecrawl_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=90,
            )
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
            return {
                "country": country,
                "property_name": candidate.get("property_name"),
                "scene_type": candidate.get("scene_type"),
                "candidate_key": key,
                "provider": "firecrawl_image_search",
                "query": query,
                "scrapling_reason": scrapling_result.get("reason"),
                "reason": f"firecrawl image search failed: {exc}",
            }
    image_candidate = {**candidate, "country": country}
    image = _choose_image(image_candidate, payload, source_date, cache_dir.parent / "open_check")
    return {
        "country": country,
        "property_name": candidate.get("property_name"),
        "scene_type": candidate.get("scene_type"),
        "candidate_key": key,
        "provider": "firecrawl_image_search",
        "query": query,
        "cache_path": str(cache_path),
        "accepted_image": image,
        "scrapling_reason": scrapling_result.get("reason"),
        "reason": None if image else "no relevant real image in search results",
    }


def _search_image_from_candidate_pages(
    country: str,
    candidate: dict[str, Any],
    image_limit: int,
    cache_dir: Path,
    source_date: str,
    source_page_cache: _SourcePageImageCache | None = None,
) -> dict[str, Any]:
    key = _candidate_key(country, candidate)
    query = _image_query(country, candidate)
    cache_path = cache_dir / "scrapling" / f"{_safe_filename(key)}.json"
    payload: dict[str, Any] | None = None
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
    if payload is None:
        provider = (
            source_page_cache.provider
            if source_page_cache is not None
            else ScraplingPublicEvidenceProvider(
                artifact_dir=cache_dir.parent / "scrapling_pages",
            )
        )
        source_urls = _candidate_source_page_urls(candidate)
        images: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for source_url in source_urls[: max(3, image_limit)]:
            try:
                images.extend(
                    source_page_cache.extract(source_url)
                    if source_page_cache is not None
                    else provider.extract_image_candidates(source_url)
                )
            except Exception as exc:  # noqa: BLE001 - retained as audit for image sourcing
                failures.append({"url": source_url, "error": str(exc)})
        payload = {
            "provider": "scrapling_page_images",
            "query": query,
            "source_urls": source_urls,
            "data": {"images": images[: max(25, image_limit * 5)]},
            "failures": failures,
            "provider_stats": _scrapling_image_stats(provider),
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    image_candidate = {**candidate, "country": country}
    image = _choose_image(image_candidate, payload, source_date, cache_dir.parent / "open_check")
    reason = None if image else "no relevant real image in candidate source pages"
    if not payload.get("source_urls"):
        reason = "no candidate source page URLs available for Scrapling image extraction"
    elif payload.get("failures") and not payload.get("data", {}).get("images"):
        reason = "Scrapling could not extract images from candidate source pages"
    return {
        "country": country,
        "property_name": candidate.get("property_name"),
        "scene_type": candidate.get("scene_type"),
        "candidate_key": key,
        "provider": "scrapling_page_images",
        "query": query,
        "cache_path": str(cache_path),
        "source_urls": payload.get("source_urls", []),
        "provider_stats": payload.get("provider_stats", {}),
        "accepted_image": image,
        "reason": reason,
    }


def _candidate_source_page_urls(candidate: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    hero = candidate.get("hero_image") if isinstance(candidate.get("hero_image"), dict) else {}
    if _http_url(str(hero.get("source_url") or "")):
        urls.append(str(hero.get("source_url")))
    for key in ("source_url", "url", "official_url", "website"):
        value = str(candidate.get(key) or "").strip()
        if _http_url(value):
            urls.append(value)
    for evidence in candidate.get("evidence", []) or []:
        if not isinstance(evidence, dict):
            continue
        value = str(evidence.get("source_url") or "").strip()
        if _http_url(value):
            urls.append(value)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = url.rstrip("/").casefold()
        if key in seen or _looks_like_direct_image_url(url):
            continue
        seen.add(key)
        deduped.append(url)
    return deduped


def _looks_like_direct_image_url(url: str) -> bool:
    path = urlparse(str(url or "")).path.casefold()
    return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif"))


def _scrapling_image_stats(provider: ScraplingPublicEvidenceProvider) -> dict[str, Any]:
    stats = provider.stats
    return {
        "fetch_requests": stats.fetch_requests,
        "static_fetch_count": stats.static_fetch_count,
        "dynamic_fetch_count": stats.dynamic_fetch_count,
        "stealth_fetch_count": stats.stealth_fetch_count,
        "cache_hits": stats.cache_hits,
        "robots_disallowed_count": stats.robots_disallowed_count,
        "restricted_page_count": stats.restricted_page_count,
        "artifact_write_count": stats.artifact_write_count,
        "failures": list(stats.failures),
        "warnings": list(stats.warnings),
    }


def _sum_scrapling_image_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    warnings: list[str] = []
    failures: list[str] = []
    for item in results:
        stats = item.get("provider_stats")
        if not isinstance(stats, dict):
            continue
        for key in (
            "fetch_requests",
            "static_fetch_count",
            "dynamic_fetch_count",
            "stealth_fetch_count",
            "cache_hits",
            "robots_disallowed_count",
            "restricted_page_count",
            "artifact_write_count",
        ):
            totals[key] += int(stats.get(key) or 0)
        warnings.extend(str(value) for value in stats.get("warnings", []) or [])
        failures.extend(str(value) for value in stats.get("failures", []) or [])
    result = dict(totals)
    result["warnings"] = warnings[:50]
    result["failures"] = failures[:50]
    return result


def _image_query(
    country: str,
    candidate: dict[str, Any],
    *,
    profile: str = "default",
) -> str:
    scene = str(candidate.get("scene_type") or "")
    default_scene_hint = {
        "luxury_hotel_mice": "hotel exterior",
        "mall_mixed_use": "shopping mall exterior",
        "stadium": "stadium",
        "airport_terminal": "airport terminal",
        "convention_center": "convention centre exterior",
        "office_government": "office tower exterior",
        "transport_hub": "transport terminal exterior",
        "hospital": "hospital building exterior",
        "university": "university campus exterior",
    }.get(scene, "building exterior")
    localized_scene_hints = {
        "Mexico": {
            "luxury_hotel_mice": "hotel fachada sitio oficial",
            "mall_mixed_use": "centro comercial fachada sitio oficial",
            "stadium": "estadio fachada sitio oficial",
            "airport_terminal": "terminal aeropuerto sitio oficial",
            "convention_center": "centro de convenciones fachada sitio oficial",
            "office_government": "torre oficinas fachada sitio oficial",
            "transport_hub": "estacion terminal fachada sitio oficial",
            "hospital": "hospital edificio sitio oficial",
            "university": "universidad campus sitio oficial",
        }
    }
    scene_hint = default_scene_hint
    if profile == "local_official":
        scene_hint = localized_scene_hints.get(country, {}).get(
            scene,
            f"{default_scene_hint} official site",
        )
    return " ".join(
        part
        for part in [
            str(candidate.get("property_name") or ""),
            str(candidate.get("city") or ""),
            country,
            scene_hint,
        ]
        if part
    )


def _choose_image(
    candidate: dict[str, Any],
    payload: dict[str, Any],
    source_date: str,
    open_cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    results = payload.get("data", {}).get("images", []) or []
    scored: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(results):
        original_image_url = str(item.get("imageUrl") or "").strip()
        image_url = stable_hero_image_url(original_image_url)
        page_url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "")
        if not _http_url(image_url):
            continue
        if _image_asset_is_low_value(image_url):
            continue
        image_width = int(item.get("imageWidth") or 0)
        image_height = int(item.get("imageHeight") or 0)
        if image_width and image_width < 480:
            continue
        if image_height and image_height < 270:
            continue
        lowered = f"{original_image_url} {image_url} {page_url} {title}".casefold()
        if _contains_low_value_image_token(lowered):
            continue
        if _result_context_is_low_value(candidate, title, page_url, image_url):
            continue
        match_audit = _image_match_audit(candidate, title, page_url, image_url)
        if not match_audit["accepted"]:
            continue
        if (
            payload.get("provider") == "scrapling_page_images"
            and not _image_url_matches_property_tokens(image_url, match_audit["property_tokens"])
        ):
            continue
        score = 100 - index
        host_text = f"{urlparse(image_url).netloc} {urlparse(page_url).netloc}".casefold()
        if any(token in host_text for token in PREFERRED_HOST_TOKENS):
            score += 30
        elif any(token in host_text for token in FALLBACK_IMAGE_HOST_TOKENS):
            score += 8
        if item.get("imageWidth") and int(item.get("imageWidth") or 0) >= 500:
            score += 5
        match_audit.update(
            {
                "source_title": title,
                "original_image_url": original_image_url,
                "result_rank": index + 1,
                "image_width": item.get("imageWidth"),
                "score": score,
            }
        )
        scored.append(
            (
                score,
                {
                    "url": image_url,
                    "alt_text": f"{candidate.get('property_name')} public property image",
                    "source_name": _source_name(item),
                    "source_url": page_url or image_url,
                    "source_date": source_date,
                    "license": "Public web image metadata",
                    "audit": match_audit,
                },
            )
        )
    if not scored:
        return None
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    if open_cache_dir is None:
        return ranked[0][1]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(ranked))) as executor:
        checks = {
            image["url"]: executor.submit(_accepted_image_opens, image["url"], open_cache_dir)
            for _, image in ranked
        }
        for _, image in ranked:
            if checks[image["url"]].result():
                return image
    return None


def _image_asset_is_low_value(image_url: str) -> bool:
    lowered = unquote(urlparse(image_url).path).casefold()
    if lowered.endswith(".svg"):
        return True
    return any(
        token in lowered
        for token in (
            "/flags/",
            "favicon",
            "site-search",
            "cobrand",
            "sprite",
            "poster",
            "logo",
            "control-type",
            "/images/ports/map/",
        )
    ) or bool(re.search(r"(?:^|[-_/])\d{1,3}x\d{1,3}(?:[-_.?/]|$)", lowered))


def _result_context_is_low_value(
    candidate: dict[str, Any],
    title: str,
    page_url: str,
    image_url: str,
) -> bool:
    lowered = f"{title} {page_url} {image_url}".casefold()
    if any(
        token in lowered
        for token in (
            "graduation project",
            "gradution project",
            "homepage screenshot",
            "website screenshot",
            "design by",
            "exciting excursions",
            "hotels near",
            "hotel near",
            "best hotels",
            "nearby hotels",
        )
    ):
        return True
    if str(candidate.get("scene_type") or "") != "luxury_hotel_mice" and "/hotels/" in lowered:
        return True
    return False


def _image_url_matches_property_tokens(image_url: str, property_tokens: list[str]) -> bool:
    haystack = normalize_property_name(unquote(urlparse(image_url).path))
    return any(token in haystack for token in property_tokens)


def _accepted_image_opens(url: str, cache_dir: Path) -> bool:
    timeout_seconds = float(os.getenv("ISITE2_IMAGE_OPEN_TIMEOUT_SECONDS", "6.0"))
    cached = _check_image_url_variants_open(url, cache_dir, timeout_seconds)
    return bool(cached.get("ok"))


def _image_matches_candidate(
    candidate: dict[str, Any],
    title: str,
    page_url: str,
    image_url: str,
) -> bool:
    return bool(_image_match_audit(candidate, title, page_url, image_url)["accepted"])


def _image_match_audit(
    candidate: dict[str, Any],
    title: str,
    page_url: str,
    image_url: str = "",
) -> dict[str, Any]:
    # Identity must be proven by the result title/source page context. CDN file
    # names can coincidentally contain weak tokens and caused cross-country
    # image pollution for generic names.
    haystack = normalize_property_name(f"{title} {page_url} {unquote(image_url)}")
    city_tokens = set(normalize_property_name(str(candidate.get("city") or "")).split())
    country_tokens = set(normalize_property_name(str(candidate.get("country") or "")).split())
    tokens = [
        token
        for token in normalize_property_name(str(candidate.get("property_name") or "")).split()
        if len(token) >= 3
        and token not in city_tokens
        and token not in {"all", "the", "and", "for", "of"}
        and token not in GENERIC_PROPERTY_IDENTITY_TOKENS
    ]
    required = 1 if len(tokens) <= 2 else max(2, (len(tokens) * 3 + 3) // 4)
    haystack_tokens = set(haystack.split())
    matched_property_tokens = [token for token in tokens if token in haystack_tokens]
    city_geo_tokens = {
        token
        for token in city_tokens
        if len(token) >= 3
        and token
        not in {
            "the",
            "and",
            "city",
            "province",
            "state",
            "new",
            "administrative",
            "capital",
        }
    }
    country_geo_tokens = {
        token
        for token in country_tokens
        if len(token) >= 3 and token not in {"the", "and", "city", "province", "state"}
    }
    if city_geo_tokens:
        geo_tokens = sorted(city_geo_tokens)
    else:
        geo_tokens = sorted(country_geo_tokens)
    matched_geo_tokens = [token for token in geo_tokens if token in haystack_tokens]
    strong_identity_required = max(2, (len(tokens) * 3 + 3) // 4)
    strong_identity_match = (
        len(tokens) >= 2 and len(matched_property_tokens) >= strong_identity_required
    )
    scene_tokens = SCENE_IMAGE_IDENTITY_TOKENS.get(
        str(candidate.get("scene_type") or ""), set()
    )
    matched_scene_tokens = sorted(token for token in scene_tokens if token in haystack_tokens)
    if tokens:
        accepted = len(matched_property_tokens) >= required and (
            len(matched_geo_tokens) >= 1 or strong_identity_match
        )
        if len(tokens) == 1:
            accepted = accepted and bool(matched_scene_tokens)
    else:
        accepted = bool(matched_geo_tokens and matched_scene_tokens)
    if accepted:
        match_reason = (
            "source title/url match property identity tokens and city/country context"
        )
    elif tokens and len(matched_property_tokens) < required:
        match_reason = "source title/url missing enough property identity tokens"
    elif not tokens and not matched_geo_tokens:
        match_reason = "generic property name missing city/country identity"
    elif not tokens and not matched_scene_tokens:
        match_reason = "generic property name missing scene identity"
    else:
        match_reason = "source title/url missing city/country context"
    return {
        "accepted": accepted,
        "match_reason": match_reason,
        "property_tokens": tokens,
        "matched_property_tokens": matched_property_tokens,
        "geo_tokens": geo_tokens,
        "matched_geo_tokens": matched_geo_tokens,
        "scene_tokens": sorted(scene_tokens),
        "matched_scene_tokens": matched_scene_tokens,
    }


def _source_name(item: dict[str, Any]) -> str:
    page_url = str(item.get("url") or item.get("imageUrl") or "")
    host = urlparse(page_url).netloc
    return host or "public web image metadata"


def _refresh_identity_keys(overlay: dict[str, Any], countries: set[str]) -> None:
    for country in countries:
        candidates = overlay.get("countries", {}).get(country, {}).get("candidates", []) or []
        for candidate in candidates:
            candidate["property_identity_key"] = property_identity_key(
                country=country,
                city=str(candidate.get("city") or ""),
                property_name=str(candidate.get("property_name") or ""),
                scene_type=str(candidate.get("scene_type") or ""),
            )


def _remaining_invalid_counts(overlay: dict[str, Any], countries: set[str]) -> dict[str, int]:
    return {
        country: sum(
            1
            for candidate in _country_candidates(overlay, country)
            if not _image_is_missing(candidate) and _image_is_invalid(candidate)
        )
        for country in countries
    }


def _counts(overlay: dict[str, Any], countries: set[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for country in countries:
        scene_counts = Counter(
            candidate.get("scene_type")
            for candidate in _country_candidates(overlay, country)
        )
        result[country] = dict(scene_counts)
    return result


def _country_candidates(overlay: dict[str, Any], country: str) -> list[dict[str, Any]]:
    return overlay.get("countries", {}).get(country, {}).get("candidates", []) or []


def _candidate_key(country: str, candidate: dict[str, Any]) -> str:
    return "|".join(
        [
            country,
            str(candidate.get("city") or ""),
            str(candidate.get("scene_type") or ""),
            str(candidate.get("property_name") or ""),
        ]
    )


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return safe.strip("_")[:180] or "candidate"


def _http_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _evidence_key(evidence: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(evidence.get("source_url") or "").strip().casefold(),
        str(evidence.get("field_group") or "").strip().casefold(),
        str(evidence.get("indicator_name") or "").strip().casefold(),
        str(evidence.get("field_value") or "").strip().casefold(),
    )


def _sync_summary(result: Any | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "created": result.created,
        "run_id": str(result.run_id) if result.run_id else None,
        "candidate_count": result.candidate_count,
        "registry_candidate_count": result.registry_candidate_count,
        "blocked_candidate_count": result.blocked_candidate_count,
        "skipped_reason": result.skipped_reason,
        "derived_refresh": result.derived_refresh,
    }


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Candidate Image and Duplicate Gate",
        "",
        f"- Countries: {', '.join(summary['countries'])}",
        f"- Duplicate variants merged: {summary['duplicate_merge']['merged_count']}",
        f"- Existing images checked: {summary['existing_image_open_check_count']}",
        f"- Temporary rate-limited images: {summary['temporary_rate_limited_image_count']}",
        f"- Broken existing images: {summary['broken_existing_image_count']}",
        (
            "- Missing-image candidates skipped by default: "
            f"{summary['missing_image_candidate_count']}"
        ),
        f"- Fill missing images requested: {summary['fill_missing_images']}",
        f"- Initial invalid/placeholder images: {summary['initial_invalid_image_count']}",
        f"- Images replaced: {summary['image_replaced_count']}",
        f"- Kept without image after failed replacement: {summary['kept_without_image_count']}",
        f"- Remaining invalid images: {summary['remaining_invalid_images']}",
        f"- Overlay sync: {summary['overlay_sync']}",
        f"- Active low-value hero images cleared: {summary['active_low_value_hero_image_cleanup']}",
        "",
        "## Removed No Image",
        "",
    ]
    for item in summary["removed_no_image"][:80]:
        reason = item["reason"]
        lines.append(
            f"- {item['country']} | {item['property_name']} | "
            f"{item['scene_type']}: {reason}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
