from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from isite2.connectors.firecrawl import firecrawl_subprocess_env
from isite2.growth.evidence_intake import DEFAULT_OVERLAY_PATH, write_registry_overlay
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import normalize_property_name, property_identity_key
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_URL = "sqlite+pysqlite:///outputs/isite2_dev.db"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
DEFAULT_CACHE_DIR = Path(".firecrawl") / "candidate_image_gate" / "image_search"
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
    "doubleclick",
    "googleads",
)
PREFERRED_HOST_TOKENS = (
    "wikimedia.org",
    "wikipedia.org",
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

DUPLICATE_CANONICAL_NAMES = {
    ("Cambodia", "airport_terminal", "Techo International Airport (KTI)"): "Techo International Airport",
    ("Cambodia", "mall_mixed_use", "Aeon Mall Mean Chey (Aeon 3)"): "AEON Mall Mean Chey",
    ("Cambodia", "office_government", "Exchange Square"): "Exchange Square Phnom Penh",
    ("Maldives", "airport_terminal", "Velana International Airport"): "Velana International Airport",
    ("Maldives", "airport_terminal", "Gan International Airport"): "Gan International Airport",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge known duplicate variants, replace placeholder hero images, and sync active candidates."
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
    invalid = _invalid_image_candidates(overlay, selected_countries)

    image_search_results: list[dict[str, Any]] = []
    if invalid and not args.skip_image_search:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = [
                executor.submit(
                    _search_image_for_candidate,
                    country,
                    candidate,
                    args.image_limit,
                    args.cache_dir,
                    args.source_date,
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
    removed: list[dict[str, Any]] = []
    replaced: list[dict[str, Any]] = []
    for country in selected_countries:
        country_registry = overlay.get("countries", {}).get(country)
        if not country_registry:
            continue
        retained: list[dict[str, Any]] = []
        for candidate in country_registry.get("candidates", []) or []:
            key = _candidate_key(country, candidate)
            if not _image_is_invalid(candidate):
                retained.append(candidate)
                continue
            replacement = replacement_by_key.get(key)
            if replacement:
                candidate["hero_image"] = replacement["accepted_image"]
                replaced.append(
                    {
                        "country": country,
                        "property_name": candidate.get("property_name"),
                        "scene_type": candidate.get("scene_type"),
                        "image_source": replacement["accepted_image"].get("source_name"),
                    }
                )
                retained.append(candidate)
            else:
                removed.append(
                    {
                        "country": country,
                        "city": candidate.get("city"),
                        "property_name": candidate.get("property_name"),
                        "scene_type": candidate.get("scene_type"),
                        "reason": "no real property image found",
                    }
                )
        country_registry["candidates"] = retained

    _refresh_identity_keys(overlay, selected_countries)

    sync = None
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
    else:
        backup_path = None

    summary = {
        "mode": "candidate_image_and_duplicate_gate",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "countries": sorted(selected_countries),
        "all_countries": args.all_countries,
        "duplicate_alias_map": str(args.duplicate_alias_map) if args.duplicate_alias_map else None,
        "duplicate_merge": merge_result,
        "initial_invalid_image_count": len(invalid),
        "image_search_count": len(image_search_results),
        "image_replaced_count": len(replaced),
        "removed_no_image_count": len(removed),
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
) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for country in countries:
        country_registry = overlay.get("countries", {}).get(country)
        if not country_registry:
            continue
        for candidate in country_registry.get("candidates", []) or []:
            if _image_is_invalid(candidate):
                result.append((country, candidate))
    return result


def _image_is_invalid(candidate: dict[str, Any]) -> bool:
    hero = candidate.get("hero_image") or {}
    url = str(hero.get("url") or "").strip()
    source_name = str(hero.get("source_name") or "").strip()
    if not _http_url(url):
        return True
    lowered = f"{url} {source_name}".casefold()
    return any(token in lowered for token in BAD_IMAGE_TOKENS)


def _search_image_for_candidate(
    country: str,
    candidate: dict[str, Any],
    image_limit: int,
    cache_dir: Path,
    source_date: str,
) -> dict[str, Any]:
    key = _candidate_key(country, candidate)
    cache_path = cache_dir / f"{_safe_filename(key)}.json"
    query = _image_query(country, candidate)
    payload: dict[str, Any] | None = None
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
    if payload is None:
        command = [
            "firecrawl",
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
                "query": query,
                "reason": f"firecrawl image search failed: {exc}",
            }
    image = _choose_image(candidate, payload, source_date)
    return {
        "country": country,
        "property_name": candidate.get("property_name"),
        "scene_type": candidate.get("scene_type"),
        "candidate_key": key,
        "query": query,
        "cache_path": str(cache_path),
        "accepted_image": image,
        "reason": None if image else "no relevant real image in search results",
    }


def _image_query(country: str, candidate: dict[str, Any]) -> str:
    scene = str(candidate.get("scene_type") or "")
    scene_hint = {
        "luxury_hotel_mice": "hotel exterior",
        "mall_mixed_use": "shopping mall exterior",
        "stadium": "stadium",
        "airport_terminal": "airport terminal",
        "convention_center": "convention centre exterior",
        "office_government": "office tower exterior",
        "transport_hub": "transport terminal exterior",
    }.get(scene, "building exterior")
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
) -> dict[str, Any] | None:
    results = payload.get("data", {}).get("images", []) or []
    scored: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(results):
        image_url = str(item.get("imageUrl") or "").strip()
        page_url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "")
        if not _http_url(image_url):
            continue
        lowered = f"{image_url} {page_url} {title}".casefold()
        if any(token in lowered for token in BAD_IMAGE_TOKENS + LOW_VALUE_IMAGE_HOST_TOKENS):
            continue
        if not _image_matches_candidate(candidate, title, page_url, image_url):
            continue
        score = 100 - index
        host_text = f"{urlparse(image_url).netloc} {urlparse(page_url).netloc}".casefold()
        if any(token in host_text for token in PREFERRED_HOST_TOKENS):
            score += 25
        if item.get("imageWidth") and int(item.get("imageWidth") or 0) >= 500:
            score += 5
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
                },
            )
        )
    if not scored:
        return None
    return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]


def _image_matches_candidate(
    candidate: dict[str, Any],
    title: str,
    page_url: str,
    image_url: str,
) -> bool:
    haystack = normalize_property_name(
        f"{title} {page_url} {image_url} {candidate.get('city') or ''}"
    )
    tokens = [
        token
        for token in normalize_property_name(str(candidate.get("property_name") or "")).split()
        if len(token) >= 3 and token not in {"hotel", "resort", "spa", "the", "and", "mall"}
    ]
    if not tokens:
        return True
    required = 1 if len(tokens) <= 2 else 2
    matches = sum(1 for token in tokens if token in haystack)
    return matches >= required


def _source_name(item: dict[str, Any]) -> str:
    page_url = str(item.get("url") or item.get("imageUrl") or "")
    host = urlparse(page_url).netloc
    return host or "Firecrawl image search"


def _refresh_identity_keys(overlay: dict[str, Any], countries: set[str]) -> None:
    for country in countries:
        for candidate in overlay.get("countries", {}).get(country, {}).get("candidates", []) or []:
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
            for candidate in overlay.get("countries", {}).get(country, {}).get("candidates", []) or []
            if _image_is_invalid(candidate)
        )
        for country in countries
    }


def _counts(overlay: dict[str, Any], countries: set[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for country in countries:
        scene_counts = Counter(
            candidate.get("scene_type")
            for candidate in overlay.get("countries", {}).get(country, {}).get("candidates", []) or []
        )
        result[country] = dict(scene_counts)
    return result


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
        f"- Initial invalid/placeholder images: {summary['initial_invalid_image_count']}",
        f"- Images replaced: {summary['image_replaced_count']}",
        f"- Removed for no real image: {summary['removed_no_image_count']}",
        f"- Remaining invalid images: {summary['remaining_invalid_images']}",
        f"- Overlay sync: {summary['overlay_sync']}",
        "",
        "## Removed No Image",
        "",
    ]
    for item in summary["removed_no_image"][:80]:
        lines.append(
            f"- {item['country']} | {item['property_name']} | {item['scene_type']}: {item['reason']}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
