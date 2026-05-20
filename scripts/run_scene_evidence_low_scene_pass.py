from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import (
    CandidateDraft,
    load_effective_source_registry,
    validate_candidate_draft,
)
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import (
    KNOWN_PROPERTY,
    NEW_OPPORTUNITY,
    known_opportunity_index_from_registry,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

from scripts.run_public_structured_growth_pass import (
    COUNTRY_QIDS,
    _active_countries,
    _active_db_summary,
    _active_lookup,
    _hero,
    _match_active,
    _region_for_country,
)

DB_PATH = Path("outputs/isite2_dev.db")
DB_URL = f"sqlite+pysqlite:///{DB_PATH}"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
CACHE_DIR = OUTPUT_DIR / "scene_evidence_low_scene_cache"
SOURCE_TYPE = "scene_evidence_low_scene_pass"
TODAY = datetime.now().date().isoformat()
USER_AGENT = "isite2-codex/0.1 public evidence research"
SPARQL_MAX_TIME_SECONDS = 45

LOW_COUNT_SCENES = {"mall_mixed_use", "luxury_hotel_mice", "convention_center"}
EVIDENCE_ONLY_SCENES = {"airport_terminal", "stadium"}
HOTEL_ALLOW = re.compile(
    r"(hilton|sheraton|hyatt|marriott|fairmont|intercontinental|four seasons|"
    r"ritz|radisson|westin|pullman|sofitel|kempinski|mandarin|st.?regis|"
    r"waldorf|luxury|palace|grand|hotel|resort)",
    re.I,
)
CONVENTION_ALLOW = re.compile(
    r"(convention|conference|congress|exhibition|expo|events?|centre|center|"
    r"centro|pavilion|fair|messe|international conference)",
    re.I,
)
MALL_ALLOW = re.compile(r"(mall|shopping|commercial|centre|center|plaza|galleria)", re.I)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich airport/stadium evidence and expand low-count mall/hotel/convention scenes."
    )
    parser.add_argument("--max-new-per-country-scene", type=int, default=4)
    parser.add_argument("--max-new-total", type=int, default=100)
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-airport", action="store_true")
    parser.add_argument("--skip-stadium", action="store_true")
    parser.add_argument("--skip-low-scenes", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    countries = [country for country in _active_countries() if country in COUNTRY_QIDS]
    registry = load_effective_source_registry()
    store = EvidenceCurationStore(database_url=DB_URL)
    known_index = known_opportunity_index_from_registry(
        registry,
        session_factory=store.session_factory,
    )
    active_lookup = _active_lookup()

    query_rows: list[dict] = []
    if not args.skip_airport:
        query_rows.extend(_query_airport_patronage(countries))
    if not args.skip_stadium:
        query_rows.extend(_query_stadium_capacity(countries))
    if not args.skip_low_scenes:
        query_rows.extend(_query_mall_area(countries))
        query_rows.extend(_query_hotel_rooms(countries))
        query_rows.extend(_query_convention_scale(countries))

    drafts, skipped, invalid = _drafts_from_rows(
        query_rows,
        active_lookup=active_lookup,
        registry=registry,
        known_index=known_index,
        max_new_per_country_scene=args.max_new_per_country_scene,
        max_new_total=args.max_new_total,
    )

    raw_ids = []
    written_or_changed = 0
    for draft in drafts:
        result = store.upsert_candidate_evidence(draft, source_type=SOURCE_TYPE)
        raw_ids.append(result.raw_evidence_id)
        if result.is_new_evidence or result.is_changed_evidence:
            written_or_changed += 1

    curation = run_pending_evidence_curation(store=store, output_dir=OUTPUT_DIR)
    sync = None
    if not args.skip_sync:
        repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
        sync = sync_overlay_to_active_repository(repository)

    summary = {
        "mode": SOURCE_TYPE,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "target_country_count": len(countries),
        "query_rows": len(query_rows),
        "query_rows_by_kind": dict(Counter(row["kind"] for row in query_rows)),
        "draft_count": len(drafts),
        "drafts_by_match_status": dict(Counter(d.identity_match_status for d in drafts)),
        "drafts_by_scene": dict(Counter(d.scene_type for d in drafts)),
        "drafts_by_country": dict(Counter(d.country for d in drafts)),
        "raw_evidence_written_or_changed": written_or_changed,
        "skipped_count": len(skipped),
        "invalid_count": len(invalid),
        "curation": None
        if curation is None
        else {
            "curation_run_id": curation.curation_run_id,
            "new_evidence_count": curation.new_evidence_count,
            "accepted_count": curation.accepted_count,
            "updated_count": curation.updated_count,
            "rejected_count": curation.rejected_count,
            "report_path": str(curation.report_path),
            "summary_path": str(curation.summary_path),
        },
        "overlay_sync": None
        if sync is None
        else {
            "created": sync.created,
            "run_id": str(sync.run_id) if sync.run_id else None,
            "candidate_count": sync.candidate_count,
            "registry_candidate_count": sync.registry_candidate_count,
            "blocked_candidate_count": sync.blocked_candidate_count,
            "skipped_reason": sync.skipped_reason,
            "derived_refresh": sync.derived_refresh,
        },
        "active_db": _active_db_summary(),
        "sample_drafts": [
            {
                "country": draft.country,
                "city": draft.city,
                "property_name": draft.property_name,
                "scene_type": draft.scene_type,
                "field_group": draft.field_group,
                "identity_match_status": draft.identity_match_status,
                "source_url": draft.source_url,
            }
            for draft in drafts[:50]
        ],
        "sample_skipped": skipped[:50],
        "sample_invalid": invalid[:50],
        "raw_evidence_ids": raw_ids,
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = OUTPUT_DIR / f"{SOURCE_TYPE}_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _query_airport_patronage(countries: list[str]) -> list[dict]:
    values = _country_values(countries)
    query = f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?patronage WHERE {{
  VALUES (?country ?countryName) {{ {values} }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q1248784; wdt:P625 ?coord; wdt:P3872 ?patronage.
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
}}
ORDER BY DESC(xsd:decimal(?patronage))
LIMIT 600
"""
    return [_row("airport_patronage", row) for row in _sparql("airport_patronage", query)]


def _query_stadium_capacity(countries: list[str]) -> list[dict]:
    rows: list[dict] = []
    for index, chunk in enumerate(_chunks(countries, 6)):
        values = _country_values(chunk)
        query = f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?capacity WHERE {{
  VALUES (?country ?countryName) {{ {values} }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q483110; wdt:P625 ?coord; wdt:P1083 ?capacity.
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
  FILTER(xsd:decimal(?capacity) >= 10000)
}}
LIMIT 500
"""
        rows.extend(_row("stadium_capacity", row) for row in _sparql(f"stadium_capacity_{index}", query))
        time.sleep(1)
    return rows


def _query_mall_area(countries: list[str]) -> list[dict]:
    rows: list[dict] = []
    for country in countries:
        query = f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?area WHERE {{
  VALUES (?country ?countryName) {{ (wd:{COUNTRY_QIDS[country]} "{country}") }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q11315; wdt:P625 ?coord; wdt:P2046 ?area.
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
  FILTER(xsd:decimal(?area) >= 35000)
}}
LIMIT 80
"""
        rows.extend(_row("mall_area", row) for row in _sparql(f"mall_area_{country}", query))
    return rows


def _query_hotel_rooms(countries: list[str]) -> list[dict]:
    rows: list[dict] = []
    for country in countries:
        query = f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?rooms WHERE {{
  VALUES (?country ?countryName) {{ (wd:{COUNTRY_QIDS[country]} "{country}") }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q27686; wdt:P625 ?coord; wdt:P8733 ?rooms.
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
  FILTER(xsd:decimal(?rooms) >= 150)
}}
LIMIT 80
"""
        rows.extend(_row("hotel_rooms", row) for row in _sparql(f"hotel_rooms_{country}", query))
    return rows


def _query_convention_scale(countries: list[str]) -> list[dict]:
    rows: list[dict] = []
    for country in countries:
        query = f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?capacity ?area WHERE {{
  VALUES (?country ?countryName) {{ (wd:{COUNTRY_QIDS[country]} "{country}") }}
  ?item wdt:P17 ?country; wdt:P625 ?coord.
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P1083 ?capacity. }}
  OPTIONAL {{ ?item wdt:P2046 ?area. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
  FILTER(BOUND(?capacity) || BOUND(?area))
  FILTER(
    CONTAINS(LCASE(STR(?itemLabel)), "convention")
    || CONTAINS(LCASE(STR(?itemLabel)), "conference")
    || CONTAINS(LCASE(STR(?itemLabel)), "congress")
    || CONTAINS(LCASE(STR(?itemLabel)), "exhibition")
    || CONTAINS(LCASE(STR(?itemLabel)), "expo")
    || CONTAINS(LCASE(STR(?itemLabel)), "centro de conven")
  )
}}
LIMIT 80
"""
        rows.extend(_row("convention_scale", row) for row in _sparql(f"convention_scale_{country}", query))
    return rows


def _drafts_from_rows(
    rows: list[dict],
    *,
    active_lookup: dict,
    registry: dict,
    known_index,
    max_new_per_country_scene: int,
    max_new_total: int,
) -> tuple[list[CandidateDraft], list[dict], list[dict]]:
    drafts: list[CandidateDraft] = []
    skipped: list[dict] = []
    invalid: list[dict] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    new_per_country_scene: Counter[tuple[str, str]] = Counter()
    new_total = 0

    for row in rows:
        scene_type = _scene_type(row)
        country = row["country"]
        if scene_type is None or country not in registry.get("countries", {}):
            continue
        active = _match_active(row, scene_type, active_lookup)
        if active is None and scene_type in EVIDENCE_ONLY_SCENES:
            skipped.append(_skip(row, "evidence-only scene; property not active"))
            continue
        if active is None:
            if new_total >= max_new_total:
                skipped.append(_skip(row, "max new total reached"))
                continue
            if new_per_country_scene[(country, scene_type)] >= max_new_per_country_scene:
                skipped.append(_skip(row, "max new per country scene reached"))
                continue
            if not row.get("image"):
                skipped.append(_skip(row, "new candidate missing image"))
                continue

        draft = _draft_from_row(row, scene_type, active=active, registry=registry)
        if draft is None:
            skipped.append(_skip(row, "failed metric or draft build"))
            continue
        match = known_index.match(
            country=draft.country,
            city=draft.city,
            property_name=draft.property_name,
            scene_type=draft.scene_type,
            latitude=draft.latitude,
            longitude=draft.longitude,
            source_url=row.get("article") or row.get("item") or draft.source_url,
        )
        if active is not None:
            draft = _replace_match(
                draft,
                status=KNOWN_PROPERTY,
                matched_property_id=active.property_id,
                matched_property_name=active.property_name,
                reason="active property matched by source URL, coordinate, or canonical name",
            )
        elif match.status == NEW_OPPORTUNITY:
            draft = _replace_match(draft, status=NEW_OPPORTUNITY)
            new_per_country_scene[(draft.country, draft.scene_type)] += 1
            new_total += 1
        else:
            skipped.append(_skip(row, match.reason or match.status))
            continue

        dedupe = (
            draft.country.casefold(),
            draft.scene_type.casefold(),
            draft.property_name.casefold(),
            draft.field_group.casefold(),
            draft.source_url.casefold(),
        )
        if dedupe in seen:
            continue
        seen.add(dedupe)
        validation = validate_candidate_draft(draft)
        if not validation.accepted:
            invalid.append({**_skip(row, "validation failed"), "issues": validation.issues})
            continue
        drafts.append(draft)
    return drafts, skipped, invalid


def _draft_from_row(row: dict, scene_type: str, *, active, registry: dict) -> CandidateDraft | None:
    metric = _metric(row)
    if metric is None:
        return None
    field_group, indicator_name, field_value, annual_visits, content_text, geocode_precision = metric
    country = row["country"]
    if active is not None:
        city = active.city
        property_name = active.property_name
        latitude = active.latitude
        longitude = active.longitude
        map_source = active.map_source or ""
        map_source_date = active.map_source_date or ""
        hero_image = active.hero_image
    else:
        city = _city(row, country)
        property_name = row["label"]
        latitude, longitude = _point(row["coord"])
        map_source = "Wikidata coordinate statement"
        map_source_date = TODAY
        hero_image = _hero(property_name, row.get("article") or row["item"], row["image"])

    return CandidateDraft(
        region=_region_for_country(country),
        country=country,
        city=city,
        property_name=property_name,
        scene_type=scene_type,
        annual_visits=annual_visits,
        latitude=latitude,
        longitude=longitude,
        geocode_precision=geocode_precision,
        map_source=map_source,
        map_source_date=map_source_date,
        field_group=field_group,
        indicator_name=indicator_name,
        field_value=field_value,
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url=row["item"],
        source_date=TODAY,
        evidence_type="Direct",
        bbox=registry["countries"][country]["bbox"],
        source_type=SOURCE_TYPE,
        content_text=content_text,
        hero_image=hero_image,
    )


def _metric(row: dict) -> tuple[str, str, str, float | None, str, str] | None:
    label = row["label"]
    kind = row["kind"]
    if kind == "airport_patronage":
        value = int(float(row["patronage"]))
        return (
            "annual_passenger_throughput",
            "annual_passenger_throughput",
            f"Wikidata patronage statement: {value:,} passengers/year.",
            float(value),
            f"{label} has Wikidata P3872 patronage value of {value:,}.",
            "airport terminal centroid",
        )
    if kind == "stadium_capacity":
        capacity = int(float(row["capacity"]))
        return (
            "seat_count",
            "seat_count",
            f"Wikidata capacity statement: {capacity:,} seats/capacity.",
            None,
            f"{label} has Wikidata P1083 capacity value of {capacity:,}.",
            "stadium centroid",
        )
    if kind == "mall_area":
        if not MALL_ALLOW.search(label):
            return None
        area = float(row["area"])
        return (
            "gla",
            "gla",
            f"Wikidata area statement: {area:g} square meters.",
            None,
            f"{label} has Wikidata P2046 area value of {area:g} square meters.",
            "shopping mall centroid",
        )
    if kind == "hotel_rooms":
        if not HOTEL_ALLOW.search(label):
            return None
        rooms = int(float(row["rooms"]))
        return (
            "keys",
            "keys",
            f"Wikidata room count statement: {rooms:,} rooms/keys.",
            None,
            f"{label} has Wikidata P8733 room count value of {rooms:,}.",
            "hotel venue centroid",
        )
    if kind == "convention_scale":
        if not CONVENTION_ALLOW.search(label):
            return None
        area = row.get("area")
        capacity = row.get("capacity")
        if area:
            value = float(area)
            return (
                "exhibition_area",
                "exhibition_area",
                f"Wikidata area statement: {value:g} square meters.",
                None,
                f"{label} has Wikidata P2046 area value of {value:g} square meters.",
                "exhibition venue centroid",
            )
        if capacity:
            value = int(float(capacity))
            return (
                "peak_event_capacity",
                "peak_event_capacity",
                f"Wikidata capacity statement: {value:,} people/seats.",
                None,
                f"{label} has Wikidata P1083 capacity value of {value:,}.",
                "exhibition venue centroid",
            )
    return None


def _scene_type(row: dict) -> str | None:
    return {
        "airport_patronage": "airport_terminal",
        "stadium_capacity": "stadium",
        "mall_area": "mall_mixed_use",
        "hotel_rooms": "luxury_hotel_mice",
        "convention_scale": "convention_center",
    }.get(row["kind"])


def _sparql(name: str, query: str) -> list[dict]:
    cache_key = hashlib.sha256(query.encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"{_safe_name(name)}_{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))["results"]["bindings"]
    url = "https://query.wikidata.org/sparql"
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            completed = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--fail",
                    "--max-time",
                    str(SPARQL_MAX_TIME_SECONDS),
                    "-H",
                    f"User-Agent: {USER_AGENT}",
                    "-H",
                    "Accept: application/sparql-results+json",
                    "--data-urlencode",
                    f"query={query}",
                    "--data-urlencode",
                    "format=json",
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return payload["results"]["bindings"]
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(10)
                continue
    print(f"SPARQL query failed for {name}: {last_error}")
    return []


def _row(kind: str, raw: dict) -> dict:
    return {
        "kind": kind,
        "country": _value(raw, "countryName") or "",
        "city": _value(raw, "cityLabel") or "",
        "item": _value(raw, "item") or "",
        "label": _clean_label(_value(raw, "itemLabel") or ""),
        "coord": _value(raw, "coord") or "",
        "image": _https(_value(raw, "image")),
        "article": _value(raw, "article"),
        "patronage": _value(raw, "patronage"),
        "capacity": _value(raw, "capacity"),
        "area": _value(raw, "area"),
        "rooms": _value(raw, "rooms"),
    }


def _replace_match(
    draft: CandidateDraft,
    *,
    status: str,
    matched_property_id: str | None = None,
    matched_property_name: str | None = None,
    reason: str | None = None,
) -> CandidateDraft:
    data = draft.__dict__.copy()
    data.update(
        {
            "identity_match_status": status,
            "matched_property_id": matched_property_id,
            "matched_property_name": matched_property_name,
            "identity_match_reason": reason,
        }
    )
    return CandidateDraft(**data)


def _country_values(countries: list[str]) -> str:
    return "\n  ".join(f'(wd:{COUNTRY_QIDS[country]} "{country}")' for country in countries)


def _chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _value(row: dict, key: str) -> str | None:
    cell = row.get(key)
    return cell.get("value") if cell else None


def _point(text: str) -> tuple[float, float]:
    match = re.match(r"Point\(([-0-9.]+) ([-0-9.]+)\)", text or "")
    if not match:
        raise ValueError(f"bad coordinate point: {text}")
    return float(match.group(2)), float(match.group(1))


def _https(url: str | None) -> str | None:
    if not url:
        return None
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


_BRAZIL_RESTRICTED_CITY_LABELS = {
    "São Paulo",
    "Sao Paulo",
    "São Paulo Metropolitan Region",
    "Central Zone of São Paulo",
    "Rio de Janeiro",
}


def _is_brazil_restricted_city(label: str) -> bool:
    return _clean_label(label) in _BRAZIL_RESTRICTED_CITY_LABELS


def _city(row: dict, country: str) -> str:
    city = _clean_label(row.get("city") or "")
    if city:
        if country != "Brazil" and _is_brazil_restricted_city(city):
            return ""
        return city
    text = f"{row.get('article') or ''} {row.get('label') or ''}"
    for token in [
        "São Paulo",
        "Rio de Janeiro",
        "Cairo",
        "Lagos",
        "Nairobi",
        "Casablanca",
        "Johannesburg",
        "Bogotá",
        "Mexico City",
        "Cape Town",
        "Accra",
        "Kigali",
        "Algiers",
        "Panama City",
        "Buenos Aires",
        "Santiago",
        "Medellín",
        "Salvador",
        "Durban",
    ]:
        if country != "Brazil" and _is_brazil_restricted_city(token):
            continue
        if token in text or token.replace(" ", "_") in text:
            return token
    return ""


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", label or "").strip()


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value)


def _skip(row: dict, reason: str) -> dict:
    return {
        "kind": row.get("kind"),
        "country": row.get("country"),
        "label": row.get("label"),
        "scene_type": _scene_type(row),
        "reason": reason,
    }


def _render_report(summary: dict) -> str:
    scene_lines = "\n".join(
        f"- {item['scene_type']}: {item['n']}"
        for item in summary["active_db"]["scene_counts"]
    )
    draft_scene_lines = "\n".join(
        f"- {scene}: {count}" for scene, count in summary["drafts_by_scene"].items()
    )
    draft_country_lines = "\n".join(
        f"- {country}: {count}" for country, count in summary["drafts_by_country"].items()
    )
    return (
        "# Scene Evidence And Low Scene Expansion Pass\n\n"
        f"- Target countries: {summary['target_country_count']}\n"
        "- Firecrawl credits used by this script: 0\n"
        f"- Structured rows: {summary['query_rows']} {summary['query_rows_by_kind']}\n"
        f"- Drafts written: {summary['draft_count']}\n"
        f"- Raw evidence written/changed: {summary['raw_evidence_written_or_changed']}\n"
        f"- Curation accepted new: {(summary['curation'] or {}).get('accepted_count', 0)}\n"
        f"- Curation updated existing: {(summary['curation'] or {}).get('updated_count', 0)}\n"
        f"- Curation rejected: {(summary['curation'] or {}).get('rejected_count', 0)}\n"
        f"- Active total properties: {summary['active_db']['total_properties']}\n"
        f"- Missing hero images: {summary['active_db']['missing_hero']}\n"
        f"- Non-ready scan candidates: {summary['active_db']['non_ready_scan_candidates']}\n"
        f"- Duplicate identity groups: {summary['active_db']['duplicate_identity_groups']}\n\n"
        "## Drafts By Scene\n"
        f"{draft_scene_lines}\n\n"
        "## Drafts By Country\n"
        f"{draft_country_lines}\n\n"
        "## Active Scenes\n"
        f"{scene_lines}\n"
    )


if __name__ == "__main__":
    main()
