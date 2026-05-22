from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import (
    CandidateDraft,
    load_effective_source_registry,
    validate_candidate_draft,
)
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import NEW_OPPORTUNITY, known_opportunity_index_from_registry
from isite2.growth.regional_targets import REGION_COUNTRIES
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_PATH = Path("outputs/isite2_dev.db")
DB_URL = f"sqlite+pysqlite:///{DB_PATH}"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
CACHE_DIR = OUTPUT_DIR / "public_structured_growth_cache"
SOURCE_TYPE = "public_structured_growth_pass"
TODAY = "2026-05-12"
USER_AGENT = "isite2-codex/0.1 public evidence research"
SPARQL_MAX_TIME_SECONDS = 45

COUNTRY_QIDS = {
    "Algeria": "Q262",
    "Argentina": "Q414",
    "Bahamas": "Q778",
    "Barbados": "Q244",
    "Benin": "Q962",
    "Bolivia": "Q750",
    "Botswana": "Q963",
    "Brazil": "Q155",
    "Burkina Faso": "Q965",
    "Cameroon": "Q1009",
    "Central African Republic": "Q929",
    "Chile": "Q298",
    "Colombia": "Q739",
    "Comoros": "Q970",
    "Cote d'Ivoire": "Q1008",
    "Djibouti": "Q977",
    "Ecuador": "Q736",
    "Egypt": "Q79",
    "Gabon": "Q1000",
    "Ghana": "Q117",
    "Kenya": "Q114",
    "Liberia": "Q1014",
    "Libya": "Q1016",
    "Malawi": "Q1020",
    "Mauritius": "Q1027",
    "Mexico": "Q96",
    "Morocco": "Q1028",
    "Mozambique": "Q1029",
    "Namibia": "Q1030",
    "Niger": "Q1032",
    "Nigeria": "Q1033",
    "Panama": "Q804",
    "Peru": "Q419",
    "Rwanda": "Q1037",
    "Sao Tome and Principe": "Q1039",
    "Sierra Leone": "Q1044",
    "South Africa": "Q258",
    "Suriname": "Q730",
    "Tanzania": "Q924",
    "Zambia": "Q953",
    "Sri Lanka": "Q854",
    "Cambodia": "Q424",
    "Maldives": "Q826",
    "Turkey": "Q43",
    "Philippines": "Q928",
    "Saudi Arabia": "Q851",
}

HOTEL_ALLOW = re.compile(
    r"(hilton|sheraton|hyatt|marriott|fairmont|intercontinental|four seasons|"
    r"ritz|radisson|westin|pullman|sofitel|kempinski|unique|palace|grand)",
    re.I,
)
OFFICE_ALLOW = re.compile(
    r"(tower|torre|office|business|corporate|bank|banco|edif[ií]cio|building|"
    r"centre|center|headquarters|world trade center)",
    re.I,
)
OFFICE_BLOCK = re.compile(r"(hotel|residential|residence|apartment|mansion|palace)", re.I)


@dataclass(frozen=True)
class ActiveProperty:
    property_id: str
    country: str
    city: str
    property_name: str
    scene_type: str
    latitude: float
    longitude: float
    geocode_precision: str
    map_source: str
    map_source_date: str
    annual_visits_est: float | None
    hero_image: dict | None
    source_urls: set[str]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Free public structured enrichment and candidate expansion for active iSite2 countries."
    )
    parser.add_argument("--max-new-per-country-scene", type=int, default=6)
    parser.add_argument("--max-new-total", type=int, default=120)
    parser.add_argument("--countries", nargs="+", default=None)
    parser.add_argument(
        "--include-slow-scenes",
        action="store_true",
        help="Also query hotel room count and office floor-area sources; these Wikidata paths can be slow.",
    )
    parser.add_argument("--skip-mall", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    countries = args.countries or _active_countries()
    countries = [country for country in countries if country in COUNTRY_QIDS]
    registry = load_effective_source_registry()
    store = EvidenceCurationStore(database_url=DB_URL)
    known_index = known_opportunity_index_from_registry(
        registry,
        session_factory=store.session_factory,
    )
    active_lookup = _active_lookup()

    query_rows = []
    query_rows.extend(_query_airport_patronage(countries))
    if not args.skip_mall:
        query_rows.extend(_query_mall_area(countries))
    if args.include_slow_scenes:
        query_rows.extend(_query_hotel_rooms(countries))
        query_rows.extend(_query_office_area(countries))

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
        "target_countries": countries,
        "query_rows": len(query_rows),
        "query_rows_by_kind": dict(Counter(row["kind"] for row in query_rows)),
        "draft_count": len(drafts),
        "drafts_by_match_status": dict(Counter(draft.identity_match_status for draft in drafts)),
        "drafts_by_scene": dict(Counter(draft.scene_type for draft in drafts)),
        "drafts_by_country": dict(Counter(draft.country for draft in drafts)),
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
            for draft in drafts[:40]
        ],
        "sample_skipped": skipped[:40],
        "sample_invalid": invalid[:40],
        "active_db": _active_db_summary(),
        "raw_evidence_ids": raw_ids,
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = OUTPUT_DIR / f"public_structured_growth_pass_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _query_airport_patronage(countries: list[str]) -> list[dict]:
    values = _country_values(countries)
    query = f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?patronage WHERE {{
  VALUES (?country ?countryName) {{ {values} }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q1248784; wdt:P625 ?coord; wdt:P3872 ?patronage.
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
}}
ORDER BY DESC(xsd:decimal(?patronage))
LIMIT 300
"""
    return [_row("airport_patronage", row) for row in _sparql("airport_patronage", query)]


def _query_mall_area(countries: list[str]) -> list[dict]:
    values = _country_values(countries)
    query = f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?area WHERE {{
  VALUES (?country ?countryName) {{ {values} }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q11315; wdt:P625 ?coord; wdt:P18 ?image; wdt:P2046 ?area.
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
  FILTER(xsd:decimal(?area) >= 40000)
}}
ORDER BY DESC(xsd:decimal(?area))
LIMIT 300
"""
    return [_row("mall_area", row) for row in _sparql("mall_area", query)]


def _query_hotel_rooms(countries: list[str]) -> list[dict]:
    rows = []
    for index, chunk in enumerate(_chunks(countries, 6)):
        values = _country_values(chunk)
        query = f"""
SELECT DISTINCT ?countryName ?item ?itemLabel ?coord ?image ?article ?rooms WHERE {{
  VALUES (?country ?countryName) {{ {values} }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q27686; wdt:P625 ?coord; wdt:P18 ?image; wdt:P8733 ?rooms.
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
  FILTER(xsd:decimal(?rooms) >= 150)
}}
ORDER BY DESC(xsd:decimal(?rooms))
LIMIT 300
"""
        rows.extend(_row("hotel_rooms", row) for row in _sparql(f"hotel_rooms_{index}", query))
        time.sleep(2)
    return rows


def _query_office_area(countries: list[str]) -> list[dict]:
    rows = []
    for index, chunk in enumerate(_chunks(countries, 6)):
        values = _country_values(chunk)
        query = f"""
SELECT DISTINCT ?countryName ?item ?itemLabel ?coord ?image ?article ?area WHERE {{
  VALUES (?country ?countryName) {{ {values} }}
  VALUES ?officeClass {{ wd:Q1021645 }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* ?officeClass; wdt:P625 ?coord; wdt:P18 ?image; wdt:P2046 ?area.
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,es,fr". }}
  FILTER(xsd:decimal(?area) >= 30000)
}}
ORDER BY DESC(xsd:decimal(?area))
LIMIT 300
"""
        rows.extend(_row("office_area", row) for row in _sparql(f"office_area_{index}", query))
        time.sleep(2)
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
    seen_dedupe: set[tuple[str, str, str, str, str]] = set()
    new_per_country_scene: Counter[tuple[str, str]] = Counter()
    new_total = 0

    for row in rows:
        country = row["country"]
        if country not in registry.get("countries", {}):
            continue
        scene_type = _scene_type(row)
        if scene_type is None:
            skipped.append(_skip(row, "unsupported row kind or label"))
            continue
        active = _match_active(row, scene_type, active_lookup)
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
            skipped.append(_skip(row, "failed draft build"))
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
                status="known_property",
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
        if dedupe in seen_dedupe:
            continue
        seen_dedupe.add(dedupe)
        validation = validate_candidate_draft(draft)
        if not validation.accepted:
            invalid.append({**_skip(row, "validation failed"), "issues": validation.issues})
            continue
        drafts.append(draft)
    return drafts, skipped, invalid


def _draft_from_row(
    row: dict,
    scene_type: str,
    *,
    active: ActiveProperty | None,
    registry: dict,
) -> CandidateDraft | None:
    country = row["country"]
    metric = _metric(row)
    if metric is None:
        return None
    field_group, indicator_name, field_value, annual_visits, content_text, geocode_precision = metric
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
        property_name = _clean_label(row["label"])
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
    kind = row["kind"]
    label = row["label"]
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
    if kind == "mall_area":
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
    if kind == "office_area":
        if not OFFICE_ALLOW.search(label) or OFFICE_BLOCK.search(label):
            return None
        area = float(row["area"])
        return (
            "office_gfa",
            "office_gfa",
            f"Wikidata area statement: {area:g} square meters.",
            None,
            f"{label} has Wikidata P2046 area value of {area:g} square meters.",
            "office tower centroid",
        )
    return None


def _scene_type(row: dict) -> str | None:
    return {
        "airport_patronage": "airport_terminal",
        "mall_area": "mall_mixed_use",
        "hotel_rooms": "luxury_hotel_mice",
        "office_area": "office_government",
    }.get(row["kind"])


def _match_active(row: dict, scene_type: str, active_lookup: dict) -> ActiveProperty | None:
    country_scene = (row["country"], scene_type)
    for url in [row.get("article"), row.get("item")]:
        active = active_lookup["by_url"].get(_normalize_url(url))
        if active is not None and (active.country, active.scene_type) == country_scene:
            return active
    label_norm = _normalize_name(row["label"])
    active = active_lookup["by_name_scene"].get((row["country"], scene_type, label_norm))
    if active is not None:
        return active
    try:
        lat, lon = _point(row["coord"])
    except ValueError:
        return None
    for candidate in active_lookup["by_country_scene"].get(country_scene, []):
        if abs(candidate.latitude - lat) <= 0.01 and abs(candidate.longitude - lon) <= 0.01:
            return candidate
    return None


def _active_lookup() -> dict:
    connection = _connection()
    property_rows = connection.execute(
        """
        SELECT p.*, sm.annual_visits_est
        FROM properties p
        JOIN scan_candidates sc ON sc.property_id = p.id
        LEFT JOIN scene_model_results sm
          ON sm.property_id = p.id AND sm.scan_run_id = sc.scan_run_id
        """
    ).fetchall()
    evidence_rows = connection.execute(
        "SELECT property_id, source_url FROM evidence_items WHERE source_url IS NOT NULL"
    ).fetchall()
    urls_by_property: dict[str, set[str]] = defaultdict(set)
    for row in evidence_rows:
        urls_by_property[row["property_id"]].add(_normalize_url(row["source_url"]))

    active_by_id = {}
    by_url = {}
    by_name_scene = {}
    by_country_scene: dict[tuple[str, str], list[ActiveProperty]] = defaultdict(list)
    for row in property_rows:
        hero_image = json.loads(row["hero_image"]) if row["hero_image"] else None
        active = ActiveProperty(
            property_id=row["id"],
            country=row["country"],
            city=row["city"],
            property_name=row["canonical_name"],
            scene_type=row["scene_type"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            geocode_precision=row["geocode_precision"],
            map_source=row["map_source"] or "",
            map_source_date=row["map_source_date"] or "",
            annual_visits_est=(
                float(row["annual_visits_est"])
                if row["annual_visits_est"] is not None
                else None
            ),
            hero_image=hero_image,
            source_urls=urls_by_property.get(row["id"], set()),
        )
        active_by_id[active.property_id] = active
        by_name_scene[(active.country, active.scene_type, _normalize_name(active.property_name))] = active
        by_country_scene[(active.country, active.scene_type)].append(active)
        for url in active.source_urls:
            by_url[url] = active
    return {
        "by_id": active_by_id,
        "by_url": by_url,
        "by_name_scene": by_name_scene,
        "by_country_scene": by_country_scene,
    }


def _sparql(name: str, query: str) -> list[dict]:
    cache_key = hashlib.sha256(query.encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"{name}_{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))["results"]["bindings"]
    url = "https://query.wikidata.org/sparql"
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            print(f"SPARQL {name}: attempt {attempt + 1}", flush=True)
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
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(12)
                continue
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(12)
                continue
    print(f"SPARQL query failed for {name}: {last_error}")
    return []


def _row(kind: str, raw: dict) -> dict:
    return {
        "kind": kind,
        "country": _value(raw, "countryName") or "",
        "item": _value(raw, "item") or "",
        "label": _clean_label(_value(raw, "itemLabel") or ""),
        "coord": _value(raw, "coord") or "",
        "image": _https(_value(raw, "image")),
        "article": _value(raw, "article"),
        "patronage": _value(raw, "patronage"),
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


def _active_countries() -> list[str]:
    connection = sqlite3.connect(DB_PATH)
    rows = connection.execute(
        "select country from properties group by country having count(*) > 0 order by country"
    ).fetchall()
    connection.close()
    return [row[0] for row in rows]


def _active_db_summary() -> dict:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    def rows(sql: str) -> list[dict]:
        return [dict(row) for row in connection.execute(sql)]

    def one(sql: str) -> int:
        return int(connection.execute(sql).fetchone()[0])

    summary = {
        "total_properties": one("select count(*) from properties"),
        "country_counts": rows(
            "select country, count(*) as n from properties group by country order by n desc"
        ),
        "scene_counts": rows(
            "select scene_type, count(*) as n from properties group by scene_type order by scene_type"
        ),
        "missing_hero": one(
            "select count(*) from properties "
            "where hero_image is null or json_extract(hero_image,'$.url') is null "
            "or json_extract(hero_image,'$.url') not like 'http%'"
        ),
        "non_ready_scan_candidates": one(
            "select count(*) from scan_candidates where candidate_quality_status!='ready'"
        ),
        "duplicate_identity_groups": one(
            "select count(*) from (select property_identity_key from properties "
            "group by property_identity_key having count(*)>1)"
        ),
    }
    connection.close()
    return summary


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


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


def _normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(str(url).strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = unquote(parsed.path).rstrip("/")
    return f"{scheme}://{netloc}{path}"


def _normalize_name(value: str | None) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[_/|,;:()\[\]{}]+", " ", text)
    text = re.sub(r"[^a-z0-9\u00c0-\u024f\s.-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", label or "").strip()


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
    article = row.get("article") or ""
    label = row.get("label") or ""
    text = f"{article} {label}"
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
        if token in text or token.replace(" ", "_") in article:
            return token
    return ""


def _hero(label: str, source_url: str, image_url: str) -> dict[str, str]:
    return {
        "url": _https(image_url) or image_url,
        "alt_text": f"{label} public image",
        "source_url": source_url,
        "source_name": "Wikimedia Commons",
        "source_date": TODAY,
        "license": "Wikimedia Commons public file metadata",
    }


def _region_for_country(country: str) -> str:
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return "Unknown"


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
    draft_country_lines = "\n".join(
        f"- {country}: {count}" for country, count in summary["drafts_by_country"].items()
    )
    return (
        "# Public Structured Growth Pass\n\n"
        f"- Target countries: {summary['target_country_count']}\n"
        "- Firecrawl credits used: 0\n"
        f"- Structured rows: {summary['query_rows']} {summary['query_rows_by_kind']}\n"
        f"- Drafts written: {summary['draft_count']} {summary['drafts_by_scene']}\n"
        f"- Raw evidence written/changed: {summary['raw_evidence_written_or_changed']}\n"
        f"- Curation accepted new: {(summary['curation'] or {}).get('accepted_count', 0)}\n"
        f"- Curation updated existing: {(summary['curation'] or {}).get('updated_count', 0)}\n"
        f"- Curation rejected: {(summary['curation'] or {}).get('rejected_count', 0)}\n"
        f"- Active total properties: {summary['active_db']['total_properties']}\n"
        f"- Missing hero images: {summary['active_db']['missing_hero']}\n"
        f"- Non-ready scan candidates: {summary['active_db']['non_ready_scan_candidates']}\n"
        f"- Duplicate identity groups: {summary['active_db']['duplicate_identity_groups']}\n\n"
        "## Drafts By Country\n"
        f"{draft_country_lines}\n\n"
        "## Active Scenes\n"
        f"{scene_lines}\n"
    )


if __name__ == "__main__":
    main()
