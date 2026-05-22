from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import (
    CandidateDraft,
    load_effective_source_registry,
    validate_candidate_draft,
)
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import (
    NEW_OPPORTUNITY,
    known_opportunity_index_from_registry,
    normalize_property_name,
    property_identity_key,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_PATH = Path("outputs/isite2_dev.db")
DB_URL = f"sqlite+pysqlite:///{DB_PATH}"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
INPUT_DIR = Path(".firecrawl") / "apac_50"
SOURCE_TYPE = "cvent_candidate_expansion"
SOURCE_DATE = "2026-05-19"
USER_AGENT = "isite2-codex/0.1 public evidence geocoding"

COUNTRIES = ["Sri Lanka", "Cambodia", "Maldives"]
COUNTRY_META = {
    "Sri Lanka": {"slug": "sri_lanka", "cc": "LK", "osm_cc": "lk"},
    "Cambodia": {"slug": "cambodia", "cc": "KH", "osm_cc": "kh"},
    "Maldives": {"slug": "maldives", "cc": "MV", "osm_cc": "mv"},
    "Peru": {"slug": "peru", "cc": "PE", "osm_cc": "pe"},
    "Chile": {"slug": "chile", "cc": "CL", "osm_cc": "cl"},
    "Ecuador": {"slug": "ecuador", "cc": "EC", "osm_cc": "ec"},
    "Colombia": {"slug": "colombia", "cc": "CO", "osm_cc": "co"},
    "Philippines": {"slug": "philippines", "cc": "PH", "osm_cc": "ph"},
}

COUNTRY_REGION = {
    "Sri Lanka": "Asia Pacific",
    "Cambodia": "Asia Pacific",
    "Maldives": "Asia Pacific",
    "Peru": "Latin America",
    "Chile": "Latin America",
    "Ecuador": "Latin America",
    "Colombia": "Latin America",
    "Philippines": "Asia Pacific",
}

PLACEHOLDER_IMAGE_TOKENS = (
    "placeholder.c229933f",
    "cvent-logo",
    "googleads.g.doubleclick",
)


@dataclass(frozen=True)
class CventVenue:
    country: str
    city: str
    property_name: str
    venue_type: str
    source_url: str
    rooms: int
    meeting_space_sqft: int
    hero_url: str | None
    hero_source_name: str
    source_file: str
    metric_text: str


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert saved Cvent APAC venue pages into iSite2 candidate drafts."
    )
    parser.add_argument("--countries", nargs="+", default=COUNTRIES)
    parser.add_argument("--target-ready-per-country", type=int, default=50)
    parser.add_argument("--overfill-margin", type=int, default=8)
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument(
        "--cached-geocodes-only",
        action="store_true",
        help="Use only cached Nominatim results; skip live geocode misses to keep sweep batches bounded.",
    )
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    args.input_dir.mkdir(parents=True, exist_ok=True)

    registry = load_effective_source_registry()
    store = EvidenceCurationStore(database_url=DB_URL)
    known_index = known_opportunity_index_from_registry(
        registry,
        session_factory=store.session_factory,
    )
    hero_index = _search_hero_index(args.input_dir)
    current_ready = _current_ready_counts(args.countries)
    geocoder = NominatimGeocoder(
        cache_path=OUTPUT_DIR / "cvent_apac_geocode_cache.json",
        registry=registry,
        live_lookup=not args.cached_geocodes_only,
    )

    parsed: list[CventVenue] = []
    for country in args.countries:
        parsed.extend(_parse_country(country, args.input_dir, hero_index))

    selected: list[CandidateDraft] = []
    invalid: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    planned_by_country: Counter[str] = Counter()
    seen_identity_keys: set[str] = set()

    for venue in _rank_venues(parsed):
        needed = max(args.target_ready_per_country - current_ready.get(venue.country, 0), 0)
        cap = needed + args.overfill_margin
        if cap <= 0:
            skipped["country_already_at_target"] += 1
            continue
        if planned_by_country[venue.country] >= cap:
            skipped["country_cap_reached"] += 1
            continue
        if "/cvb/" in venue.source_url or not _is_property_venue(venue):
            skipped["non_property_venue"] += 1
            continue
        if venue.rooms <= 0 and venue.meeting_space_sqft < 500:
            skipped["metric_missing"] += 1
            continue

        scene_type = _scene_type(venue)
        key = property_identity_key(
            country=venue.country,
            city=venue.city,
            property_name=venue.property_name,
            scene_type=scene_type,
        )
        if key in seen_identity_keys:
            skipped["duplicate_in_batch"] += 1
            continue
        if known_index.knows_source_url(venue.source_url):
            skipped["known_source_url"] += 1
            continue

        geocode = geocoder.geocode(venue)
        if geocode is None:
            skipped["geocode_failed_or_out_of_bbox"] += 1
            continue

        draft = _draft_from_venue(venue, scene_type, geocode, registry)
        validation = validate_candidate_draft(draft)
        if not validation.accepted:
            invalid.append(
                {
                    "country": venue.country,
                    "property_name": venue.property_name,
                    "source_url": venue.source_url,
                    "issues": validation.issues,
                }
            )
            skipped["validation_failed"] += 1
            continue

        identity_match = known_index.match(
            country=draft.country,
            city=draft.city,
            property_name=draft.property_name,
            scene_type=draft.scene_type,
            latitude=draft.latitude,
            longitude=draft.longitude,
            source_url=draft.source_url,
        )
        if identity_match.status != NEW_OPPORTUNITY:
            skipped[f"identity_{identity_match.status}"] += 1
            continue

        selected.append(draft)
        seen_identity_keys.add(key)
        planned_by_country[venue.country] += 1

    raw_ids: list[str] = []
    written_or_changed = 0
    if selected and not args.dry_run:
        for draft in selected:
            result = store.upsert_candidate_evidence(draft, source_type=SOURCE_TYPE)
            raw_ids.append(result.raw_evidence_id)
            if result.is_new_evidence or result.is_changed_evidence:
                written_or_changed += 1

    curation = None
    sync = None
    if selected and not args.dry_run:
        curation = run_pending_evidence_curation(store=store, output_dir=OUTPUT_DIR)
        if not args.skip_sync:
            repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
            sync = sync_overlay_to_active_repository(repository)

    final_ready = _current_ready_counts(args.countries)
    summary = {
        "mode": SOURCE_TYPE,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "countries": args.countries,
        "target_ready_per_country": args.target_ready_per_country,
        "current_ready_before": current_ready,
        "current_ready_after": final_ready,
        "parsed_count": len(parsed),
        "parsed_by_country": dict(Counter(item.country for item in parsed)),
        "selected_count": len(selected),
        "selected_by_country": dict(planned_by_country),
        "selected_by_hero_source": dict(
            Counter((draft.hero_image or {}).get("source_name", "") for draft in selected)
        ),
        "invalid_count": len(invalid),
        "invalid": invalid[:50],
        "skipped": dict(skipped),
        "raw_evidence_written_or_changed": written_or_changed,
        "raw_evidence_ids": raw_ids,
        "curation": _curation_summary(curation),
        "overlay_sync": _sync_summary(sync),
        "geocode_cache_path": str(geocoder.cache_path),
        "geocode_stats": dict(geocoder.stats),
        "firecrawl_credits_used_by_script": 0,
        "selected_candidates": [
            {
                "country": draft.country,
                "city": draft.city,
                "property_name": draft.property_name,
                "scene_type": draft.scene_type,
                "field_group": draft.field_group,
                "field_value": draft.field_value,
                "source_url": draft.source_url,
                "hero_source": (draft.hero_image or {}).get("source_name"),
            }
            for draft in selected
        ],
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = OUTPUT_DIR / f"cvent_apac_candidate_expansion_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


class NominatimGeocoder:
    def __init__(
        self,
        *,
        cache_path: Path,
        registry: dict[str, Any],
        live_lookup: bool = True,
    ) -> None:
        self.cache_path = cache_path
        self.registry = registry
        self.live_lookup = live_lookup
        self.stats: Counter[str] = Counter()
        if cache_path.exists():
            self.cache: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            self.cache = {}

    def geocode(self, venue: CventVenue) -> dict[str, float] | None:
        meta = COUNTRY_META[venue.country]
        queries = [
            f"{venue.property_name}, {venue.city}, {venue.country}",
            f"{venue.property_name}, {venue.country}",
        ]
        for query in queries:
            query = re.sub(r"\s+", " ", query).strip(", ")
            if not query:
                continue
            cache_key = f"{meta['osm_cc']}|{query.casefold()}"
            cached = self.cache.get(cache_key)
            if cached is None:
                if not self.live_lookup:
                    self.stats["cache_miss_skipped"] += 1
                    continue
                cached = self._fetch(query, meta["osm_cc"])
                self.cache[cache_key] = cached
                self._write_cache()
                time.sleep(1.1)
            else:
                self.stats["cache_hit"] += 1
            if not cached:
                continue
            lat = float(cached["lat"])
            lon = float(cached["lon"])
            if self._within_country(venue.country, lat, lon):
                return {"latitude": lat, "longitude": lon}
            self.stats["out_of_bbox"] += 1
        return None

    def _fetch(self, query: str, country_code: str) -> dict[str, str] | None:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": country_code,
            }
        )
        request = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            self.stats["request_failed"] += 1
            return None
        if not payload:
            self.stats["no_result"] += 1
            return None
        self.stats["request_success"] += 1
        return {"lat": str(payload[0]["lat"]), "lon": str(payload[0]["lon"])}

    def _within_country(self, country: str, latitude: float, longitude: float) -> bool:
        bbox = self.registry.get("countries", {}).get(country, {}).get("bbox", {})
        return bool(
            bbox
            and float(bbox["min_latitude"]) <= latitude <= float(bbox["max_latitude"])
            and float(bbox["min_longitude"]) <= longitude <= float(bbox["max_longitude"])
        )

    def _write_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _parse_country(country: str, input_dir: Path, hero_index: dict[str, str]) -> list[CventVenue]:
    meta = COUNTRY_META[country]
    venues: list[CventVenue] = []
    seen_urls: set[str] = set()
    for path in sorted(input_dir.glob(f"cvent_{meta['slug']}*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        markdown = data.get("markdown") or data.get("data", {}).get("markdown") or ""
        for entry in re.split(r"\n- \[", markdown)[1:]:
            venue = _parse_entry(
                country=country,
                country_code=meta["cc"],
                entry="- [" + entry,
                source_file=str(path),
                hero_index=hero_index,
            )
            if venue is None:
                continue
            if venue.source_url in seen_urls:
                continue
            seen_urls.add(venue.source_url)
            venues.append(venue)
    return venues


def _parse_entry(
    *,
    country: str,
    country_code: str,
    entry: str,
    source_file: str,
    hero_index: dict[str, str],
) -> CventVenue | None:
    urls = re.findall(
        r"\((https://www\.cvent\.com/venues/en-US/[^)]+/venue-[^)]+)\)",
        entry,
    )
    if not urls:
        return None
    source_url = urls[-1].split("?")[0]
    bolds = [item.strip() for item in re.findall(r"\*\*([^*]+)\*\*", entry) if item.strip()]
    if not bolds:
        return None
    property_name = bolds[-1]

    raw = html.unescape(entry.replace("\\", " "))
    raw_without_images = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", raw)
    raw_without_images = re.sub(r"\s+", " ", raw_without_images)
    venue_match = re.search(
        r"\*\*([^*]+)\*\*\s+([^,]{2,80}),\s*"
        + re.escape(country_code)
        + r"\s*•\s*([^•\[]+)",
        raw,
    )
    if venue_match is None:
        return None
    city = venue_match.group(2).strip()
    venue_type = venue_match.group(3).strip()

    metric_text = _metric_text(raw_without_images)
    rooms, meeting_space_sqft = _metrics(metric_text)
    hero_url, hero_source_name = _hero_image(entry, property_name, hero_index)

    return CventVenue(
        country=country,
        city=city,
        property_name=property_name,
        venue_type=venue_type,
        source_url=source_url,
        rooms=rooms,
        meeting_space_sqft=meeting_space_sqft,
        hero_url=hero_url,
        hero_source_name=hero_source_name,
        source_file=source_file,
        metric_text=metric_text,
    )


def _metric_text(entry_text: str) -> str:
    position = entry_text.casefold().find("select venue")
    if position < 0:
        return ""
    text = entry_text[position + len("select venue") :]
    for marker in ("show more", "select venue]"):
        marker_position = text.casefold().find(marker)
        if marker_position >= 0:
            text = text[:marker_position]
    return re.sub(r"\s+", " ", text).strip()


def _metrics(metric_text: str) -> tuple[int, int]:
    space_values = [
        int(value.replace(",", ""))
        for value in re.findall(r"(\d[\d,]*)\s*sq\.\s*ft", metric_text, re.I)
    ]
    metric_without_space = re.sub(
        r"\d[\d,]*\s*sq\.\s*ft\.?",
        " ",
        metric_text,
        flags=re.I,
    )
    numbers = [
        int(value.replace(",", ""))
        for value in re.findall(r"(?<![\w.])(\d{1,4}(?:,\d{3})?)(?![\w.])", metric_without_space)
    ]
    numbers = [
        number
        for number in numbers
        if number not in {1, 2, 3, 4, 5} and not 1900 <= number <= 2035
    ]
    plausible_rooms = [number for number in numbers if 20 <= number <= 1500]
    rooms = 0
    if len(plausible_rooms) >= 2 and plausible_rooms[0] <= 30 and plausible_rooms[1] >= 40:
        rooms = plausible_rooms[1]
    elif plausible_rooms:
        rooms = plausible_rooms[0]
    meeting_space = max(space_values) if space_values else 0
    if meeting_space < 500:
        meeting_space = 0
    return rooms, meeting_space


def _hero_image(
    entry: str,
    property_name: str,
    hero_index: dict[str, str],
) -> tuple[str | None, str]:
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", entry)
    for image in images:
        if not _is_placeholder_image(image):
            return image, "Cvent Supplier Network"
    indexed = hero_index.get(normalize_property_name(property_name))
    if indexed:
        return indexed, "Travel Weekly / Meetings & Conventions"
    for image in images:
        if image.startswith("http"):
            return image, "Cvent Supplier Network placeholder"
    return None, ""


def _search_hero_index(input_dir: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in sorted(input_dir.glob("search_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        web_items = (data.get("data") or {}).get("web", [])
        for item in web_items:
            markdown = item.get("markdown") or ""
            title = str(item.get("title") or "")
            names = _candidate_names_from_search(title, markdown)
            image = _first_search_image(markdown)
            if not image:
                continue
            for name in names:
                key = normalize_property_name(name)
                if key and key not in index:
                    index[key] = image
    return index


def _candidate_names_from_search(title: str, markdown: str) -> list[str]:
    names: list[str] = []
    heading = re.search(r"^#\s+(.+)$", markdown, re.M)
    if heading:
        names.append(heading.group(1).strip())
    if title:
        names.append(re.split(r"\s+-\s+|\s+\|\s+", title)[0].strip())
    return names


def _first_search_image(markdown: str) -> str | None:
    for image in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
        if image.startswith("http") and "TW/x.png" not in image and not _is_placeholder_image(image):
            return image
    return None


def _is_placeholder_image(url: str) -> bool:
    lowered = url.casefold()
    return any(token in lowered for token in PLACEHOLDER_IMAGE_TOKENS)


def _rank_venues(venues: list[CventVenue]) -> list[CventVenue]:
    def score(venue: CventVenue) -> tuple[int, int, int, str]:
        metric_score = max(venue.rooms * 30, venue.meeting_space_sqft)
        real_image = 0 if venue.hero_source_name.endswith("placeholder") else 1
        named_city = 1 if venue.city else 0
        return (real_image, metric_score, named_city, venue.property_name.casefold())

    return sorted(venues, key=score, reverse=True)


def _draft_from_venue(
    venue: CventVenue,
    scene_type: str,
    geocode: dict[str, float],
    registry: dict[str, Any],
) -> CandidateDraft:
    if venue.rooms > 0:
        field_group = "keys"
        indicator_name = "keys"
        field_value = f"Cvent lists {venue.rooms:,} guest rooms for the venue."
        annual_visits = float(venue.rooms * 600)
    else:
        field_group = "meeting_ballroom_area"
        indicator_name = "meeting_ballroom_area"
        field_value = f"Cvent lists event or meeting space up to {venue.meeting_space_sqft:,} sq. ft."
        annual_visits = float(max(venue.meeting_space_sqft * 20, 25_000))

    bbox = registry.get("countries", {}).get(venue.country, {}).get("bbox", {})
    return CandidateDraft(
        region=COUNTRY_REGION.get(venue.country, "Unknown"),
        country=venue.country,
        city=venue.city,
        property_name=venue.property_name,
        scene_type=scene_type,
        annual_visits=annual_visits,
        latitude=geocode["latitude"],
        longitude=geocode["longitude"],
        geocode_precision=_geocode_precision(scene_type),
        map_source="Nominatim/OpenStreetMap public geocode",
        map_source_date=SOURCE_DATE,
        field_group=field_group,
        indicator_name=indicator_name,
        field_value=field_value,
        source_name="Cvent Supplier Network",
        source_tier="Tier 2",
        source_url=venue.source_url,
        source_date=SOURCE_DATE,
        evidence_type="Direct",
        bbox=bbox,
        source_type=SOURCE_TYPE,
        content_text=_content_text(venue),
        hero_image=_hero_payload(venue),
    )


def _hero_payload(venue: CventVenue) -> dict[str, Any] | None:
    if not venue.hero_url:
        return None
    return {
        "url": venue.hero_url,
        "alt_text": f"{venue.property_name} venue image",
        "source_name": venue.hero_source_name,
        "source_url": venue.source_url,
    }


def _content_text(venue: CventVenue) -> str:
    metrics = []
    if venue.rooms:
        metrics.append(f"{venue.rooms:,} guest rooms")
    if venue.meeting_space_sqft:
        metrics.append(f"{venue.meeting_space_sqft:,} sq. ft. meeting/event space")
    return (
        f"Cvent result card for {venue.property_name} in {venue.city}, {venue.country}; "
        f"venue type: {venue.venue_type}; metrics: {', '.join(metrics)}."
    )


def _scene_type(venue: CventVenue) -> str:
    text = f"{venue.venue_type} {venue.source_url}".casefold()
    if any(token in text for token in ("convention", "conference", "exhibition")):
        return "convention_center"
    return "luxury_hotel_mice"


def _geocode_precision(scene_type: str) -> str:
    if scene_type == "convention_center":
        return "convention venue centroid"
    return "hotel venue centroid"


def _is_property_venue(venue: CventVenue) -> bool:
    text = f"{venue.venue_type} {venue.source_url}".casefold()
    return any(
        token in text
        for token in (
            "/hotel/",
            "/resort/",
            "/boutique-hotel/",
            "/luxury-hotel/",
            "hotel",
            "resort",
            "boutique hotel",
            "luxury hotel",
            "convention",
            "conference",
            "exhibition",
        )
    )


def _city_from_url(source_url: str) -> str:
    match = re.search(r"/en-US/([^/]+)/", source_url)
    if not match:
        return ""
    return match.group(1).replace("-", " ").title()


def _venue_type_from_url(source_url: str) -> str:
    match = re.search(r"/en-US/[^/]+/([^/]+)/", source_url)
    if not match:
        return ""
    return match.group(1).replace("-", " ").title()


def _current_ready_counts(countries: list[str]) -> dict[str, int]:
    placeholders = ",".join("?" for _ in countries)
    query = (
        "select country, count(*) from scan_candidates "
        f"where country in ({placeholders}) and candidate_quality_status='ready' "
        "group by country"
    )
    counts = {country: 0 for country in countries}
    with sqlite3.connect(DB_PATH) as connection:
        for country, count in connection.execute(query, countries):
            counts[str(country)] = int(count)
    return counts


def _curation_summary(curation: Any | None) -> dict[str, Any] | None:
    if curation is None:
        return None
    return {
        "curation_run_id": curation.curation_run_id,
        "new_evidence_count": curation.new_evidence_count,
        "accepted_count": curation.accepted_count,
        "updated_count": curation.updated_count,
        "rejected_count": curation.rejected_count,
        "countries": curation.countries,
        "summary_path": str(curation.summary_path),
        "report_path": str(curation.report_path),
        "review_actions": curation.review_actions[:20],
        "progress_groups": curation.progress_groups,
    }


def _sync_summary(sync: Any | None) -> dict[str, Any] | None:
    if sync is None:
        return None
    return {
        "created": sync.created,
        "run_id": str(sync.run_id) if sync.run_id else None,
        "candidate_count": sync.candidate_count,
        "skipped_reason": sync.skipped_reason,
        "registry_candidate_count": sync.registry_candidate_count,
        "blocked_candidate_count": sync.blocked_candidate_count,
        "derived_refresh": sync.derived_refresh,
    }


def _render_report(summary: dict[str, Any]) -> str:
    rows = [
        "| Country | Before ready | Selected | After ready |",
        "| --- | ---: | ---: | ---: |",
    ]
    for country in summary["countries"]:
        rows.append(
            "| "
            + " | ".join(
                [
                    country,
                    str(summary["current_ready_before"].get(country, 0)),
                    str(summary["selected_by_country"].get(country, 0)),
                    str(summary["current_ready_after"].get(country, 0)),
                ]
            )
            + " |"
        )
    skipped = "\n".join(
        f"- {key}: {value}" for key, value in sorted(summary["skipped"].items())
    ) or "- None"
    return (
        "# Cvent APAC Candidate Expansion\n\n"
        + "\n".join(rows)
        + "\n\n"
        f"- Parsed records: {summary['parsed_count']}\n"
        f"- Selected drafts: {summary['selected_count']}\n"
        f"- Invalid drafts: {summary['invalid_count']}\n"
        f"- Raw evidence written/changed: {summary['raw_evidence_written_or_changed']}\n"
        f"- Firecrawl credits used by script: {summary['firecrawl_credits_used_by_script']}\n\n"
        "## Skipped\n\n"
        f"{skipped}\n"
    )


if __name__ == "__main__":
    main()
