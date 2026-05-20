from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import CandidateDraft, load_effective_source_registry
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.regional_targets import REGION_COUNTRIES
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository
from isite2.rules.config_loader import scene_definitions

DB_PATH = Path("outputs/isite2_dev.db")
DB_URL = f"sqlite+pysqlite:///{DB_PATH}"
OUTPUT_DIR = Path("outputs") / "qa"
SOURCE_TYPE = "existing_scene_metric_enrichment"
TODAY = "2026-05-12"

QUANTITATIVE_PRIMARY = {
    "airport_terminal": {"annual_passenger_throughput", "international_passenger_share"},
    "convention_center": {
        "exhibition_area",
        "meeting_area",
        "annual_events",
        "international_event_frequency",
        "peak_event_capacity",
    },
    "stadium": {"seat_count", "event_days", "international_events"},
    "luxury_hotel_mice": {"keys", "meeting_ballroom_area", "ballroom_capacity"},
    "mall_mixed_use": {"gla", "annual_footfall"},
    "office_government": {"office_nla", "office_gfa", "hq_density"},
    "hospital": {"beds", "outpatient_volume"},
    "university": {"enrollment"},
    "transport_hub": {"daily_ridership", "interchange_volume", "line_count"},
    "cruise_port": {"passenger_throughput", "international_cruise_frequency"},
}


@dataclass(frozen=True)
class MetricEnrichment:
    country: str
    property_name: str
    scene_type: str
    field_group: str
    indicator_name: str
    field_value: str
    source_name: str
    source_tier: str
    source_url: str
    source_date: str
    content_text: str
    annual_visits: float | None = None


ENRICHMENTS = [
    MetricEnrichment(
        country="Argentina",
        property_name="BBVA Tower",
        scene_type="office_government",
        field_group="office_gfa",
        indicator_name="office_gfa",
        field_value="Arquitectonica project page lists size as 645,000 sf / 60,000 m2.",
        source_name="Arquitectonica project page",
        source_tier="Tier 2",
        source_url="https://arquitectonica.com/architecture/project/bbva-tower/",
        source_date=TODAY,
        content_text=(
            "Arquitectonica describes BBVA Tower in Buenos Aires as an office/financial "
            "project and lists the project size as 645,000 sf / 60,000 m2."
        ),
        annual_visits=600_000.0,
    ),
    MetricEnrichment(
        country="Brazil",
        property_name="Eldorado Business Tower",
        scene_type="office_government",
        field_group="office_gfa",
        indicator_name="office_gfa",
        field_value="Aflalo/Gasperini project page lists building area as 107,240 m2.",
        source_name="Aflalo/Gasperini architects project page",
        source_tier="Tier 2",
        source_url="https://aflalogasperini.com.br/en/eldorado-business-tower/",
        source_date=TODAY,
        content_text=(
            "Aflalo/Gasperini's Eldorado Business Tower project page lists land area "
            "of 10,000 m2 and building area of 107,240 m2."
        ),
        annual_visits=1_072_400.0,
    ),
    MetricEnrichment(
        country="Brazil",
        property_name="Torre Norte Office Tower",
        scene_type="office_government",
        field_group="office_gfa",
        indicator_name="office_gfa",
        field_value="Cushman & Wakefield listing reports building size of 61,809 SM.",
        source_name="Cushman & Wakefield property listing",
        source_tier="Tier 2",
        source_url=(
            "https://www.cushmanwakefield.com/en/brazil/properties/for-lease/office/sp/"
            "sao-paulo/av-das-nacoes-unidas-12901/s117366667-227898-l"
        ),
        source_date=TODAY,
        content_text=(
            "Cushman & Wakefield's Centro Empresarial Nacoes Unidas Torre Norte listing "
            "reports a building size of 61,809 SM."
        ),
        annual_visits=618_090.0,
    ),
    MetricEnrichment(
        country="Chile",
        property_name="Gran Torre Costanera",
        scene_type="office_government",
        field_group="office_gfa",
        indicator_name="office_gfa",
        field_value="The Skyscraper Center lists Tower GFA as 110,000 m2.",
        source_name="The Skyscraper Center",
        source_tier="Tier 2",
        source_url="https://www.skyscrapercenter.com/building/torre-costanera/521",
        source_date=TODAY,
        content_text=(
            "The Skyscraper Center's Torre Costanera profile lists Tower GFA as "
            "110,000 m2."
        ),
        annual_visits=1_100_000.0,
    ),
    MetricEnrichment(
        country="Mexico",
        property_name="Cancún International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="2025 year-to-date passenger traffic: 29,345,538 passengers for CUN/Cancun.",
        source_name="ASUR passenger traffic release",
        source_tier="Tier 1",
        source_url=(
            "https://www.asur.com.mx/media/Comunicados%20de%20prensa/"
            "Comunicados%20a%20Bolsa/2025/12/"
            "ASUR-Airport-Cancun-Mexico-Passenger-Traffic-Dec-25.pdf"
        ),
        source_date="2026-01-06",
        content_text=(
            "ASUR December 2025 passenger traffic release lists CUN/Cancun "
            "year-to-date total traffic of 29,345,538 passengers for 2025."
        ),
        annual_visits=29_345_538.0,
    ),
    MetricEnrichment(
        country="Mexico",
        property_name="Cozumel International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="2025 year-to-date passenger traffic: 646,606 passengers for CZM/Cozumel.",
        source_name="ASUR passenger traffic release",
        source_tier="Tier 1",
        source_url=(
            "https://www.asur.com.mx/media/Comunicados%20de%20prensa/"
            "Comunicados%20a%20Bolsa/2025/12/"
            "ASUR-Airport-Cancun-Mexico-Passenger-Traffic-Dec-25.pdf"
        ),
        source_date="2026-01-06",
        content_text=(
            "ASUR December 2025 passenger traffic release lists CZM/Cozumel "
            "year-to-date total traffic of 646,606 passengers for 2025."
        ),
        annual_visits=646_606.0,
    ),
    MetricEnrichment(
        country="Mexico",
        property_name="Manuel Crescencio Rejón International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="2025 year-to-date passenger traffic: 3,939,692 passengers for MID/Merida.",
        source_name="ASUR passenger traffic release",
        source_tier="Tier 1",
        source_url=(
            "https://www.asur.com.mx/media/Comunicados%20de%20prensa/"
            "Comunicados%20a%20Bolsa/2025/12/"
            "ASUR-Airport-Cancun-Mexico-Passenger-Traffic-Dec-25.pdf"
        ),
        source_date="2026-01-06",
        content_text=(
            "ASUR December 2025 passenger traffic release lists MID/Merida "
            "year-to-date total traffic of 3,939,692 passengers for 2025."
        ),
        annual_visits=3_939_692.0,
    ),
    MetricEnrichment(
        country="Mexico",
        property_name="Xoxocotlán International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="2025 year-to-date passenger traffic: 1,864,967 passengers for OAX/Oaxaca.",
        source_name="ASUR passenger traffic release",
        source_tier="Tier 1",
        source_url=(
            "https://www.asur.com.mx/media/Comunicados%20de%20prensa/"
            "Comunicados%20a%20Bolsa/2025/12/"
            "ASUR-Airport-Cancun-Mexico-Passenger-Traffic-Dec-25.pdf"
        ),
        source_date="2026-01-06",
        content_text=(
            "ASUR December 2025 passenger traffic release lists OAX/Oaxaca "
            "year-to-date total traffic of 1,864,967 passengers for 2025."
        ),
        annual_visits=1_864_967.0,
    ),
    MetricEnrichment(
        country="Brazil",
        property_name="São Paulo/Guarulhos International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="2025 annual passenger traffic: 47.2 million passengers.",
        source_name="CAPA Centre for Aviation",
        source_tier="Tier 2",
        source_url=(
            "https://centreforaviation.com/news/"
            "gru-airport-sao-paulo-guarulhos-intl-airport-handles-42m-pax-"
            "in-dec-2025-472m-pax-in-2025-1345280"
        ),
        source_date="2026-01-19",
        content_text=(
            "CAPA public news headline reports GRU Airport handled 4.2 million "
            "passengers in Dec-2025 and 47.2 million passengers in 2025."
        ),
        annual_visits=47_200_000.0,
    ),
    MetricEnrichment(
        country="Brazil",
        property_name="São Paulo/Guarulhos International Airport",
        scene_type="airport_terminal",
        field_group="monthly_passenger_throughput",
        indicator_name="monthly_passenger_throughput",
        field_value=(
            "GRU official operational table provides monthly passenger totals; "
            "July 2017 total passengers: 3,499,000."
        ),
        source_name="GRU Airport operational information",
        source_tier="Tier 1",
        source_url="https://www.gru.com.br/en/institutional/sobre-gru-airport/operational-information",
        source_date=TODAY,
        content_text=(
            "GRU Airport official operational information table lists July 2017 "
            "domestic, international and total passenger counts, including "
            "3,499,000 total passengers."
        ),
    ),
    MetricEnrichment(
        country="Brazil",
        property_name="São Paulo Expo",
        scene_type="convention_center",
        field_group="exhibition_area",
        indicator_name="exhibition_area",
        field_value="Total exhibition space: 100,000 sqm; indoor surface area: 90,000 sqm.",
        source_name="GL events São Paulo Expo venue page",
        source_tier="Tier 1",
        source_url="https://www.gl-events.com/en/sao-paulo-expo",
        source_date=TODAY,
        content_text=(
            "GL events venue page states São Paulo Expo has 90,000 sqm of indoor "
            "surface area and total exhibition space of 100,000 sqm."
        ),
        annual_visits=1_000_000.0,
    ),
    MetricEnrichment(
        country="Brazil",
        property_name="São Paulo Expo",
        scene_type="convention_center",
        field_group="peak_event_capacity",
        indicator_name="peak_event_capacity",
        field_value="Auditorium/event layout capacity up to 7,810 people.",
        source_name="GL events São Paulo Expo venue page",
        source_tier="Tier 1",
        source_url="https://www.gl-events.com/en/sao-paulo-expo",
        source_date=TODAY,
        content_text=(
            "GL events venue page lists auditorium capacity from 70 to 7,810 "
            "seats and event layout capacity up to 7,810 people."
        ),
        annual_visits=1_000_000.0,
    ),
    MetricEnrichment(
        country="South Africa",
        property_name="Moses Mabhida Stadium",
        scene_type="stadium",
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Post-2010 scaled stadium capacity: 56,000 seats; World Cup capacity: 70,000 seats.",
        source_name="AECOM Moses Mabhida Stadium project page",
        source_tier="Tier 2",
        source_url="https://aecom.com/za/projects/moses-mabhida-stadium/",
        source_date=TODAY,
        content_text=(
            "AECOM project page states Moses Mabhida Stadium had 70,000 seats for "
            "the World Cup and was scaled down to 56,000 seats post-2010."
        ),
        annual_visits=840_000.0,
    ),
    MetricEnrichment(
        country="Argentina",
        property_name="Mas Monumental Stadium",
        scene_type="stadium",
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Buenos Aires official tourism page lists stadium capacity as 76,000.",
        source_name="Buenos Aires tourism River Plate Stadium page",
        source_tier="Tier 2",
        source_url="https://turismo.buenosaires.gob.ar/en/atractivo/river-plate-stadium",
        source_date=TODAY,
        content_text=(
            "Buenos Aires official tourism page states River Plate Stadium has a "
            "capacity of 76,000 and is the biggest stadium in Argentina."
        ),
        annual_visits=1_140_000.0,
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="QA scene evidence metrics and enrich existing iSite2 properties."
    )
    parser.add_argument("--qa-only", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    enrichments = [*ENRICHMENTS, *_brazil_renai_airport_primary_normalizations()]
    before = metric_qa_summary()
    written = []
    curation = None
    sync = None

    if not args.qa_only:
        store = EvidenceCurationStore(database_url=DB_URL)
        registry = load_effective_source_registry()
        for enrichment in enrichments:
            draft = draft_for_enrichment(enrichment, registry)
            result = store.upsert_candidate_evidence(draft, source_type=SOURCE_TYPE)
            written.append(
                {
                    "property_name": enrichment.property_name,
                    "country": enrichment.country,
                    "scene_type": enrichment.scene_type,
                    "field_group": enrichment.field_group,
                    "source_name": enrichment.source_name,
                    "raw_evidence_id": result.raw_evidence_id,
                    "is_new_evidence": result.is_new_evidence,
                    "is_changed_evidence": result.is_changed_evidence,
                    "duplicate_unchanged": result.duplicate_unchanged,
                    "status": result.status,
                }
            )
        curation = run_pending_evidence_curation(store=store, output_dir=Path("outputs") / "regional_scan_loop")
        if not args.skip_sync:
            repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
            sync = sync_overlay_to_active_repository(repository)

    after = metric_qa_summary()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary = {
        "mode": "existing_scene_metric_enrichment",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "source_type": SOURCE_TYPE,
        "qa_before": before,
        "qa_after": after,
        "enrichment_count": len(enrichments),
        "raw_evidence_written": written,
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
    }
    json_path = OUTPUT_DIR / f"existing_scene_metric_enrichment_{timestamp}.json"
    report_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(summary), encoding="utf-8")
    print(json.dumps({**summary, "summary_path": str(json_path), "report_path": str(report_path)}, ensure_ascii=False, indent=2))


def metric_qa_summary() -> dict:
    scenes = scene_definitions()
    primary = {scene: set(config.get("primary_indicators", [])) for scene, config in scenes.items()}
    quantitative_primary = QUANTITATIVE_PRIMARY
    rows = _connection().execute(
        """
        SELECT p.id AS property_id, p.country, p.city, p.canonical_name, p.scene_type,
               e.field_group, e.indicator_name, e.field_value, e.source_url, e.source_name
        FROM properties p
        JOIN scan_candidates sc ON sc.property_id = p.id
        LEFT JOIN evidence_items e
          ON e.property_id = p.id AND e.scan_run_id = sc.scan_run_id
        ORDER BY p.country, p.scene_type, p.canonical_name
        """
    ).fetchall()
    by_property: dict[str, list[sqlite3.Row]] = defaultdict(list)
    properties: dict[str, sqlite3.Row] = {}
    for row in rows:
        by_property[row["property_id"]].append(row)
        properties[row["property_id"]] = row

    scene_totals = Counter(row["scene_type"] for row in properties.values())
    scene_primary = Counter()
    scene_quant_primary = Counter()
    scene_multi_source = Counter()
    scene_domains: dict[str, Counter[str]] = defaultdict(Counter)
    weak_samples: dict[str, list[dict]] = defaultdict(list)

    for property_id, evidence_rows in by_property.items():
        prop = properties[property_id]
        scene_type = prop["scene_type"]
        domains = set()
        has_primary = False
        has_quant_primary = False
        field_groups = set()
        for evidence in evidence_rows:
            field_group = evidence["field_group"]
            indicator_name = evidence["indicator_name"]
            field_groups.update(value for value in [field_group, indicator_name] if value)
            tokens = {field_group, indicator_name} - {None, ""}
            if tokens & primary.get(scene_type, set()):
                has_primary = True
            if tokens & quantitative_primary.get(scene_type, set()) and _has_digit(evidence["field_value"]):
                has_quant_primary = True
            domain = _domain(evidence["source_url"])
            if domain:
                domains.add(domain)
                scene_domains[scene_type][domain] += 1
        if has_primary:
            scene_primary[scene_type] += 1
        if has_quant_primary:
            scene_quant_primary[scene_type] += 1
        if len(domains) >= 2:
            scene_multi_source[scene_type] += 1
        if (not has_quant_primary or len(domains) < 2) and len(weak_samples[scene_type]) < 12:
            weak_samples[scene_type].append(
                {
                    "country": prop["country"],
                    "city": prop["city"],
                    "property_name": prop["canonical_name"],
                    "has_quantitative_primary": has_quant_primary,
                    "source_domains": sorted(domains),
                    "field_groups": sorted(field_groups),
                }
            )

    return {
        "total_properties": len(properties),
        "scene_summary": {
            scene: {
                "total": scene_totals[scene],
                "primary_indicator_properties": scene_primary[scene],
                "quantitative_primary_properties": scene_quant_primary[scene],
                "multi_source_properties": scene_multi_source[scene],
                "top_source_domains": scene_domains[scene].most_common(5),
            }
            for scene in sorted(scene_totals)
        },
        "weak_sample_by_scene": dict(weak_samples),
    }


def _brazil_renai_airport_primary_normalizations() -> list[MetricEnrichment]:
    rows = _connection().execute(
        """
        SELECT p.canonical_name, e.field_value, e.source_name, e.source_tier,
               e.source_url, e.source_date
        FROM properties p
        JOIN scan_candidates sc ON sc.property_id = p.id
        JOIN evidence_items e
          ON e.property_id = p.id AND e.scan_run_id = sc.scan_run_id
        WHERE p.country = 'Brazil'
          AND p.scene_type = 'airport_terminal'
          AND e.field_group = 'passenger_throughput'
          AND e.indicator_name = 'passenger_throughput'
          AND e.source_name = 'Brazil RENAI / Sistema Horus airport movement table'
          AND e.field_value LIKE 'Passenger movement in Brazil airport ranking:%'
        ORDER BY p.canonical_name
        """
    ).fetchall()
    enrichments = []
    for row in rows:
        visitors = _first_number(row["field_value"])
        enrichments.append(
            MetricEnrichment(
                country="Brazil",
                property_name=row["canonical_name"],
                scene_type="airport_terminal",
                field_group="annual_passenger_throughput",
                indicator_name="annual_passenger_throughput",
                field_value=row["field_value"].replace(
                    "Passenger movement in Brazil airport ranking",
                    "Annual passenger throughput from Brazil airport ranking",
                ),
                source_name=row["source_name"],
                source_tier=row["source_tier"],
                source_url=row["source_url"],
                source_date=row["source_date"] or "",
                content_text=(
                    "Existing Brazil RENAI / Sistema Horus airport ranking evidence "
                    "contains a property-level passenger movement value and is "
                    "normalized to the airport_terminal primary indicator."
                ),
                annual_visits=float(visitors) if visitors is not None else None,
            )
        )
    return enrichments


def draft_for_enrichment(enrichment: MetricEnrichment, registry: dict) -> CandidateDraft:
    row = _find_property(enrichment)
    country_registry = registry["countries"][enrichment.country]
    return CandidateDraft(
        region=_region_for_country(enrichment.country),
        country=enrichment.country,
        city=row["city"],
        property_name=row["canonical_name"],
        scene_type=row["scene_type"],
        annual_visits=enrichment.annual_visits,
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        geocode_precision=row["geocode_precision"],
        map_source=row["map_source"] or "",
        map_source_date=row["map_source_date"] or "",
        field_group=enrichment.field_group,
        indicator_name=enrichment.indicator_name,
        field_value=enrichment.field_value,
        source_name=enrichment.source_name,
        source_tier=enrichment.source_tier,
        source_url=enrichment.source_url,
        source_date=enrichment.source_date,
        evidence_type="Direct",
        bbox=country_registry["bbox"],
        source_type=SOURCE_TYPE,
        content_text=enrichment.content_text,
        hero_image=json.loads(row["hero_image"]) if row["hero_image"] else None,
    )


def _find_property(enrichment: MetricEnrichment) -> sqlite3.Row:
    row = _connection().execute(
        """
        SELECT p.*, sm.annual_visits_est
        FROM properties p
        JOIN scan_candidates sc ON sc.property_id = p.id
        LEFT JOIN scene_model_results sm
          ON sm.property_id = p.id AND sm.scan_run_id = sc.scan_run_id
        WHERE p.country = ?
          AND p.scene_type = ?
          AND p.canonical_name = ?
        ORDER BY sc.created_at DESC
        LIMIT 1
        """,
        (enrichment.country, enrichment.scene_type, enrichment.property_name),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"property not found: {enrichment.country} / {enrichment.scene_type} / "
            f"{enrichment.property_name}"
        )
    return row


def render_report(summary: dict) -> str:
    after = summary["qa_after"]["scene_summary"]
    scene_lines = "\n".join(
        "- {scene}: total={total}, primary={primary}, quantitative_primary={quant}, "
        "multi_source={multi}, top_domains={domains}".format(
            scene=scene,
            total=stats["total"],
            primary=stats["primary_indicator_properties"],
            quant=stats["quantitative_primary_properties"],
            multi=stats["multi_source_properties"],
            domains=", ".join(f"{domain}:{count}" for domain, count in stats["top_source_domains"][:3]),
        )
        for scene, stats in after.items()
    )
    written_lines = "\n".join(
        f"- {item['country']} / {item['property_name']} / {item['field_group']} / "
        f"{item['source_name']} / new={item['is_new_evidence']} changed={item['is_changed_evidence']}"
        for item in summary["raw_evidence_written"]
    )
    curation = summary["curation"] or {}
    sync = summary["overlay_sync"] or {}
    return (
        "# Existing Scene Metric Enrichment\n\n"
        f"- Enrichment items: {summary['enrichment_count']}\n"
        f"- Curation accepted new: {curation.get('accepted_count', 0)}\n"
        f"- Curation updated existing: {curation.get('updated_count', 0)}\n"
        f"- Curation rejected: {curation.get('rejected_count', 0)}\n"
        f"- Active sync candidates: {sync.get('candidate_count', 'n/a')}\n"
        f"- Active sync blocked: {sync.get('blocked_candidate_count', 'n/a')}\n\n"
        "## Scene QA After\n"
        f"{scene_lines}\n\n"
        "## Written Evidence\n"
        f"{written_lines}\n"
    )


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    domain = urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


def _has_digit(value: str | None) -> bool:
    return any(char.isdigit() for char in str(value or ""))


def _first_number(value: str | None) -> int | None:
    match = re.search(r"([0-9][0-9,]*)", str(value or ""))
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _region_for_country(country: str) -> str:
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return "Unknown"


if __name__ == "__main__":
    main()
