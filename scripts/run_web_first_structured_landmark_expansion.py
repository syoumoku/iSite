from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import time
from collections import Counter
from datetime import UTC, date, datetime
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
    NEW_OPPORTUNITY,
    known_opportunity_index_from_registry,
)
from isite2.growth.regional_targets import REGION_COUNTRIES
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_URL = "sqlite+pysqlite:///outputs/isite2_dev.db"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
CACHE_DIR = OUTPUT_DIR / "structured_web_cache"
TODAY = date.today().isoformat()
SOURCE_TYPE = "web_first_structured_landmark"
USER_AGENT = "isite2-codex/0.1 public evidence research"
QUERY_COOLDOWN_SECONDS = 65
SPARQL_MAX_TIME_SECONDS = 60

COUNTRY_QIDS = {
    "Algeria": "Q262",
    "Angola": "Q916",
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
    "Chad": "Q657",
    "Chile": "Q298",
    "Colombia": "Q739",
    "Comoros": "Q970",
    "Congo": "Q971",
    "Cote d'Ivoire": "Q1008",
    "Czech Republic": "Q213",
    "Democratic Republic of the Congo": "Q974",
    "Djibouti": "Q977",
    "Ecuador": "Q736",
    "Egypt": "Q79",
    "Equatorial Guinea": "Q983",
    "Eritrea": "Q986",
    "Gabon": "Q1000",
    "Gambia": "Q1005",
    "France": "Q142",
    "Germany": "Q183",
    "Ghana": "Q117",
    "Greece": "Q41",
    "Guinea": "Q1006",
    "Guinea-Bissau": "Q1007",
    "Eswatini": "Q1050",
    "Zimbabwe": "Q954",
    "Reunion": "Q17070",
    "Somalia": "Q1045",
    "Kenya": "Q114",
    "Lesotho": "Q1013",
    "Liberia": "Q1014",
    "Libya": "Q1016",
    "Madagascar": "Q1019",
    "Malawi": "Q1020",
    "Mali": "Q912",
    "Mauritania": "Q1025",
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
    "South Sudan": "Q958",
    "Suriname": "Q730",
    "Tanzania": "Q924",
    "Thailand": "Q869",
    "Togo": "Q945",
    "Zambia": "Q953",
    "Sri Lanka": "Q854",
    "Cambodia": "Q424",
    "Maldives": "Q826",
    "Turkey": "Q43",
    "Philippines": "Q928",
    "Vietnam": "Q881",
    "Indonesia": "Q252",
    "Tunisia": "Q948",
    "Uganda": "Q1036",
    "Slovakia": "Q214",
    "Moldova": "Q217",
    "Cyprus": "Q229",
    "Albania": "Q222",
    "North Macedonia": "Q221",
    "Bulgaria": "Q219",
    "Croatia": "Q224",
    "Slovenia": "Q215",
    "Bosnia and Herzegovina": "Q225",
    "Serbia": "Q403",
    "Montenegro": "Q236",
    "Nicaragua": "Q811",
    "Venezuela": "Q717",
    "Haiti": "Q790",
    "Uruguay": "Q77",
    "Papua New Guinea": "Q691",
    "Solomon Islands": "Q685",
    "Fiji": "Q712",
    "Nepal": "Q837",
    "Laos": "Q819",
    "Brunei": "Q921",
    "Afghanistan": "Q889",
    "Yemen": "Q805",
    "Lebanon": "Q822",
    "Bahrain": "Q398",
    "Kuwait": "Q817",
    "Kazakhstan": "Q232",
    "Pakistan": "Q843",
    "Uzbekistan": "Q265",
    "Georgia": "Q230",
    "Azerbaijan": "Q227",
    "Kyrgyzstan": "Q813",
    "Mongolia": "Q711",
    "Tajikistan": "Q863",
    "Turkmenistan": "Q874",
    "Armenia": "Q399",
}

OFFICE_ALLOW = re.compile(
    r"(bank|business|corporate|center|centre|tower|torre|edif[ií]cio|building|"
    r"empresarial|tribunal|banco|headquarters|office)",
    re.I,
)
OFFICE_BLOCK = re.compile(
    r"(hotel|residence|residential|mansion|mans[aã]o|palace|pal[aá]cio|"
    r"condominium|apartment|apartamento|altos do)",
    re.I,
)
CONVENTION_ALLOW = re.compile(
    r"(convention|conference|congress|exhibition|expo|events?|centre|center|"
    r"centro|pavilion|fair|riocentro)",
    re.I,
)
CONVENTION_BLOCK = re.compile(r"(stadium|arena|gin[aá]sio|gymnasium)", re.I)
HOTEL_ALLOW = re.compile(
    r"(hilton|sheraton|hyatt|marriott|fairmont|intercontinental|four seasons|"
    r"ritz|radisson|westin|pullman|sofitel|kempinski|unique)",
    re.I,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Web-first structured expansion for countries already in the active DB."
    )
    parser.add_argument("--max-per-country-scene", type=int, default=30)
    parser.add_argument("--countries", nargs="+", default=None)
    parser.add_argument("--include-brazil", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument(
        "--query-set",
        choices=["landmark", "gap"],
        default="landmark",
        help="landmark runs the broad first-pass set; gap targets weak second-pass scenes.",
    )
    parser.add_argument(
        "--scenes",
        nargs="+",
        default=None,
        help="Optional scene/query names to run, e.g. stadium mall_mixed_use.",
    )
    parser.add_argument("--query-cooldown-seconds", type=int, default=QUERY_COOLDOWN_SECONDS)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    registry = load_effective_source_registry()
    store = EvidenceCurationStore(database_url=DB_URL)
    known_index = known_opportunity_index_from_registry(
        registry,
        session_factory=store.session_factory,
    )

    countries = args.countries or _active_countries()
    if args.countries is None and not args.include_brazil:
        countries = [country for country in countries if country != "Brazil"]
    unsupported_countries = [country for country in countries if country not in COUNTRY_QIDS]
    if args.countries and unsupported_countries:
        raise SystemExit(
            "Unsupported explicit countries: " + ", ".join(sorted(unsupported_countries))
        )
    countries = [country for country in countries if country in COUNTRY_QIDS]
    if not countries:
        raise SystemExit("No supported countries selected; refusing to run global curation or sync.")
    country_values = _country_values(countries)

    planned: list[CandidateDraft] = []
    query_stats: list[dict[str, object]] = []
    queries = _gap_queries(country_values) if args.query_set == "gap" else _queries(country_values)
    if args.scenes:
        requested_scenes = {scene.strip() for scene in args.scenes if scene.strip()}
        queries = [(query_name, query) for query_name, query in queries if query_name in requested_scenes]
    for query_index, (query_name, query) in enumerate(queries):
        rows = _sparql(query_name, query)
        query_stats.append({"query": query_name, "rows": len(rows)})
        planned.extend(_drafts_from_rows(query_name, rows, registry, args.max_per_country_scene))
        if query_index < len(queries) - 1 and args.query_cooldown_seconds > 0:
            time.sleep(args.query_cooldown_seconds)

    accepted, invalid, skipped = _filter_candidates(
        planned,
        known_index=known_index,
        max_per_country_scene=args.max_per_country_scene,
    )
    raw_ids = []
    written_or_changed = 0
    for draft in accepted:
        result = store.upsert_candidate_evidence(draft, source_type=SOURCE_TYPE)
        raw_ids.append(result.raw_evidence_id)
        if result.is_new_evidence or result.is_changed_evidence:
            written_or_changed += 1

    curation = run_pending_evidence_curation(store=store, output_dir=OUTPUT_DIR)
    sync = None
    if not args.skip_sync:
        repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
        sync = sync_overlay_to_active_repository(repository)

    summary = _summary(
        countries=countries,
        query_stats=query_stats,
        planned=planned,
        accepted=accepted,
        invalid=invalid,
        skipped=skipped,
        raw_ids=raw_ids,
        written_or_changed=written_or_changed,
        curation=curation,
        sync=sync,
    )
    summary["query_set"] = args.query_set
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = OUTPUT_DIR / f"web_first_structured_landmark_expansion_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _active_countries() -> list[str]:
    connection = sqlite3.connect("outputs/isite2_dev.db")
    rows = connection.execute(
        "select country from properties group by country having count(*) > 0 order by country"
    ).fetchall()
    connection.close()
    return [row[0] for row in rows]


def _country_values(countries: list[str]) -> str:
    return "\n  ".join(
        f'(wd:{COUNTRY_QIDS[country]} "{country}")'
        for country in countries
        if country in COUNTRY_QIDS
    )


def _queries(country_values: str) -> list[tuple[str, str]]:
    values = f"VALUES (?country ?countryName) {{\n  {country_values}\n}}"
    return [
        (
            "airport_terminal",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?iata ?icao WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q1248784; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P238 ?iata. }}
  OPTIONAL {{ ?item wdt:P239 ?icao. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(BOUND(?iata) || BOUND(?icao) || CONTAINS(LCASE(STR(?itemLabel)), "international"))
}}
LIMIT 1200
""",
        ),
        (
            "transport_hub",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel
       (COUNT(DISTINCT ?line) AS ?lineCount) WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q928830; wdt:P625 ?coord; wdt:P18 ?image; wdt:P81 ?line.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
}}
GROUP BY ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel
HAVING(?lineCount >= 2)
ORDER BY DESC(?lineCount) ?countryName ?itemLabel
LIMIT 1200
""",
        ),
        (
            "stadium",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?capacity WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q483110; wdt:P625 ?coord; wdt:P18 ?image; wdt:P1083 ?capacity.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(xsd:decimal(?capacity) >= 15000)
}}
ORDER BY DESC(xsd:decimal(?capacity)) ?countryName ?itemLabel
LIMIT 1200
""",
        ),
        (
            "office_government",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?height ?floors WHERE {{
  {values}
  VALUES ?officeClass {{ wd:Q1021645 }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* ?officeClass; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P2048 ?height. }}
  OPTIONAL {{ ?item wdt:P1101 ?floors. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(BOUND(?height) || BOUND(?floors))
}}
LIMIT 800
""",
        ),
        (
            "convention_center",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?capacity ?area WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q641226; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P1083 ?capacity. }}
  OPTIONAL {{ ?item wdt:P2046 ?area. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(BOUND(?capacity) || BOUND(?area))
}}
LIMIT 500
""",
        ),
        (
            "luxury_hotel_mice",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?rooms WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q27686; wdt:P625 ?coord; wdt:P18 ?image; wdt:P8733 ?rooms.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(xsd:decimal(?rooms) >= 150)
}}
LIMIT 500
""",
        ),
        (
            "mall_mixed_use",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?area WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q11315; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P2046 ?area. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,si,ta,km,dv,pt,fr,es". }}
  FILTER(BOUND(?area) || BOUND(?article))
}}
LIMIT 600
""",
        ),
    ]


def _gap_queries(country_values: str) -> list[tuple[str, str]]:
    values = f"VALUES (?country ?countryName) {{\n  {country_values}\n}}"
    return [
        (
            "transport_hub",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel
       (COUNT(DISTINCT ?line) AS ?lineCount) WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q55488; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P81 ?line. }}
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(BOUND(?article))
}}
GROUP BY ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel
HAVING(?lineCount >= 2
  || CONTAINS(LCASE(STR(?itemLabel)), "central")
  || CONTAINS(LCASE(STR(?itemLabel)), "terminal")
  || CONTAINS(LCASE(STR(?itemLabel)), "main")
  || CONTAINS(LCASE(STR(?itemLabel)), "junction"))
ORDER BY DESC(?lineCount) ?countryName ?itemLabel
LIMIT 1200
""",
        ),
        (
            "convention_center",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?capacity ?area WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P1083 ?capacity. }}
  OPTIONAL {{ ?item wdt:P2046 ?area. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(BOUND(?article))
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
LIMIT 800
""",
        ),
        (
            "luxury_hotel_mice",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?rooms WHERE {{
  {values}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* wd:Q27686; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P8733 ?rooms. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(BOUND(?article))
  FILTER(
    CONTAINS(LCASE(STR(?itemLabel)), "hilton")
    || CONTAINS(LCASE(STR(?itemLabel)), "sheraton")
    || CONTAINS(LCASE(STR(?itemLabel)), "hyatt")
    || CONTAINS(LCASE(STR(?itemLabel)), "marriott")
    || CONTAINS(LCASE(STR(?itemLabel)), "fairmont")
    || CONTAINS(LCASE(STR(?itemLabel)), "intercontinental")
    || CONTAINS(LCASE(STR(?itemLabel)), "four seasons")
    || CONTAINS(LCASE(STR(?itemLabel)), "radisson")
    || CONTAINS(LCASE(STR(?itemLabel)), "westin")
    || CONTAINS(LCASE(STR(?itemLabel)), "pullman")
    || CONTAINS(LCASE(STR(?itemLabel)), "sofitel")
  )
}}
LIMIT 800
""",
        ),
        (
            "office_government",
            f"""
SELECT ?countryName ?item ?itemLabel ?coord ?image ?article ?cityLabel ?height ?floors WHERE {{
  {values}
  VALUES ?officeClass {{ wd:Q1021645 }}
  ?item wdt:P17 ?country; wdt:P31/wdt:P279* ?officeClass; wdt:P625 ?coord; wdt:P18 ?image.
  OPTIONAL {{ ?item wdt:P131 ?city. }}
  OPTIONAL {{ ?item wdt:P2048 ?height. }}
  OPTIONAL {{ ?item wdt:P1101 ?floors. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,pt,fr,es". }}
  FILTER(BOUND(?article))
  FILTER(BOUND(?height) || BOUND(?floors))
  FILTER(
    CONTAINS(LCASE(STR(?itemLabel)), "tower")
    || CONTAINS(LCASE(STR(?itemLabel)), "business")
    || CONTAINS(LCASE(STR(?itemLabel)), "corporate")
    || CONTAINS(LCASE(STR(?itemLabel)), "office")
    || CONTAINS(LCASE(STR(?itemLabel)), "bank")
    || CONTAINS(LCASE(STR(?itemLabel)), "banco")
    || CONTAINS(LCASE(STR(?itemLabel)), "edif")
  )
}}
LIMIT 1200
""",
        ),
    ]


def _sparql(name: str, query: str) -> list[dict]:
    cache_key = hashlib.sha256(query.encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"{name}_{cache_key}.json"
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
                time.sleep(15)
                continue
    print(f"SPARQL query failed for {name}: {last_error}")
    return []


def _drafts_from_rows(
    scene_type: str,
    rows: list[dict],
    registry: dict,
    max_per_country_scene: int,
) -> list[CandidateDraft]:
    drafts: list[CandidateDraft] = []
    per_country: Counter[str] = Counter()
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        country = _value(row, "countryName")
        if not country or country not in registry.get("countries", {}):
            continue
        if per_country[(country, scene_type)] >= max_per_country_scene:
            continue
        draft = _draft_from_row(scene_type, row, registry)
        if draft is None:
            continue
        key = (draft.country.casefold(), draft.scene_type.casefold(), draft.property_name.casefold())
        if key in seen:
            continue
        seen.add(key)
        drafts.append(draft)
        per_country[(country, scene_type)] += 1
    return drafts


def _draft_from_row(scene_type: str, row: dict, registry: dict) -> CandidateDraft | None:
    country = _value(row, "countryName")
    label = _clean_label(_value(row, "itemLabel") or "")
    image = _https(_value(row, "image"))
    source_url = _value(row, "article") or _value(row, "item")
    if not country or not label or not image or not source_url:
        return None
    latitude, longitude = _point(_value(row, "coord") or "")
    bbox = registry["countries"][country]["bbox"]
    city = _city(row, country)
    common = {
        "region": _region_for_country(country),
        "country": country,
        "city": city,
        "property_name": label,
        "latitude": latitude,
        "longitude": longitude,
        "map_source": "Wikidata coordinate statement",
        "map_source_date": TODAY,
        "source_name": "Wikidata/Wikipedia",
        "source_tier": "Tier 3",
        "source_url": source_url,
        "source_date": TODAY,
        "evidence_type": "Direct",
        "bbox": bbox,
        "source_type": SOURCE_TYPE,
        "hero_image": _hero(label, source_url, image),
    }
    if scene_type == "airport_terminal":
        iata = _value(row, "iata")
        icao = _value(row, "icao")
        codes = ", ".join(code for code in [iata, icao] if code)
        return CandidateDraft(
            **common,
            scene_type=scene_type,
            annual_visits=None,
            geocode_precision="airport terminal centroid",
            field_group="terminal_role",
            indicator_name="terminal_role",
            field_value=f"Public airport entity with terminal/gateway role; codes: {codes or 'not listed'}",
            content_text=f"{label} is a public airport entity in Wikidata/Wikipedia with coordinate and image evidence.",
        )
    if scene_type == "transport_hub":
        line_count = int(float(_value(row, "lineCount") or "0"))
        name = label
        if all(token not in name.casefold() for token in ("station", "metro", "terminal")):
            name = f"{name} Station"
        return CandidateDraft(
            **{**common, "property_name": name},
            scene_type=scene_type,
            annual_visits=None,
            geocode_precision="rail or metro station centroid",
            field_group="line_count",
            indicator_name="line_count",
            field_value=f"{line_count} rail/metro lines connected per Wikidata P81 statements",
            content_text=f"{name} has {line_count} public rail/metro line links in Wikidata.",
        )
    if scene_type == "stadium":
        capacity = int(float(_value(row, "capacity") or "0"))
        return CandidateDraft(
            **common,
            scene_type=scene_type,
            annual_visits=None,
            geocode_precision="stadium centroid",
            field_group="seat_count",
            indicator_name="seat_count",
            field_value=f"{capacity:,} seats/capacity per Wikidata P1083 statement",
            content_text=f"{label} has public capacity evidence of {capacity:,}.",
        )
    if scene_type == "office_government":
        if not OFFICE_ALLOW.search(label) or OFFICE_BLOCK.search(label):
            return None
        floors = _value(row, "floors")
        height = _value(row, "height")
        if floors:
            metric = "floor_count"
            field_value = f"{int(float(floors))} floors per Wikidata P1101 statement"
        elif height:
            metric = "tower_height"
            field_value = f"{float(height):g} m height per Wikidata P2048 statement"
        else:
            return None
        return CandidateDraft(
            **common,
            scene_type=scene_type,
            annual_visits=None,
            geocode_precision="office tower centroid",
            field_group=metric,
            indicator_name=metric,
            field_value=field_value,
            content_text=f"{label} has public building scale evidence: {field_value}.",
        )
    if scene_type == "convention_center":
        if not CONVENTION_ALLOW.search(label) or CONVENTION_BLOCK.search(label):
            return None
        capacity = _value(row, "capacity")
        area = _value(row, "area")
        if area:
            metric = "exhibition_area"
            field_value = f"{float(area):g} square meters area per Wikidata P2046 statement"
        elif capacity:
            metric = "peak_event_capacity"
            field_value = f"{int(float(capacity)):,} event capacity per Wikidata P1083 statement"
        else:
            return None
        return CandidateDraft(
            **common,
            scene_type=scene_type,
            annual_visits=None,
            geocode_precision="exhibition venue centroid",
            field_group=metric,
            indicator_name=metric,
            field_value=field_value,
            content_text=f"{label} has public convention/event scale evidence: {field_value}.",
        )
    if scene_type == "luxury_hotel_mice":
        if not HOTEL_ALLOW.search(label):
            return None
        rooms = int(float(_value(row, "rooms") or "0"))
        if rooms > 0:
            field_group = "keys"
            indicator_name = "keys"
            field_value = f"{rooms:,} rooms/keys per Wikidata P8733 statement"
            content_text = f"{label} has public hotel room count evidence of {rooms:,}."
        else:
            field_group = "brand"
            indicator_name = "brand"
            field_value = f"{label} is a public hotel entity matching an international luxury/upscale brand keyword"
            content_text = f"{label} has public hotel brand/entity evidence with coordinate and image metadata."
        return CandidateDraft(
            **common,
            scene_type=scene_type,
            annual_visits=None,
            geocode_precision="hotel venue centroid",
            field_group=field_group,
            indicator_name=indicator_name,
            field_value=field_value,
            content_text=content_text,
        )
    if scene_type == "mall_mixed_use":
        area = _value(row, "area")
        if area:
            area_value = float(area)
            field_group = "gla"
            indicator_name = "gla"
            field_value = f"{area_value:g} square meters area per Wikidata P2046 statement"
            content_text = f"{label} has public mall area evidence: {field_value}."
        else:
            field_group = "flagship_position"
            indicator_name = "flagship_position"
            field_value = f"{label} is a public shopping mall entity with coordinate, image and article evidence"
            content_text = f"{label} has public mall entity evidence with coordinate and image metadata; quantify GLA or footfall next."
        return CandidateDraft(
            **common,
            scene_type=scene_type,
            annual_visits=None,
            geocode_precision="shopping mall centroid",
            field_group=field_group,
            indicator_name=indicator_name,
            field_value=field_value,
            content_text=content_text,
        )
    return None


def _filter_candidates(
    planned: list[CandidateDraft],
    *,
    known_index,
    max_per_country_scene: int,
) -> tuple[list[CandidateDraft], list[dict], list[dict]]:
    accepted: list[CandidateDraft] = []
    invalid: list[dict] = []
    skipped: list[dict] = []
    per_country_scene: Counter[tuple[str, str]] = Counter()
    for draft in planned:
        if per_country_scene[(draft.country, draft.scene_type)] >= max_per_country_scene:
            continue
        validation = validate_candidate_draft(draft)
        match = known_index.match(
            country=draft.country,
            city=draft.city,
            property_name=draft.property_name,
            scene_type=draft.scene_type,
            latitude=draft.latitude,
            longitude=draft.longitude,
            source_url=draft.source_url,
        )
        if match.status != NEW_OPPORTUNITY:
            skipped.append(
                {
                    "country": draft.country,
                    "property_name": draft.property_name,
                    "scene_type": draft.scene_type,
                    "match_status": match.status,
                    "reason": match.reason,
                }
            )
        elif not validation.accepted:
            invalid.append(
                {
                    "country": draft.country,
                    "property_name": draft.property_name,
                    "scene_type": draft.scene_type,
                    "issues": validation.issues,
                }
            )
        else:
            accepted.append(draft)
            per_country_scene[(draft.country, draft.scene_type)] += 1
    return accepted, invalid, skipped


def _summary(
    *,
    countries: list[str],
    query_stats: list[dict[str, object]],
    planned: list[CandidateDraft],
    accepted: list[CandidateDraft],
    invalid: list[dict],
    skipped: list[dict],
    raw_ids: list[str],
    written_or_changed: int,
    curation,
    sync,
) -> dict:
    return {
        "mode": "web_first_structured_landmark_expansion",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "target_country_count": len(countries),
        "target_countries": countries,
        "query_stats": query_stats,
        "firecrawl_search_count": 0,
        "firecrawl_fetch_count": 0,
        "firecrawl_credits_used": 0,
        "planned_candidates": len(planned),
        "accepted_for_curation_count": len(accepted),
        "raw_evidence_written_or_changed": written_or_changed,
        "skipped_known_or_duplicate_count": len(skipped),
        "invalid_before_curation_count": len(invalid),
        "planned_by_scene": dict(Counter(draft.scene_type for draft in planned)),
        "accepted_by_scene": dict(Counter(draft.scene_type for draft in accepted)),
        "accepted_by_country": dict(Counter(draft.country for draft in accepted)),
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
        "raw_evidence_ids": raw_ids,
        "sample_skipped_known_or_duplicate": skipped[:30],
        "sample_invalid_before_curation": invalid[:30],
    }


def _active_db_summary() -> dict:
    connection = sqlite3.connect("outputs/isite2_dev.db")
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
            "select count(*) from properties where hero_image is null or hero_image='null'"
        ),
        "non_ready_scan_candidates": one(
            "select count(*) from scan_candidates where candidate_quality_status!='ready'"
        ),
        "duplicate_identity_groups": one(
            "select count(*) from (select country, scene_type, property_identity_key, count(*) n "
            "from properties group by country, scene_type, property_identity_key having n>1)"
        ),
    }
    connection.close()
    return summary


def _render_report(summary: dict) -> str:
    country_lines = "\n".join(
        f"- {item['country']}: {item['n']}"
        for item in summary["active_db"]["country_counts"][:40]
    )
    scene_lines = "\n".join(
        f"- {item['scene_type']}: {item['n']}"
        for item in summary["active_db"]["scene_counts"]
    )
    return (
        "# Web-first Structured Landmark Expansion\n\n"
        f"- Target countries: {summary['target_country_count']}\n"
        "- Firecrawl: 0 search / 0 fetch / 0 credits\n"
        f"- Planned candidates: {summary['planned_candidates']}\n"
        f"- Accepted for curation: {summary['accepted_for_curation_count']}\n"
        f"- Curation accepted: {(summary['curation'] or {}).get('accepted_count', 0)}\n"
        f"- Curation rejected: {(summary['curation'] or {}).get('rejected_count', 0)}\n"
        f"- Active total properties: {summary['active_db']['total_properties']}\n"
        f"- Missing hero images: {summary['active_db']['missing_hero']}\n"
        f"- Non-ready scan candidates: {summary['active_db']['non_ready_scan_candidates']}\n"
        f"- Duplicate identity groups: {summary['active_db']['duplicate_identity_groups']}\n\n"
        "## Active Countries\n"
        f"{country_lines}\n\n"
        "## Active Scenes\n"
        f"{scene_lines}\n"
    )


def _value(row: dict, key: str) -> str | None:
    cell = row.get(key)
    return cell.get("value") if cell else None


def _point(text: str) -> tuple[float, float]:
    match = re.match(r"Point\(([-0-9.]+) ([-0-9.]+)\)", text)
    if not match:
        raise ValueError(f"bad coordinate point: {text}")
    return float(match.group(2)), float(match.group(1))


def _https(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", label).strip()


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
    city = _value(row, "cityLabel") or ""
    if city and country not in {"Brazil"}:
        if _is_brazil_restricted_city(city):
            return ""
        if _is_non_city_admin_label(city):
            return ""
        return city
    article = _value(row, "article") or ""
    label = _value(row, "itemLabel") or ""
    text = f"{city} {article} {label}"
    known_city_tokens = [
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
        "Malabo",
        "Bata",
        "Bissau",
        "Mbabane",
        "Manzini",
        "Harare",
        "Bulawayo",
        "Saint-Denis",
        "Mogadishu",
        "Hargeisa",
        "Bratislava",
        "Košice",
        "Chișinău",
        "Nicosia",
        "Limassol",
        "Tirana",
        "Skopje",
        "Sofia",
        "Plovdiv",
        "Zagreb",
        "Split",
        "Ljubljana",
        "Sarajevo",
        "Banja Luka",
        "Belgrade",
        "Novi Sad",
        "Podgorica",
        "Managua",
        "Caracas",
        "Maracaibo",
        "Port-au-Prince",
        "Montevideo",
        "Port Moresby",
        "Lae",
        "Honiara",
        "Suva",
        "Nadi",
        "Kathmandu",
        "Pokhara",
        "Vientiane",
        "Luang Prabang",
        "Bandar Seri Begawan",
        "Kabul",
        "Herat",
        "Sana'a",
        "Aden",
        "Beirut",
        "Manama",
        "Tbilisi",
        "Batumi",
        "Baku",
        "Ganja",
        "Bishkek",
        "Osh",
        "Ulaanbaatar",
        "Dushanbe",
        "Khujand",
        "Ashgabat",
        "Yerevan",
        "Gyumri",
    ]
    for token in known_city_tokens:
        if country != "Brazil" and _is_brazil_restricted_city(token):
            continue
        if token in text or token.replace(" ", "_") in article:
            return token
    if city.endswith(" District") or city in {
        "Central Zone of São Paulo",
        "Centro",
        "Barra da Tijuca",
        "Ipanema District",
    }:
        if country != "Brazil":
            return ""
        return "Rio de Janeiro" if "Rio" in text or city in {"Centro", "Barra da Tijuca", "Ipanema District"} else "São Paulo"
    return city


_ADMIN_CITY_LEVEL_TOKENS = {
    "administrative",
    "area",
    "canton",
    "county",
    "department",
    "district",
    "governorate",
    "municipality",
    "oblast",
    "province",
    "region",
    "raion",
    "state",
}


def _is_non_city_admin_label(label: str) -> bool:
    words = set(_clean_label(label).casefold().split())
    return bool(words & _ADMIN_CITY_LEVEL_TOKENS)


def _region_for_country(country: str) -> str:
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return "Unknown"


def _hero(label: str, source_url: str, image_url: str) -> dict[str, str]:
    return {
        "url": _https(image_url) or image_url,
        "alt_text": f"{label} public image",
        "source_url": source_url,
        "source_name": "Wikimedia Commons",
        "source_date": TODAY,
        "license": "Wikimedia Commons public file metadata",
    }


if __name__ == "__main__":
    main()
