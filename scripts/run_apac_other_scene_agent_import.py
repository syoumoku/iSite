from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import (
    CandidateDraft,
    candidate_key,
    candidate_keys,
    load_effective_source_registry,
    validate_candidate_draft,
)
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import NEW_OPPORTUNITY, known_opportunity_index_from_registry
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_URL = "sqlite+pysqlite:///outputs/isite2_dev.db"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
SOURCE_TYPE = "firecrawl_agent_other_scene_apac"
SOURCE_DATE = "2026-05-14"

INPUT_FILES = [
    Path(".firecrawl/apac_other_scenes/agent_cambodia_other_scenes.json"),
    Path(".firecrawl/apac_other_scenes/agent_maldives_other_scenes.json"),
    Path(".firecrawl/apac_other_scenes/agent_sri_lanka_other_scenes.json"),
]

PLACEHOLDER_TOKENS = (
    "placeholder",
    "logo",
    "icon",
    "map",
    "googleads",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import structured agent-discovered scene candidates into curation."
    )
    parser.add_argument("--input-files", nargs="+", type=Path, default=INPUT_FILES)
    parser.add_argument("--db-url", default=DB_URL)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--source-type", default=SOURCE_TYPE)
    parser.add_argument("--source-date", default=SOURCE_DATE)
    parser.add_argument("--region", default="Asia Pacific")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    parsed = []
    for path in args.input_files:
        if path.exists():
            parsed.extend(_load_candidates(path))

    registry = load_effective_source_registry() if parsed else {}
    existing_keys = candidate_keys(registry) if parsed else set()
    store = EvidenceCurationStore(database_url=args.db_url) if parsed else None
    known_index = (
        known_opportunity_index_from_registry(
            registry,
            session_factory=store.session_factory,
        )
        if store is not None
        else None
    )

    accepted: list[CandidateDraft] = []
    rejected: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    seen: set[tuple[str, str, str]] = set()
    for item in parsed:
        if not _real_image(item.get("image_url")):
            skipped["missing_real_image"] += 1
            rejected.append(_reject(item, "missing real property image"))
            continue
        if item.get("scene_type") == "transport_hub" and not _accepted_transport_metric(item):
            skipped["transport_metric_not_objective"] += 1
            rejected.append(_reject(item, "transport metric is not daily ridership/interchange volume/line count"))
            continue
        metric = _metric(item)
        if metric is None:
            skipped["metric_not_supported"] += 1
            rejected.append(_reject(item, "metric is not accepted for scene objective evidence"))
            continue
        key = (
            str(item.get("country", "")).casefold(),
            str(item.get("scene_type", "")).casefold(),
            str(item.get("property_name", "")).casefold(),
        )
        if key in seen:
            skipped["duplicate_in_agent_output"] += 1
            continue
        seen.add(key)

        draft = _draft(
            item,
            metric,
            registry,
            source_type=args.source_type,
            source_date=args.source_date,
            region=args.region,
        )
        validation = validate_candidate_draft(draft)
        if not validation.accepted:
            skipped["validation_failed"] += 1
            rejected.append(_reject(item, "; ".join(validation.issues)))
            continue
        assert known_index is not None
        identity_match = known_index.match(
            country=draft.country,
            city=draft.city,
            property_name=draft.property_name,
            scene_type=draft.scene_type,
            latitude=draft.latitude,
            longitude=draft.longitude,
            source_url=draft.source_url,
        )
        key = candidate_key(draft.country, draft.city, draft.property_name, draft.scene_type)
        if identity_match.status != NEW_OPPORTUNITY and key not in existing_keys:
            skipped[f"identity_{identity_match.status}"] += 1
            rejected.append(_reject(item, identity_match.reason or identity_match.status))
            continue
        accepted.append(draft)

    raw_ids: list[str] = []
    written_or_changed = 0
    curation = None
    sync = None
    if accepted and not args.dry_run:
        assert store is not None
        for draft in accepted:
            result = store.upsert_candidate_evidence(draft, source_type=args.source_type)
            raw_ids.append(result.raw_evidence_id)
            if result.is_new_evidence or result.is_changed_evidence:
                written_or_changed += 1
        curation = run_pending_evidence_curation(store=store, output_dir=args.output_dir)
        if not args.skip_sync:
            repository = SQLAlchemyScanRunRepository.from_url(args.db_url, storage_mode="sqlite")
            sync = sync_overlay_to_active_repository(repository)

    summary = {
        "mode": args.source_type,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "input_files": [str(path) for path in args.input_files if path.exists()],
        "parsed_count": len(parsed),
        "accepted_count": len(accepted),
        "accepted_by_country": dict(Counter(draft.country for draft in accepted)),
        "accepted_by_scene": dict(Counter(draft.scene_type for draft in accepted)),
        "skipped": dict(skipped),
        "rejected_count": len(rejected),
        "rejected": rejected[:100],
        "raw_evidence_written_or_changed": written_or_changed,
        "raw_evidence_ids": raw_ids,
        "curation": _curation_summary(curation),
        "overlay_sync": _sync_summary(sync),
        "accepted_candidates": [
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
            for draft in accepted
        ],
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = args.output_dir / f"agent_scene_candidate_import_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    marker = "Result:"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    payload = json.loads(text)
    candidates = payload.get("candidates")
    if candidates is None and isinstance(payload.get("data"), dict):
        candidates = payload["data"].get("candidates")
    return list(candidates or [])


def _real_image(url: Any) -> bool:
    text = str(url or "").strip()
    if not text.startswith(("http://", "https://")):
        return False
    lowered = text.casefold()
    return not any(token in lowered for token in PLACEHOLDER_TOKENS)


def _accepted_transport_metric(item: dict[str, Any]) -> bool:
    text = f"{item.get('objective_metric_name', '')} {item.get('objective_metric_value', '')}".casefold()
    return any(token in text for token in ("daily", "ridership", "passenger", "line count", "lines"))


def _metric(item: dict[str, Any]) -> tuple[str, str] | None:
    scene = str(item.get("scene_type") or "")
    text = f"{item.get('objective_metric_name', '')} {item.get('objective_metric_value', '')}".casefold()
    if scene == "airport_terminal":
        if "capacity" in text:
            return "terminal_capacity", "terminal_capacity"
        if "passenger" in text:
            return "annual_passenger_throughput", "annual_passenger_throughput"
    if scene == "stadium":
        if any(token in text for token in ("seat", "capacity")):
            return "seat_count", "seat_count"
    if scene == "mall_mixed_use":
        if any(token in text for token in ("gfa", "floor area", "gross", "area", "m2", "sqm")):
            return "retail_gfa", "retail_gfa"
        if any(token in text for token in ("largest", "flagship")):
            return "flagship_position", "flagship_position"
    if scene == "office_government":
        if any(
            token in text
            for token in (
                "office_gfa",
                "gross floor area",
                "built-up area",
                "built up area",
                "building area",
                "bua",
                "sqm",
                "sq m",
                "m2",
                "m²",
                "square meter",
                "square metre",
            )
        ):
            return "office_gfa", "office_gfa"
        if "floor" in text:
            return "floor_count", "floor_count"
        if "height" in text or " m" in text:
            return "tower_height", "tower_height"
        if "grade" in text:
            return "building_grade", "building_grade"
    if scene == "convention_center":
        if any(token in text for token in ("area", "m2", "sqm", "floor")):
            return "exhibition_area", "exhibition_area"
        if any(token in text for token in ("capacity", "hall")):
            return "peak_event_capacity", "peak_event_capacity"
    if scene == "luxury_hotel_mice":
        if any(
            token in text
            for token in (
                "guest room",
                "guest rooms",
                "sleeping room",
                "sleeping rooms",
                "room count",
                "keys",
            )
        ):
            return "keys", "keys"
        if any(token in text for token in ("area", "m2", "sqm", "sq ft", "sq.ft", "sq. ft")):
            return "meeting_ballroom_area", "meeting_ballroom_area"
        if any(token in text for token in ("ballroom", "banquet", "meeting", "event", "capacity")):
            return "ballroom_capacity", "ballroom_capacity"
        if any(token in text for token in ("room", "key")):
            return "keys", "keys"
    if scene == "transport_hub":
        if any(token in text for token in ("daily", "ridership", "passenger")):
            return "daily_ridership", "daily_ridership"
        if "line" in text:
            return "line_count", "line_count"
    if scene == "hospital":
        if "bed" in text:
            return "beds", "beds"
        if any(token in text for token in ("outpatient", "patient volume", "visits")):
            return "outpatient_volume", "outpatient_volume"
    if scene == "university":
        if any(token in text for token in ("enrollment", "student", "campus population")):
            return "enrollment", "enrollment"
    return None


def _draft(
    item: dict[str, Any],
    metric: tuple[str, str],
    registry: dict[str, Any],
    *,
    source_type: str,
    source_date: str,
    region: str,
) -> CandidateDraft:
    country = str(item["country"])
    scene_type = str(item["scene_type"])
    bbox = registry.get("countries", {}).get(country, {}).get("bbox", {})
    field_group, indicator_name = metric
    value = str(item.get("objective_metric_value") or "")
    name = str(item.get("objective_metric_name") or indicator_name)
    annual_visits = (
        float(item["annual_visits_estimate"])
        if item.get("annual_visits_estimate") not in {None, ""}
        else None
    )
    return CandidateDraft(
        region=region,
        country=country,
        city=str(item.get("city") or ""),
        property_name=str(item.get("property_name") or ""),
        scene_type=scene_type,
        annual_visits=annual_visits,
        latitude=float(item["latitude"]),
        longitude=float(item["longitude"]),
        geocode_precision=_geocode_precision(item),
        map_source=str(item.get("map_source") or "Firecrawl agent sourced public coordinate"),
        map_source_date=str(item.get("map_source_date") or source_date),
        field_group=field_group,
        indicator_name=indicator_name,
        field_value=f"{name}: {value}",
        source_name=str(item.get("evidence_source_name") or "Firecrawl discovered public source"),
        source_tier=str(item.get("evidence_source_tier") or "Tier 2"),
        source_url=str(item["evidence_source_url"]),
        source_date=str(item.get("evidence_source_date") or source_date),
        evidence_type="Direct",
        bbox=bbox,
        source_type=source_type,
        content_text=str(item.get("confidence_notes") or ""),
        hero_image={
            "url": str(item["image_url"]),
            "alt_text": f"{item.get('property_name')} public property image",
            "source_name": str(item.get("image_source_name") or "Public web image"),
            "source_url": str(item.get("image_source_url") or item.get("image_url")),
        },
    )


def _geocode_precision(item: dict[str, Any]) -> str:
    provided = str(item.get("geocode_precision") or "").strip()
    if provided:
        return provided
    scene_type = str(item.get("scene_type") or "")
    return {
        "airport_terminal": "airport terminal centroid",
        "stadium": "stadium venue centroid",
        "mall_mixed_use": "shopping mall centroid",
        "office_government": "office tower centroid",
        "convention_center": "convention venue centroid",
        "luxury_hotel_mice": "hotel venue centroid",
        "transport_hub": "rail or metro station centroid",
    }.get(scene_type, "venue centroid")


def _reject(item: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "country": item.get("country"),
        "property_name": item.get("property_name"),
        "scene_type": item.get("scene_type"),
        "reason": reason,
        "source_url": item.get("evidence_source_url"),
    }


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
    }


def _sync_summary(sync: Any | None) -> dict[str, Any] | None:
    if sync is None:
        return None
    return {
        "created": sync.created,
        "run_id": str(sync.run_id) if sync.run_id else None,
        "candidate_count": sync.candidate_count,
        "registry_candidate_count": sync.registry_candidate_count,
        "blocked_candidate_count": sync.blocked_candidate_count,
        "skipped_reason": sync.skipped_reason,
        "derived_refresh": sync.derived_refresh,
    }


def _render_report(summary: dict[str, Any]) -> str:
    return (
        "# Agent Scene Candidate Import\n\n"
        f"- Parsed: {summary['parsed_count']}\n"
        f"- Accepted: {summary['accepted_count']}\n"
        f"- Accepted by country: {summary['accepted_by_country']}\n"
        f"- Accepted by scene: {summary['accepted_by_scene']}\n"
        f"- Skipped: {summary['skipped']}\n"
    )


if __name__ == "__main__":
    main()
