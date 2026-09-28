from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from isite2.growth.derived_refresh_trigger import refresh_derived_after_scan
from isite2.growth.evidence_intake import DEFAULT_OVERLAY_PATH, load_effective_source_registry
from isite2.orchestrator.pipeline import build_scan_result
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

OVERLAY_SYNC_FILTER = "overlay_active_sync"
OVERLAY_SYNC_HASH = "overlay_sync_hash"
OVERLAY_SYNC_FORMAT_VERSION = 2


@dataclass(frozen=True)
class OverlaySyncResult:
    created: bool
    run_id: UUID | None
    candidate_count: int
    skipped_reason: str | None = None
    overlay_hash: str | None = None
    registry_candidate_count: int = 0
    blocked_candidate_count: int = 0
    derived_refresh: dict | None = None


def sync_overlay_to_active_repository(
    repository: SQLAlchemyScanRunRepository,
    overlay_path: Path = DEFAULT_OVERLAY_PATH,
    *,
    refresh_derived: bool = True,
) -> OverlaySyncResult:
    source_registry = load_effective_source_registry(overlay_path)
    countries = _countries_with_candidates(source_registry)
    registry_candidate_count = sum(
        len(country.get("candidates", []))
        for country in source_registry.get("countries", {}).values()
    )
    overlay_hash = _overlay_hash(source_registry)
    existing_sync_runs = _overlay_sync_runs(repository)

    if existing_sync_runs and all(
        _sync_hash(result) == overlay_hash for result in existing_sync_runs
    ):
        latest = max(existing_sync_runs, key=lambda result: result.scan_run.created_at)
        visible_candidate_count = int(
            latest.scan_run.scope.custom_filters.get(
                "visible_candidate_count",
                latest.scan_run.candidate_count,
            )
        )
        derived_refresh = _derived_refresh_skipped(
            latest.scan_run.run_id,
            reason=(
                "overlay hash unchanged; derived fields were not refreshed"
                if refresh_derived
                else "overlay sync derived refresh disabled"
            ),
        )
        return OverlaySyncResult(
            created=False,
            run_id=latest.scan_run.run_id,
            candidate_count=visible_candidate_count,
            skipped_reason="overlay hash already synced to active repository",
            overlay_hash=overlay_hash,
            registry_candidate_count=registry_candidate_count,
            blocked_candidate_count=max(
                registry_candidate_count - visible_candidate_count,
                0,
            ),
            derived_refresh=derived_refresh,
        )

    scope_data = {
        "level": "global",
        "regions": [],
        "countries": countries,
        "cities": [],
        "full_scan": True,
        "scene_types": [],
        "output_formats": ["geojson"],
        "custom_filters": {
            "registry_backed_only": True,
            OVERLAY_SYNC_FILTER: True,
            OVERLAY_SYNC_HASH: overlay_hash,
            "overlay_path": str(overlay_path),
            "sync_visible_only": False,
            "visibility_authoritative_snapshot": True,
            "registry_candidate_count": registry_candidate_count,
        },
    }
    result = build_scan_result(
        scope_data,
        source_registry=source_registry,
        include_blocked_quality=True,
    )
    visible_candidate_count = sum(
        packet.candidate_quality_status != "blocked_quality"
        for packet in result.packets
    )
    result.scan_run.scope.custom_filters["visible_candidate_count"] = (
        visible_candidate_count
    )
    blocked_candidate_count = max(
        registry_candidate_count - visible_candidate_count,
        0,
    )
    prior_run_ids = [sync_result.scan_run.run_id for sync_result in existing_sync_runs]
    repository.delete_runs(prior_run_ids)
    saved = repository.save(result)
    derived_refresh = (
        _refresh_derived_after_sync(repository, saved.scan_run.run_id)
        if refresh_derived
        else _derived_refresh_skipped(saved.scan_run.run_id)
    )
    return OverlaySyncResult(
        created=True,
        run_id=saved.scan_run.run_id,
        candidate_count=visible_candidate_count,
        overlay_hash=overlay_hash,
        registry_candidate_count=registry_candidate_count,
        blocked_candidate_count=blocked_candidate_count,
        derived_refresh=derived_refresh,
    )


def _overlay_hash(source_registry: dict) -> str:
    payload = json.dumps(
        {
            "sync_format_version": OVERLAY_SYNC_FORMAT_VERSION,
            "source_registry": source_registry,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _countries_with_candidates(source_registry: dict) -> list[str]:
    return sorted(
        country
        for country, registry in source_registry.get("countries", {}).items()
        if registry.get("candidates")
    )


def _overlay_sync_runs(repository: SQLAlchemyScanRunRepository):
    return [
        result
        for result in repository.list()
        if result.scan_run.scope.custom_filters.get(OVERLAY_SYNC_FILTER)
    ]


def _sync_hash(result) -> str | None:
    return result.scan_run.scope.custom_filters.get(OVERLAY_SYNC_HASH)


def _refresh_derived_after_sync(
    repository: SQLAlchemyScanRunRepository,
    scan_run_id: UUID,
) -> dict:
    visible_property_ids = [
        str(packet.entity.property_id)
        for packet in repository.list_properties({"scan_run_id": str(scan_run_id)})
        if packet.candidate_quality_status != "blocked_quality"
        and packet.entity.property_id is not None
    ]
    return refresh_derived_after_scan(
        repository,
        scan_run_id,
        property_ids=visible_property_ids,
    )


def _derived_refresh_skipped(
    scan_run_id: UUID,
    *,
    reason: str = "overlay sync derived refresh disabled",
) -> dict:
    return {
        "mode": "gpt_derived_info_refresh",
        "scan_run_id": str(scan_run_id),
        "skipped_reason": reason,
    }
