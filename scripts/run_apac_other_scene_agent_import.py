from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.derived_refresh_trigger import refresh_derived_after_scan
from isite2.growth.evidence_intake import (
    DEFAULT_OVERLAY_PATH,
    CandidateDraft,
    candidate_key,
    candidate_keys,
    load_effective_source_registry,
    load_registry_overlay,
    validate_candidate_draft,
    write_registry_overlay,
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
    parser.add_argument(
        "--allow-missing-image",
        action="store_true",
        help="Allow otherwise valid candidates without a hero image; missing images remain explicit.",
    )
    parser.add_argument(
        "--designated-lead-mode",
        action="store_true",
        help=(
            "Allow identity evidence for a user-designated lead whose quantitative "
            "primary metric is still missing."
        ),
    )
    parser.add_argument(
        "--designation-source",
        help="Auditable source label or URL for the designated lead list.",
    )
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
    designated_missing_metric_keys: set[tuple[str, str, str, str]] = set()
    accepted_missing_image_count = 0
    rejected: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    seen: set[tuple[str, str, str]] = set()
    for item in parsed:
        has_real_image = _real_image(item.get("image_url"))
        if not has_real_image and not args.allow_missing_image:
            skipped["missing_real_image"] += 1
            rejected.append(_reject(item, "missing real property image"))
            continue
        missing_objective_metric = not _has_objective_metric_value(item)
        if missing_objective_metric and not args.designated_lead_mode:
            skipped["missing_objective_metric_value"] += 1
            rejected.append(_reject(item, "objective metric value is empty"))
            continue
        if item.get("scene_type") == "transport_hub" and not _accepted_transport_metric(item):
            skipped["transport_metric_not_objective"] += 1
            rejected.append(
                _reject(
                    item,
                    "transport metric is not daily ridership/interchange volume/line count",
                )
            )
            continue
        metric = _metric(item)
        if metric is None and args.designated_lead_mode and missing_objective_metric:
            metric = ("property_identity", "property_identity")
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
            designated_missing_metric=missing_objective_metric,
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
        if missing_objective_metric:
            designated_missing_metric_keys.add(
                candidate_key(
                    draft.country,
                    draft.city,
                    draft.property_name,
                    draft.scene_type,
                )
            )
        if not has_real_image:
            accepted_missing_image_count += 1

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
        overlay_hero_image_update_count = _sync_accepted_hero_images_to_overlay(
            DEFAULT_OVERLAY_PATH,
            accepted,
        )
        if designated_missing_metric_keys:
            _mark_designated_missing_metric_candidates(
                DEFAULT_OVERLAY_PATH,
                designated_missing_metric_keys,
                designation_source=(
                    args.designation_source
                    or ", ".join(str(path) for path in args.input_files if path.exists())
                ),
            )
        if not args.skip_sync:
            repository = SQLAlchemyScanRunRepository.from_url(args.db_url, storage_mode="sqlite")
            sync = sync_overlay_to_active_repository(repository, refresh_derived=False)
            if sync.run_id is not None and written_or_changed:
                property_ids = _accepted_property_ids(
                    repository,
                    sync.run_id,
                    accepted,
                )
                targeted_refresh = (
                    refresh_derived_after_scan(
                        repository,
                        sync.run_id,
                        property_ids=property_ids,
                    )
                    if property_ids
                    else {
                        "mode": "gpt_derived_info_refresh",
                        "scan_run_id": str(sync.run_id),
                        "property_ids": [],
                        "skipped_reason": (
                            "changed candidates did not resolve to active property ids; "
                            "unscoped refresh was not attempted"
                        ),
                    }
                )
                sync = replace(sync, derived_refresh=targeted_refresh)

    summary = {
        "mode": args.source_type,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "input_files": [str(path) for path in args.input_files if path.exists()],
        "parsed_count": len(parsed),
        "accepted_count": len(accepted),
        "accepted_missing_image_count": accepted_missing_image_count,
        "designated_missing_primary_metric_count": len(designated_missing_metric_keys),
        "accepted_by_country": dict(Counter(draft.country for draft in accepted)),
        "accepted_by_scene": dict(Counter(draft.scene_type for draft in accepted)),
        "skipped": dict(skipped),
        "rejected_count": len(rejected),
        "rejected": rejected[:100],
        "raw_evidence_written_or_changed": written_or_changed,
        "overlay_hero_image_update_count": (
            overlay_hero_image_update_count if accepted and not args.dry_run else 0
        ),
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
    return not _contains_placeholder_image_token(text)


def _contains_placeholder_image_token(url: str) -> bool:
    lowered = url.casefold()
    if "googleads" in lowered:
        return True
    parts = [part for part in re.split(r"[^a-z0-9]+", lowered) if part]
    part_set = set(parts)
    if {"placeholder", "logo"} & part_set:
        return True
    if {"icon", "icons", "favicon"} & part_set:
        return True
    if {"map", "maps", "staticmap", "staticmaps", "maptile", "maptiles"} & part_set:
        return True
    return False


def _accepted_transport_metric(item: dict[str, Any]) -> bool:
    text = _objective_metric_text(item)
    if _line_count_from_text(text) is not None:
        return True
    return "ridership" in text or (
        "daily" in text and any(token in text for token in ("passenger", "boarding"))
    )


def _metric(item: dict[str, Any]) -> tuple[str, str] | None:
    scene = str(item.get("scene_type") or "")
    text = _objective_metric_text(item)
    if scene == "airport_terminal":
        if "capacity" in text:
            return "terminal_capacity", "terminal_capacity"
        if "passenger" in text:
            return "annual_passenger_throughput", "annual_passenger_throughput"
    if scene == "stadium":
        if any(token in text for token in ("seat", "capacity")):
            return "seat_count", "seat_count"
    if scene == "mall_mixed_use":
        if any(
            token in text
            for token in (
                "gfa",
                "nla",
                "floor area",
                "gross",
                "area",
                "m2",
                "sqm",
                "square meter",
                "square metre",
            )
        ):
            if "nla" in text:
                return "mixed_use_area", "mixed_use_area"
            return "retail_gfa", "retail_gfa"
        if any(
            token in text
            for token in (
                "annual footfall",
                "annual visits",
                "annual visitors",
                "footfall",
                "visits",
                "visitors",
                "visitor",
                "afluencia",
            )
        ):
            return "annual_footfall", "annual_footfall"
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
        if _line_count_from_text(text) is not None:
            return "line_count", "line_count"
        if "ridership" in text or (
            "daily" in text and any(token in text for token in ("passenger", "boarding"))
        ):
            return "daily_ridership", "daily_ridership"
    if scene == "cruise_port":
        if any(
            token in text
            for token in (
                "passenger throughput",
                "annual passengers",
                "cruise passengers",
            )
        ):
            return "passenger_throughput", "passenger_throughput"
        if any(
            token in text
            for token in (
                "international_cruise_calls",
                "international cruise calls",
                "scheduled call",
                "scheduled calls",
                "cruise call frequency",
            )
        ):
            return "international_cruise_frequency", "international_cruise_frequency"
    if scene == "hospital":
        if "bed" in text:
            return "beds", "beds"
        if any(token in text for token in ("outpatient", "patient volume", "visits")):
            return "outpatient_volume", "outpatient_volume"
    if scene == "university":
        if any(token in text for token in ("enrollment", "student", "campus population")):
            return "enrollment", "enrollment"
    if scene == "mosque":
        if any(
            token in text
            for token in (
                "annual visitors",
                "annual_visitors",
                "annual visits",
                "annual_visits",
                "annual footfall",
                "annual_footfall",
                "yearly visitors",
                "visitor count",
                "number of visitors",
                "عدد الزوار",
                "زائر",
                "زوار",
            )
        ):
            return "annual_visitors", "annual_visitors"
        if any(token in text for token in ("daily visitors", "daily visits")):
            return "daily_visitors", "daily_visitors"
        if any(
            token in text
            for token in (
                "prayer hall area",
                "prayer area",
                "gross floor area",
                "floor area",
                "built-up area",
                "built up area",
                "mosque area",
                "area",
                "sqm",
                "sq m",
                "m2",
                "m²",
                "مساحة",
            )
        ):
            return "mosque_area", "mosque_area"
    return None


def _objective_metric_text(item: dict[str, Any]) -> str:
    return (
        f"{item.get('objective_metric_name', '')} "
        f"{item.get('objective_metric_value', '')}"
    ).casefold()


def _has_objective_metric_value(item: dict[str, Any]) -> bool:
    return bool(str(item.get("objective_metric_value") or "").strip())


def _line_count_from_text(text: str) -> int | None:
    patterns = [
        r"\bline[_\s]*count\b[^0-9]{0,24}(\d{1,3})\b",
        r"\b(\d{1,3})\s*(?:rail/metro\s*)?(?:metro\s*)?(?:rail\s*)?"
        r"(?:lines|routes)\b",
        r"\b(\d{1,3})\s+(?:[a-z]+[/\s]+){1,3}(?:lines|routes)\b",
        r"\b(?:serves|served\s+by|connected\s+to|interchange\s+with)\s+"
        r"(\d{1,3})\s*(?:metro\s*)?(?:rail\s*)?(?:lines|routes)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = int(match.group(1))
        if 1 <= value <= 20:
            return value
    return None


def _draft(
    item: dict[str, Any],
    metric: tuple[str, str],
    registry: dict[str, Any],
    *,
    source_type: str,
    source_date: str,
    region: str,
    designated_missing_metric: bool = False,
) -> CandidateDraft:
    country = str(item["country"])
    scene_type = str(item["scene_type"])
    bbox = registry.get("countries", {}).get(country, {}).get("bbox", {})
    field_group, indicator_name = metric
    value = str(item.get("objective_metric_value") or "")
    name = str(item.get("objective_metric_name") or indicator_name)
    if designated_missing_metric:
        value = "Exact property identity confirmed; quantitative scene primary metric missing."
        name = "Designated lead identity"
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
        source_name=str(
            _evidence_value(item, "evidence_source_name", "source_name")
            or "Firecrawl discovered public source"
        ),
        source_tier=str(
            _evidence_value(item, "evidence_source_tier", "source_tier") or "Tier 2"
        ),
        source_url=str(_evidence_value(item, "evidence_source_url", "source_url") or ""),
        source_date=str(
            _evidence_value(item, "evidence_source_date", "source_date") or source_date
        ),
        evidence_type="Context" if designated_missing_metric else "Direct",
        bbox=bbox,
        source_type=source_type,
        content_text=str(item.get("confidence_notes") or ""),
        hero_image=_hero_image(item),
    )


def _mark_designated_missing_metric_candidates(
    overlay_path: Path,
    keys: set[tuple[str, str, str, str]],
    *,
    designation_source: str,
) -> None:
    overlay = load_registry_overlay(overlay_path)
    for country, country_registry in overlay.get("countries", {}).items():
        for candidate in country_registry.get("candidates", []) or []:
            key = candidate_key(
                country,
                candidate.get("city", ""),
                candidate.get("property_name", ""),
                candidate.get("scene_type", ""),
            )
            if key not in keys:
                continue
            candidate["designated_lead"] = True
            candidate["primary_metric_status"] = "missing"
            candidate["designation_source"] = designation_source
    write_registry_overlay(overlay_path, overlay)


def _hero_image(item: dict[str, Any]) -> dict[str, str] | None:
    if not _real_image(item.get("image_url")):
        return None
    return {
        "url": str(item["image_url"]),
        "alt_text": f"{item.get('property_name')} public property image",
        "source_name": str(item.get("image_source_name") or "Public web image"),
        "source_url": str(item.get("image_source_url") or item.get("image_url")),
    }


def _sync_accepted_hero_images_to_overlay(
    overlay_path: Path,
    accepted: list[CandidateDraft],
) -> int:
    """Persist image-only changes without bypassing evidence curation for new candidates."""
    overlay = load_registry_overlay(overlay_path)
    image_by_key = {
        candidate_key(
            draft.country,
            draft.city,
            draft.property_name,
            draft.scene_type,
        ): dict(draft.hero_image)
        for draft in accepted
        if draft.hero_image
    }
    changed = 0
    for country, country_registry in overlay.get("countries", {}).items():
        for candidate in country_registry.get("candidates", []) or []:
            key = candidate_key(
                country,
                candidate.get("city", ""),
                candidate.get("property_name", ""),
                candidate.get("scene_type", ""),
            )
            hero_image = image_by_key.get(key)
            if hero_image is None or candidate.get("hero_image") == hero_image:
                continue
            candidate["hero_image"] = hero_image
            changed += 1
    if changed:
        write_registry_overlay(overlay_path, overlay)
    return changed


def _evidence_value(
    item: dict[str, Any],
    flat_key: str,
    nested_key: str,
) -> Any:
    value = item.get(flat_key)
    if value not in {None, ""}:
        return value
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        return None
    for row in evidence:
        if not isinstance(row, dict):
            continue
        value = row.get(nested_key)
        if value not in {None, ""}:
            return value
    return None


def _geocode_precision(item: dict[str, Any]) -> str:
    provided = str(item.get("geocode_precision") or "").strip()
    if provided:
        scene_type = str(item.get("scene_type") or "")
        normalized = provided.casefold()
        if scene_type == "airport_terminal" and not any(
            token in normalized for token in ("airport", "aerodrome", "terminal")
        ):
            return f"{provided}; airport venue centroid"
        if scene_type == "cruise_port" and not any(
            token in normalized for token in ("port", "cruise", "terminal")
        ):
            return f"{provided}; cruise port centroid"
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
        "mosque": "mosque building centroid",
        "cruise_port": "cruise port or terminal centroid",
    }.get(scene_type, "venue centroid")


def _accepted_property_ids(
    repository: SQLAlchemyScanRunRepository,
    run_id: Any,
    accepted: list[CandidateDraft],
) -> list[str]:
    accepted_keys = {
        (
            draft.country.casefold(),
            draft.city.casefold(),
            draft.property_name.casefold(),
            draft.scene_type.casefold(),
        )
        for draft in accepted
    }
    property_ids = {
        str(packet.entity.property_id)
        for packet in repository.list_properties({"scan_run_id": str(run_id)})
        if (
            packet.entity.country.casefold(),
            packet.entity.city.casefold(),
            packet.entity.property_name.casefold(),
            str(
                getattr(
                    packet.entity.scene_type,
                    "value",
                    packet.entity.scene_type,
                )
            ).casefold(),
        )
        in accepted_keys
    }
    return sorted(property_ids)


def _reject(item: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "country": item.get("country"),
        "property_name": item.get("property_name"),
        "scene_type": item.get("scene_type"),
        "reason": reason,
        "source_url": _evidence_value(item, "evidence_source_url", "source_url"),
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
