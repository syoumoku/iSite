from __future__ import annotations

# Legacy seed snapshot retained for reproducibility of the Sri Lanka APAC pass.
# Do not extend this file for new countries or rounds; use reusable import,
# structured-growth, and quality-gate scripts with external input/config instead.

import argparse
import json
from collections import Counter
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
from isite2.growth.property_identity import NEW_OPPORTUNITY, known_opportunity_index_from_registry
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_URL = "sqlite+pysqlite:///outputs/isite2_dev.db"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
SOURCE_TYPE = "apac_sri_lanka_other_scene_seed"
SOURCE_DATE = "2026-05-14"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Sri Lanka non-hotel APAC candidates with hard scene metrics and real images."
    )
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    registry = load_effective_source_registry()
    store = EvidenceCurationStore(database_url=DB_URL)
    # The active APAC surface is overlay-synced, so registry+overlay identity is
    # enough for this small seed pass and avoids loading every historical
    # evidence URL from SQLite.
    known_index = known_opportunity_index_from_registry(registry)

    accepted: list[CandidateDraft] = []
    rejected: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for draft in _drafts(registry):
        validation = validate_candidate_draft(draft)
        if not validation.accepted:
            skipped["validation_failed"] += 1
            rejected.append(_rejected(draft, validation.issues))
            continue
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
            skipped[f"identity_{match.status}"] += 1
            rejected.append(_rejected(draft, [match.reason or match.status]))
            continue
        accepted.append(draft)

    raw_ids: list[str] = []
    write_stats = Counter()
    curation = None
    sync = None
    if accepted and not args.dry_run:
        for draft in accepted:
            result = store.upsert_candidate_evidence(draft, source_type=SOURCE_TYPE)
            raw_ids.append(result.raw_evidence_id)
            if result.duplicate_unchanged:
                write_stats["duplicate_unchanged"] += 1
            elif result.is_changed_evidence:
                write_stats["changed_evidence"] += 1
            elif result.is_new_evidence:
                write_stats["new_evidence"] += 1
        curation = run_pending_evidence_curation(store=store, output_dir=OUTPUT_DIR)
        if not args.skip_sync:
            repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
            sync = sync_overlay_to_active_repository(repository)

    summary = {
        "mode": SOURCE_TYPE,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "planned_count": len(_drafts(registry)),
        "accepted_count": len(accepted),
        "accepted_by_scene": dict(Counter(draft.scene_type for draft in accepted)),
        "skipped": dict(skipped),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "raw_evidence_ids": raw_ids,
        "write_stats": dict(write_stats),
        "curation": _curation_summary(curation),
        "overlay_sync": _sync_summary(sync),
        "accepted_candidates": [
            {
                "property_name": draft.property_name,
                "scene_type": draft.scene_type,
                "indicator_name": draft.indicator_name,
                "field_value": draft.field_value,
                "source_url": draft.source_url,
                "hero_source": (draft.hero_image or {}).get("source_name"),
            }
            for draft in accepted
        ],
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = OUTPUT_DIR / f"apac_sri_lanka_other_scene_seed_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _drafts(registry: dict[str, Any]) -> list[CandidateDraft]:
    bbox = registry["countries"]["Sri Lanka"]["bbox"]
    return [
        _draft(
            bbox,
            city="Kandy",
            property_name="Pallekele International Cricket Stadium",
            scene_type="stadium",
            annual_visits=525_000,
            latitude=7.2807,
            longitude=80.7227,
            geocode_precision="stadium centroid",
            field_group="seat_count",
            indicator_name="seat_count",
            field_value="Stadium capacity: 35,000 spectators",
            source_name="Wikipedia",
            source_tier="Tier 3",
            source_url="https://en.wikipedia.org/wiki/Pallekele_International_Cricket_Stadium",
            hero_url="https://upload.wikimedia.org/wikipedia/commons/3/38/Pallekele_International_Cricket_Stadium_Main_pavilion.jpg",
            hero_source_name="Wikimedia Commons",
            hero_source_url="https://en.wikipedia.org/wiki/Pallekele_International_Cricket_Stadium",
        ),
        _draft(
            bbox,
            city="Hambantota",
            property_name="Mahinda Rajapaksa International Cricket Stadium",
            scene_type="stadium",
            annual_visits=525_000,
            latitude=6.2233,
            longitude=81.3069,
            geocode_precision="stadium centroid",
            field_group="seat_count",
            indicator_name="seat_count",
            field_value="Stadium capacity: 35,000 people",
            source_name="Wikipedia / ESPNcricinfo",
            source_tier="Tier 3",
            source_url="https://en.wikipedia.org/wiki/Mahinda_Rajapaksa_International_Cricket_Stadium",
            hero_url="https://www.swa.lk/sites/default/files/project-bottom-image/mahinda-rajapaksa-international-cricket-stadium-hambantota-2.jpg",
            hero_source_name="SWA public project image",
            hero_source_url="https://www.swa.lk/projects/mahinda-rajapaksa-international-cricket-stadium",
        ),
        _draft(
            bbox,
            city="Galle",
            property_name="Galle International Stadium",
            scene_type="stadium",
            annual_visits=525_000,
            latitude=6.0317,
            longitude=80.2164,
            geocode_precision="stadium centroid",
            field_group="seat_count",
            indicator_name="seat_count",
            field_value="Stadium capacity: 35,000",
            source_name="Wikipedia / Sportsmatik",
            source_tier="Tier 3",
            source_url="https://en.wikipedia.org/wiki/Galle_International_Stadium",
            hero_url="https://upload.wikimedia.org/wikipedia/commons/8/8f/Sri_Lanka_vs_Pakistan_test_match.JPG",
            hero_source_name="Wikimedia Commons",
            hero_source_url="https://en.wikipedia.org/wiki/Galle_International_Stadium",
        ),
        _draft(
            bbox,
            city="Dambulla",
            property_name="Rangiri Dambulla International Stadium",
            scene_type="stadium",
            annual_visits=252_000,
            latitude=7.8683,
            longitude=80.6517,
            geocode_precision="stadium centroid",
            field_group="seat_count",
            indicator_name="seat_count",
            field_value="Stadium capacity: 16,800 seats",
            source_name="Wikipedia / ESPNcricinfo",
            source_tier="Tier 3",
            source_url="https://en.wikipedia.org/wiki/Rangiri_Dambulla_International_Stadium",
            hero_url="https://upload.wikimedia.org/wikipedia/commons/f/fe/RDICS.jpg",
            hero_source_name="Wikimedia Commons",
            hero_source_url="https://en.wikipedia.org/wiki/Rangiri_Dambulla_International_Stadium",
        ),
        _draft(
            bbox,
            city="Colombo",
            property_name="Sugathadasa Stadium",
            scene_type="stadium",
            annual_visits=375_000,
            latitude=6.9486,
            longitude=79.8675,
            geocode_precision="stadium centroid",
            field_group="seat_count",
            indicator_name="seat_count",
            field_value="Stadium capacity: 25,000 people",
            source_name="Wikipedia / World of Stadiums",
            source_tier="Tier 3",
            source_url="https://en.wikipedia.org/wiki/Sugathadasa_Stadium",
            hero_url="https://upload.wikimedia.org/wikipedia/commons/c/cf/Sugatadasa_Stadium.jpg",
            hero_source_name="Wikimedia Commons",
            hero_source_url="https://en.wikipedia.org/wiki/Sugathadasa_Stadium",
        ),
        _draft(
            bbox,
            city="Colombo",
            property_name="Marino Mall Colombo",
            scene_type="mall_mixed_use",
            annual_visits=300_000,
            latitude=6.8979,
            longitude=79.8537,
            geocode_precision="shopping mall centroid",
            field_group="retail_gfa",
            indicator_name="retail_gfa",
            field_value="Retail area: over 150,000 square feet",
            source_name="Marino Mall",
            source_tier="Tier 1",
            source_url="https://www.marinomall.com/",
            hero_url="https://dynamic-media-cdn.tripadvisor.com/media/photo-o/31/22/14/aa/marino-mall-covers-an.jpg?w=1200&h=1200&s=1",
            hero_source_name="Tripadvisor public image metadata",
            hero_source_url="https://www.tripadvisor.com/Attraction_Review-g293962-d15084869-Reviews-Marino_Mall-Colombo_Western_Province.html",
        ),
        _draft(
            bbox,
            city="Colombo",
            property_name="Colombo Central Bus Terminal",
            scene_type="transport_hub",
            annual_visits=27_375_000,
            latitude=6.9367,
            longitude=79.8537,
            geocode_precision="bus terminal centroid",
            field_group="daily_ridership",
            indicator_name="daily_ridership",
            field_value="Serves approximately 75,000 daily passengers and nearly 2,000 buses",
            source_name="Clean Sri Lanka",
            source_tier="Tier 2",
            source_url="https://cleansrilanka.gov.lk/news/renovated-colombo-central-bus-terminal-opens-to-public",
            hero_url="https://adaderanaenglish.s3.amazonaws.com/1775633901-Pettah-Central-Bus-Stand-Colombo-Sri-Lanka-6.jpg",
            hero_source_name="Ada Derana public news image",
            hero_source_url="http://www.adaderana.lk/news/120915/renovated-colombo-pettah-central-bus-stand-declared-opened",
        ),
    ]


def _draft(
    bbox: dict[str, float],
    *,
    city: str,
    property_name: str,
    scene_type: str,
    annual_visits: float,
    latitude: float,
    longitude: float,
    geocode_precision: str,
    field_group: str,
    indicator_name: str,
    field_value: str,
    source_name: str,
    source_tier: str,
    source_url: str,
    hero_url: str,
    hero_source_name: str,
    hero_source_url: str,
) -> CandidateDraft:
    return CandidateDraft(
        region="Asia Pacific",
        country="Sri Lanka",
        city=city,
        property_name=property_name,
        scene_type=scene_type,
        annual_visits=float(annual_visits),
        latitude=latitude,
        longitude=longitude,
        geocode_precision=geocode_precision,
        map_source="public map coordinate cross-check",
        map_source_date=SOURCE_DATE,
        field_group=field_group,
        indicator_name=indicator_name,
        field_value=field_value,
        source_name=source_name,
        source_tier=source_tier,
        source_url=source_url,
        source_date=SOURCE_DATE,
        evidence_type="Direct",
        bbox=bbox,
        source_type=SOURCE_TYPE,
        content_text=f"{property_name}: {field_value}",
        hero_image={
            "url": hero_url,
            "alt_text": f"{property_name} public property image",
            "source_name": hero_source_name,
            "source_url": hero_source_url,
            "source_date": SOURCE_DATE,
            "license": "Public web image metadata",
        },
    )


def _rejected(draft: CandidateDraft, issues: list[str]) -> dict[str, Any]:
    return {
        "country": draft.country,
        "city": draft.city,
        "property_name": draft.property_name,
        "scene_type": draft.scene_type,
        "issues": issues,
    }


def _curation_summary(result: Any | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "curation_run_id": result.curation_run_id,
        "new_evidence_count": result.new_evidence_count,
        "accepted_count": result.accepted_count,
        "updated_count": result.updated_count,
        "rejected_count": result.rejected_count,
        "countries": result.countries,
        "summary_path": str(result.summary_path),
        "report_path": str(result.report_path),
    }


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
    rows = [
        "| Property | Scene | Metric | Source |",
        "| --- | --- | --- | --- |",
    ]
    for item in summary["accepted_candidates"]:
        rows.append(
            f"| {item['property_name']} | {item['scene_type']} | "
            f"{item['field_value']} | {item['source_url']} |"
        )
    return "\n".join(
        [
            "# APAC Sri Lanka Other Scene Seed",
            "",
            f"- Planned: {summary['planned_count']}",
            f"- Accepted: {summary['accepted_count']}",
            f"- Accepted by scene: {summary['accepted_by_scene']}",
            f"- Skipped: {summary['skipped']}",
            "",
            *rows,
            "",
        ]
    )


if __name__ == "__main__":
    main()
