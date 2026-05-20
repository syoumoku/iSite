from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isite2.growth.derived_refresh_trigger import refresh_derived_after_scan
from isite2.growth.evidence_curation import (
    EvidenceCurationRunResult,
    run_pending_evidence_curation,
)
from isite2.growth.evidence_discovery import (
    EvidenceDiscoveryResult,
    discover_public_evidence,
)
from isite2.growth.evidence_intake import (
    DEFAULT_DRAFT_PATH,
    DEFAULT_OVERLAY_PATH,
    EvidenceIntakeProvider,
    load_effective_source_registry,
)
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.regional_targets import (
    ASIA_PACIFIC_COUNTRIES,
    DEFAULT_REGIONS,
    LATIN_AMERICA_COUNTRIES,
    REGION_COUNTRIES,
    countries_for_regions,
)
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.output.excel import write_excel_skeleton
from isite2.repositories import get_default_repository
from isite2.repositories.interfaces import ScanRunRepository

DEFAULT_BATCH_MIN = 30
DEFAULT_BATCH_MAX = 50
DEFAULT_OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
DEFAULT_STATE_PATH = DEFAULT_OUTPUT_DIR / "state.json"
__all__ = [
    "DEFAULT_REGIONS",
    "ASIA_PACIFIC_COUNTRIES",
    "LATIN_AMERICA_COUNTRIES",
    "countries_for_regions",
    "registry_backed_country_counts",
    "run_regional_scan_round",
    "select_country_batch",
]


@dataclass(frozen=True)
class RegionalLoopRound:
    round_number: int
    regions: list[str]
    countries: list[str]
    run_id: str | None
    candidate_count: int
    review_count: int
    storage_mode: str | None
    report_path: Path
    summary_path: Path
    excel_path: Path | None
    improvements: list[str]
    ui_paths: dict[str, str]
    mode: str = "scan"
    scan_created: bool = True


def run_regional_scan_round(
    repository: ScanRunRepository | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    regions: list[str] | None = None,
    batch_min: int = DEFAULT_BATCH_MIN,
    batch_max: int = DEFAULT_BATCH_MAX,
    overlay_path: Path = DEFAULT_OVERLAY_PATH,
    draft_path: Path = DEFAULT_DRAFT_PATH,
    intake_provider: EvidenceIntakeProvider | None = None,
    enable_intake: bool = True,
    evidence_store: EvidenceCurationStore | None = None,
    target_countries: list[str] | None = None,
    max_searches_per_cycle: int = 20,
    max_fetches_per_cycle: int = 40,
    force_scan_existing_pool: bool = False,
    new_opportunities_only: bool = True,
    force_rescan_existing: bool = False,
) -> RegionalLoopRound:
    """Run one evidence-backed regional scan batch and persist all artifacts."""
    if batch_min <= 0 or batch_max < batch_min:
        raise ValueError("batch_min must be positive and batch_max must be >= batch_min")

    selected_regions = regions or DEFAULT_REGIONS
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    all_region_countries = countries_for_regions(selected_regions)
    selected_target_countries = _select_target_countries(
        all_region_countries,
        target_countries,
    )
    state = _load_state(state_path)
    round_number = int(state.get("round_number", 0)) + 1
    repository = repository or get_default_repository()
    store = evidence_store or EvidenceCurationStore.from_repository(repository)
    discovery_result: EvidenceDiscoveryResult | None = None
    curation_result: EvidenceCurationRunResult | None = None
    progress_settlement = None

    if enable_intake:
        discovery_result = discover_public_evidence(
            regions=selected_regions,
            store=store,
            provider=intake_provider,
            output_dir=output_dir,
            max_searches_per_cycle=max_searches_per_cycle,
            max_fetches_per_cycle=max_fetches_per_cycle,
            target_countries=selected_target_countries,
            new_opportunities_only=new_opportunities_only,
            force_rescan_existing=force_rescan_existing,
        )
        curation_result = run_pending_evidence_curation(
            store=store,
            output_dir=output_dir,
            overlay_path=overlay_path,
            draft_path=draft_path,
        )
        progress_settlement = store.settle_completed_progress_groups(
            _progress_group_stats(curation_result)
        )

    effective_registry = load_effective_source_registry(overlay_path)
    registry_counts = registry_backed_country_counts(
        selected_regions,
        effective_registry,
        target_countries=selected_target_countries,
    )

    if not registry_counts:
        raise RuntimeError(
            "No target-region countries with source_registry candidates are available."
        )

    if _registry_candidate_total(registry_counts) < batch_min:
        return _write_intake_only_report(
            output_dir=output_dir,
            state_path=state_path,
            state=state,
            round_number=round_number,
            selected_regions=selected_regions,
            target_countries=selected_target_countries,
            registry_counts=registry_counts,
            curation_result=curation_result,
            discovery_result=discovery_result,
            progress_settlement=progress_settlement,
            batch_min=batch_min,
            batch_max=batch_max,
        )

    if (
        enable_intake
        and not force_scan_existing_pool
        and not _curation_changed_candidate_pool(curation_result)
    ):
        return _write_no_scan_report(
            output_dir=output_dir,
            state_path=state_path,
            state=state,
            round_number=round_number,
            selected_regions=selected_regions,
            target_countries=selected_target_countries,
            registry_counts=registry_counts,
            curation_result=curation_result,
            discovery_result=discovery_result,
            progress_settlement=progress_settlement,
            batch_min=batch_min,
            batch_max=batch_max,
        )

    batch = select_country_batch(state, registry_counts, batch_min, batch_max)

    countries = [item["country"] for item in batch]
    result = run_scan_pipeline(
        {
            "level": "region",
            "regions": selected_regions,
            "countries": countries,
            "full_scan": True,
            "output_formats": ["excel", "geojson"],
            "custom_filters": {
                "loop_name": "regional_high_value_buildings",
                "registry_backed_only": True,
                "batch_min": batch_min,
                "batch_max": batch_max,
            },
        },
        repository,
        source_registry=effective_registry,
    )
    derived_refresh = refresh_derived_after_scan(repository, result.scan_run.run_id)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = _batch_slug(countries)
    excel_path = output_dir / f"round_{round_number:04d}_{slug}_{result.scan_run.run_id}.xlsx"
    summary_path = output_dir / f"round_{round_number:04d}_{slug}_{result.scan_run.run_id}.json"
    report_path = output_dir / f"round_{round_number:04d}_{slug}_{result.scan_run.run_id}.md"

    write_excel_skeleton(excel_path, packets=result.packets)
    repository.add_output_artifact(result.scan_run.run_id, "excel", str(excel_path))

    improvements = _improvement_points(
        selected_regions=selected_regions,
        target_countries=selected_target_countries,
        registry_counts=registry_counts,
        packets=result.packets,
        batch_min=batch_min,
        batch_max=batch_max,
        curation_result=curation_result,
        discovery_result=discovery_result,
        progress_settlement=progress_settlement,
    )
    summary = _round_summary(
        round_number=round_number,
        timestamp=timestamp,
        selected_regions=selected_regions,
        countries=countries,
        target_countries=selected_target_countries,
        registry_counts=registry_counts,
        result=result,
        derived_refresh=derived_refresh,
        excel_path=excel_path,
        report_path=report_path,
        improvements=improvements,
        batch_min=batch_min,
        batch_max=batch_max,
        curation_result=curation_result,
        discovery_result=discovery_result,
        progress_settlement=progress_settlement,
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(summary), encoding="utf-8")
    (output_dir / "latest_report.md").write_text(
        report_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    repository.add_output_artifact(result.scan_run.run_id, "json_summary", str(summary_path))
    repository.add_output_artifact(result.scan_run.run_id, "markdown_report", str(report_path))

    _save_state(
        state_path,
        _next_state(
            state=state,
            selected_regions=selected_regions,
            registry_counts=registry_counts,
            round_number=round_number,
            countries=countries,
            result=result,
            report_path=report_path,
            summary_path=summary_path,
            excel_path=excel_path,
        ),
    )

    return RegionalLoopRound(
        round_number=round_number,
        regions=selected_regions,
        countries=countries,
        run_id=str(result.scan_run.run_id),
        candidate_count=result.scan_run.candidate_count,
        review_count=result.scan_run.review_count,
        storage_mode=result.storage_mode,
        report_path=report_path,
        summary_path=summary_path,
        excel_path=excel_path,
        improvements=improvements,
        ui_paths={
            "map": "/ui/",
            "geojson": "/map/properties",
            "scan_run": f"/scan-runs/{result.scan_run.run_id}",
        },
    )


def registry_backed_country_counts(
    regions: list[str] | None = None,
    registry: dict[str, Any] | None = None,
    target_countries: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected_regions = regions or DEFAULT_REGIONS
    source_registry = registry or load_effective_source_registry()
    registry_countries = source_registry.get("countries", {})
    allowed_countries = (
        {country.casefold() for country in target_countries}
        if target_countries
        else None
    )
    counts = []
    for region in selected_regions:
        for country in REGION_COUNTRIES[region]:
            if allowed_countries is not None and country.casefold() not in allowed_countries:
                continue
            country_registry = _find_registry(country, registry_countries)
            candidates = country_registry.get("candidates", []) if country_registry else []
            if candidates:
                counts.append(
                    {
                        "region": region,
                        "country": country,
                        "candidate_count": len(candidates),
                    }
                )
    return counts


def select_country_batch(
    state: dict[str, Any],
    registry_counts: list[dict[str, Any]],
    batch_min: int,
    batch_max: int,
) -> list[dict[str, Any]]:
    start = int(state.get("registry_item_index", 0)) % len(registry_counts)
    selected: list[dict[str, Any]] = []
    selected_count = 0

    for offset in range(len(registry_counts)):
        item = registry_counts[(start + offset) % len(registry_counts)]
        count = int(item["candidate_count"])
        if selected and selected_count >= batch_min and selected_count + count > batch_max:
            break
        if selected_count + count <= batch_max or selected_count < batch_min:
            selected.append(item)
            selected_count += count
        if selected_count >= batch_min:
            break

    return selected or [registry_counts[start]]


def _find_registry(country: str, countries: dict[str, Any]) -> dict[str, Any] | None:
    normalized = country.casefold()
    for canonical, registry in countries.items():
        aliases = [canonical, *registry.get("aliases", [])]
        if normalized in {alias.casefold() for alias in aliases}:
            return registry
    return None


def _select_target_countries(
    region_countries: list[str],
    target_countries: list[str] | None,
) -> list[str]:
    if not target_countries:
        return region_countries
    allowed = {country.casefold() for country in target_countries}
    return [country for country in region_countries if country.casefold() in allowed]


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "round_number": 0,
            "registry_item_index": 0,
            "cycles_completed": 0,
            "history": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _write_intake_only_report(
    output_dir: Path,
    state_path: Path,
    state: dict[str, Any],
    round_number: int,
    selected_regions: list[str],
    target_countries: list[str],
    registry_counts: list[dict[str, Any]],
    curation_result: EvidenceCurationRunResult | None,
    discovery_result: EvidenceDiscoveryResult | None,
    progress_settlement: Any | None,
    batch_min: int,
    batch_max: int,
) -> RegionalLoopRound:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = output_dir / f"intake_{round_number:04d}_{timestamp}.json"
    report_path = output_dir / f"intake_{round_number:04d}_{timestamp}.md"
    improvements = _improvement_points(
        selected_regions=selected_regions,
        target_countries=target_countries,
        registry_counts=registry_counts,
        packets=[],
        batch_min=batch_min,
        batch_max=batch_max,
        curation_result=curation_result,
        discovery_result=discovery_result,
        progress_settlement=progress_settlement,
    )
    summary = {
        "mode": "intake_only",
        "scan_created": False,
        "round_number": round_number,
        "timestamp_utc": timestamp,
        "regions": selected_regions,
        "countries": [],
        "run_id": None,
        "status": "intake_underfilled",
        "storage_mode": None,
        "batch_target": {"min": batch_min, "max": batch_max},
        "candidate_count": 0,
        "review_count": 0,
        "target_country_count": len(target_countries),
        "registry_backed_country_count": len(registry_counts),
        "registry_backed_candidate_count": _registry_candidate_total(registry_counts),
        "intake": _intake_summary(curation_result, discovery_result, progress_settlement),
        "persistent_files": {
            "sqlite_default": "outputs/isite2_dev.db",
            "excel": None,
            "report": str(report_path),
        },
        "ui_paths": {"map": "/ui/", "geojson": "/map/properties", "country_maps": {}},
        "packets": [],
        "improvement_points": improvements,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(summary), encoding="utf-8")
    (output_dir / "latest_report.md").write_text(
        report_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _save_state(
        state_path,
        _next_intake_state(
            state=state,
            selected_regions=selected_regions,
            registry_counts=registry_counts,
            round_number=round_number,
            report_path=report_path,
            summary_path=summary_path,
        ),
    )
    return RegionalLoopRound(
        round_number=round_number,
        regions=selected_regions,
        countries=[],
        run_id=None,
        candidate_count=0,
        review_count=0,
        storage_mode=None,
        report_path=report_path,
        summary_path=summary_path,
        excel_path=None,
        improvements=improvements,
        ui_paths={"map": "/ui/", "geojson": "/map/properties"},
        mode="intake_only",
        scan_created=False,
    )


def _write_no_scan_report(
    output_dir: Path,
    state_path: Path,
    state: dict[str, Any],
    round_number: int,
    selected_regions: list[str],
    target_countries: list[str],
    registry_counts: list[dict[str, Any]],
    curation_result: EvidenceCurationRunResult | None,
    discovery_result: EvidenceDiscoveryResult | None,
    progress_settlement: Any | None,
    batch_min: int,
    batch_max: int,
) -> RegionalLoopRound:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = output_dir / f"idle_{round_number:04d}_{timestamp}.json"
    report_path = output_dir / f"idle_{round_number:04d}_{timestamp}.md"
    improvements = _improvement_points(
        selected_regions=selected_regions,
        target_countries=target_countries,
        registry_counts=registry_counts,
        packets=[],
        batch_min=batch_min,
        batch_max=batch_max,
        curation_result=curation_result,
        discovery_result=discovery_result,
        progress_settlement=progress_settlement,
    )
    improvements.insert(
        0,
        "No scan run was created because this poll found no new or changed evidence that "
        "updated the candidate pool.",
    )
    summary = {
        "mode": "idle_no_new_evidence",
        "scan_created": False,
        "round_number": round_number,
        "timestamp_utc": timestamp,
        "regions": selected_regions,
        "countries": [],
        "run_id": None,
        "status": "no_new_evidence",
        "storage_mode": None,
        "batch_target": {"min": batch_min, "max": batch_max},
        "candidate_count": 0,
        "review_count": 0,
        "target_country_count": len(target_countries),
        "registry_backed_country_count": len(registry_counts),
        "registry_backed_candidate_count": _registry_candidate_total(registry_counts),
        "intake": _intake_summary(curation_result, discovery_result, progress_settlement),
        "persistent_files": {
            "sqlite_default": "outputs/isite2_dev.db",
            "excel": None,
            "report": str(report_path),
        },
        "ui_paths": {"map": "/ui/", "geojson": "/map/properties", "country_maps": {}},
        "packets": [],
        "improvement_points": improvements,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(summary), encoding="utf-8")
    (output_dir / "latest_report.md").write_text(
        report_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _save_state(
        state_path,
        _next_intake_state(
            state=state,
            selected_regions=selected_regions,
            registry_counts=registry_counts,
            round_number=round_number,
            report_path=report_path,
            summary_path=summary_path,
            mode="idle_no_new_evidence",
        ),
    )
    return RegionalLoopRound(
        round_number=round_number,
        regions=selected_regions,
        countries=[],
        run_id=None,
        candidate_count=0,
        review_count=0,
        storage_mode=None,
        report_path=report_path,
        summary_path=summary_path,
        excel_path=None,
        improvements=improvements,
        ui_paths={"map": "/ui/", "geojson": "/map/properties"},
        mode="idle_no_new_evidence",
        scan_created=False,
    )


def _next_intake_state(
    state: dict[str, Any],
    selected_regions: list[str],
    registry_counts: list[dict[str, Any]],
    round_number: int,
    report_path: Path,
    summary_path: Path,
    mode: str = "intake_only",
) -> dict[str, Any]:
    history = list(state.get("history", []))
    history.append(
        {
            "mode": mode,
            "round_number": round_number,
            "countries": [],
            "run_id": None,
            "candidate_count": 0,
            "review_count": 0,
            "report_path": str(report_path),
            "summary_path": str(summary_path),
            "excel_path": None,
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    return {
        "version": 1,
        "round_number": round_number,
        "registry_item_index": int(state.get("registry_item_index", 0)),
        "cycles_completed": int(state.get("cycles_completed", 0)),
        "registry_backed_countries": [item["country"] for item in registry_counts],
        "registry_backed_candidate_count": _registry_candidate_total(registry_counts),
        "target_country_count": len(countries_for_regions(selected_regions)),
        "history": history[-200:],
    }


def _next_state(
    state: dict[str, Any],
    selected_regions: list[str],
    registry_counts: list[dict[str, Any]],
    round_number: int,
    countries: list[str],
    result,
    report_path: Path,
    summary_path: Path,
    excel_path: Path,
) -> dict[str, Any]:
    index_by_country = {item["country"]: index for index, item in enumerate(registry_counts)}
    last_index = index_by_country[countries[-1]]
    next_index = last_index + 1
    cycles_completed = int(state.get("cycles_completed", 0))
    if next_index >= len(registry_counts):
        next_index = 0
        cycles_completed += 1

    history = list(state.get("history", []))
    history.append(
        {
            "round_number": round_number,
            "mode": "scan",
            "countries": countries,
            "run_id": str(result.scan_run.run_id),
            "candidate_count": result.scan_run.candidate_count,
            "review_count": result.scan_run.review_count,
            "report_path": str(report_path),
            "summary_path": str(summary_path),
            "excel_path": str(excel_path),
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    return {
        "version": 1,
        "round_number": round_number,
        "registry_item_index": next_index,
        "cycles_completed": cycles_completed,
        "registry_backed_countries": [item["country"] for item in registry_counts],
        "registry_backed_candidate_count": sum(
            int(item["candidate_count"]) for item in registry_counts
        ),
        "target_country_count": len(countries_for_regions(selected_regions)),
        "history": history[-200:],
    }


def _registry_candidate_total(registry_counts: list[dict[str, Any]]) -> int:
    return sum(int(item["candidate_count"]) for item in registry_counts)


def _curation_changed_candidate_pool(
    curation_result: EvidenceCurationRunResult | None,
) -> bool:
    if curation_result is None:
        return False
    return curation_result.accepted_count > 0 or curation_result.updated_count > 0


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _round_summary(
    round_number: int,
    timestamp: str,
    selected_regions: list[str],
    countries: list[str],
    target_countries: list[str],
    registry_counts: list[dict[str, Any]],
    result,
    derived_refresh: dict[str, Any],
    excel_path: Path,
    report_path: Path,
    improvements: list[str],
    batch_min: int,
    batch_max: int,
    curation_result: EvidenceCurationRunResult | None = None,
    discovery_result: EvidenceDiscoveryResult | None = None,
    progress_settlement: Any | None = None,
) -> dict[str, Any]:
    packets = []
    for packet in result.packets:
        first_evidence = packet.evidence[0] if packet.evidence else None
        packets.append(
            {
                "property_id": str(packet.entity.property_id),
                "property_name": packet.entity.property_name,
                "country": packet.entity.country,
                "city": packet.entity.city,
                "scene_type": packet.entity.scene_type,
                "coordinate_status": packet.entity.coordinate_status,
                "main_metric": first_evidence.field_value if first_evidence else "",
                "source_count": len({str(evidence.source_url) for evidence in packet.evidence}),
                "evidence_status": str(packet.conclusion.evidence_status),
                "value_class": str(packet.conclusion.value_class),
                "action_class": str(packet.conclusion.action_class),
                "recommended_solution": str(packet.conclusion.recommended_solution),
                "indoor_system_presence": str(packet.build_status.indoor_system_presence),
                "build_evidence_status": str(packet.build_status.build_evidence_status),
                "annual_visits_est": packet.scene.annual_visits_est,
                "busy_hour_traffic_gb": (
                    packet.demand.busy_hour_traffic_gb if packet.demand else None
                ),
                "review_next_actions": [item.next_action for item in packet.review_queue],
                "google_maps_link": packet.entity.google_maps_link,
            }
        )
    return {
        "mode": "scan",
        "scan_created": True,
        "round_number": round_number,
        "timestamp_utc": timestamp,
        "regions": selected_regions,
        "countries": countries,
        "run_id": str(result.scan_run.run_id),
        "status": result.scan_run.status,
        "storage_mode": result.storage_mode,
        "batch_target": {"min": batch_min, "max": batch_max},
        "candidate_count": result.scan_run.candidate_count,
        "review_count": result.scan_run.review_count,
        "target_country_count": len(target_countries),
        "registry_backed_country_count": len(registry_counts),
        "registry_backed_candidate_count": sum(
            int(item["candidate_count"]) for item in registry_counts
        ),
        "intake": _intake_summary(curation_result, discovery_result, progress_settlement),
        "persisted_counts": result.persisted_counts,
        "derived_refresh": derived_refresh,
        "persistent_files": {
            "sqlite_default": "outputs/isite2_dev.db",
            "excel": str(excel_path),
            "report": str(report_path),
        },
        "ui_paths": {
            "map": "/ui/",
            "geojson": "/map/properties",
            "scan_run": f"/scan-runs/{result.scan_run.run_id}",
            "country_maps": {country: f"/ui/?country={country}" for country in countries},
        },
        "packets": packets,
        "improvement_points": improvements,
    }


def _improvement_points(
    selected_regions: list[str],
    target_countries: list[str],
    registry_counts: list[dict[str, Any]],
    packets: list[Any],
    batch_min: int,
    batch_max: int,
    curation_result: EvidenceCurationRunResult | None = None,
    discovery_result: EvidenceDiscoveryResult | None = None,
    progress_settlement: Any | None = None,
) -> list[str]:
    backed_countries = {item["country"] for item in registry_counts}
    unseeded = [country for country in target_countries if country not in backed_countries]
    registry_candidate_count = sum(int(item["candidate_count"]) for item in registry_counts)
    points = [
        (
            f"Source registry coverage is {len(backed_countries)}/{len(target_countries)} "
            f"target countries across {', '.join(selected_regions)}, with "
            f"{registry_candidate_count} evidence-backed candidates available."
        )
    ]
    if discovery_result is not None:
        points.append(
            f"Discovery checked {discovery_result.discovered_count} evidence seeds: "
            f"{discovery_result.new_count} new, {discovery_result.changed_count} changed, "
            f"{discovery_result.duplicate_unchanged_count} unchanged duplicates suppressed."
        )
        if (
            discovery_result.known_property_skipped_count
            or discovery_result.known_url_skipped_count
            or discovery_result.firecrawl_requests_saved_estimate
        ):
            points.append(
                "Known-opportunity filtering skipped "
                f"{discovery_result.known_property_skipped_count} known properties and "
                f"{discovery_result.known_url_skipped_count} known URLs before fetch, "
                "saving an estimated "
                f"{discovery_result.firecrawl_requests_saved_estimate} provider requests."
            )
    if curation_result is not None:
        points.append(
            f"Evidence curation accepted {curation_result.accepted_count} new candidates, "
            f"updated {curation_result.updated_count} existing candidates, and sent "
            f"{curation_result.rejected_count} drafts to review at {curation_result.draft_path}."
        )
    if progress_settlement is not None:
        points.append(
            "Discovery progress settled "
            f"{len(progress_settlement.settled_groups)} country/scene/source cycles, "
            f"exhausted {len(progress_settlement.exhausted_groups)}, and blocked "
            f"{len(progress_settlement.blocked_groups)} on retriable/review issues."
        )
    if len(packets) < batch_min:
        points.append(
            f"This round has {len(packets)} candidates, below the {batch_min}-{batch_max} "
            "target; expand source_registry with objective field evidence before forcing larger "
            "batches."
        )
    elif len(packets) > batch_max:
        points.append(
            f"This round has {len(packets)} candidates, above the {batch_min}-{batch_max} "
            "target because one full-scope unit exceeded the cap; split future work by scene or "
            "city while preserving full-scan semantics inside each unit."
        )
    if unseeded:
        points.append(
            "Do not create map points for unseeded countries until official/map evidence is added; "
            f"next unseeded examples: {', '.join(unseeded[:10])}."
        )
    all_build_unknown = all(
        packet.build_status.build_evidence_status == "Unknown" for packet in packets
    )
    if packets and all_build_unknown:
        points.append(
            "Indoor build-status evidence is still Unknown for this round; add operator "
            "announcement and venue network-upgrade source queries as a separate evidence chain."
        )
    single_source = [
        packet.entity.property_name
        for packet in packets
        if len({str(evidence.source_url) for evidence in packet.evidence}) < 2
    ]
    if single_source:
        points.append(
            "Cross-check evidence before upgrading confidence for: "
            + ", ".join(single_source[:5])
            + "."
        )
    points.append(
        "Persist public-source fetch cache across runs so repeated scans do not refetch unchanged "
        "official pages."
    )
    return points


def _intake_summary(
    curation_result: EvidenceCurationRunResult | None,
    discovery_result: EvidenceDiscoveryResult | None,
    progress_settlement: Any | None = None,
) -> dict[str, Any]:
    if curation_result is None and discovery_result is None:
        return {
            "attempted": False,
            "accepted_count": 0,
            "updated_count": 0,
            "rejected_count": 0,
            "discovered_count": 0,
            "new_evidence_count": 0,
            "changed_evidence_count": 0,
            "duplicate_unchanged_count": 0,
            "searched_count": 0,
            "fetched_count": 0,
            "failed_count": 0,
            "firecrawl_search_count": 0,
            "firecrawl_fetch_count": 0,
            "firecrawl_credits_used": 0,
            "firecrawl_warnings": [],
            "known_property_skipped_count": 0,
            "known_url_skipped_count": 0,
            "possible_duplicate_review_count": 0,
            "new_opportunity_count": 0,
            "existing_property_evidence_update_count": 0,
            "firecrawl_requests_saved_estimate": 0,
            "overlay_path": None,
            "draft_path": None,
            "curation_run_id": None,
            "progress_settled_count": 0,
            "progress_exhausted_count": 0,
            "progress_blocked_count": 0,
        }
    discovery = discovery_result or EvidenceDiscoveryResult(
        attempted=False,
        discovered_count=0,
        new_count=0,
        changed_count=0,
        duplicate_unchanged_count=0,
    )
    return {
        "attempted": True,
        "accepted_count": curation_result.accepted_count if curation_result else 0,
        "updated_count": curation_result.updated_count if curation_result else 0,
        "rejected_count": curation_result.rejected_count if curation_result else 0,
        "discovered_count": discovery.discovered_count,
        "new_evidence_count": discovery.new_count,
        "changed_evidence_count": discovery.changed_count,
        "duplicate_unchanged_count": discovery.duplicate_unchanged_count,
        "searched_count": discovery.searched_count,
        "fetched_count": discovery.fetched_count,
        "failed_count": discovery.failed_count,
        "firecrawl_search_count": discovery.firecrawl_search_count,
        "firecrawl_fetch_count": discovery.firecrawl_fetch_count,
        "firecrawl_credits_used": discovery.firecrawl_credits_used,
        "firecrawl_warnings": discovery.firecrawl_warnings,
        "known_property_skipped_count": discovery.known_property_skipped_count,
        "known_url_skipped_count": discovery.known_url_skipped_count,
        "possible_duplicate_review_count": discovery.possible_duplicate_review_count,
        "new_opportunity_count": discovery.new_opportunity_count,
        "existing_property_evidence_update_count": (
            discovery.existing_property_evidence_update_count
        ),
        "firecrawl_requests_saved_estimate": discovery.firecrawl_requests_saved_estimate,
        "overlay_path": str(curation_result.overlay_path) if curation_result else None,
        "draft_path": str(curation_result.draft_path) if curation_result else None,
        "curation_run_id": curation_result.curation_run_id if curation_result else None,
        "progress_settled_count": (
            len(progress_settlement.settled_groups) if progress_settlement else 0
        ),
        "progress_exhausted_count": (
            len(progress_settlement.exhausted_groups) if progress_settlement else 0
        ),
        "progress_blocked_count": (
            len(progress_settlement.blocked_groups) if progress_settlement else 0
        ),
        "progress_exhausted_examples": (
            [
                {
                    "country": group["country"],
                    "scene_type": group["scene_type"],
                    "source_type": group["source_type"],
                }
                for group in progress_settlement.exhausted_groups[:10]
            ]
            if progress_settlement
            else []
        ),
    }


def _progress_group_stats(
    curation_result: EvidenceCurationRunResult | None,
) -> dict[tuple[str, str, str, str], dict[str, int]]:
    if curation_result is None:
        return {}
    stats: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for group in curation_result.progress_groups:
        key = (
            str(group.get("region", "")).casefold(),
            str(group.get("country", "")).casefold(),
            str(group.get("scene_type", "")).casefold(),
            str(group.get("source_type", "")).casefold(),
        )
        stats[key] = {
            "accepted_new_count": int(group.get("accepted_new_count", 0)),
            "updated_count": int(group.get("updated_count", 0)),
            "draft_review_count": int(group.get("draft_review_count", 0)),
        }
    return stats


def _render_report(summary: dict[str, Any]) -> str:
    rows = [
        (
            "| Property | Country | City | Scene | Main Metric | Value | Action | Build | "
            "Review Action |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for packet in summary["packets"]:
        review_action = "; ".join(packet["review_next_actions"]) or "-"
        rows.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in [
                    packet["property_name"],
                    packet["country"],
                    packet["city"],
                    packet["scene_type"],
                    packet["main_metric"],
                    packet["value_class"],
                    packet["action_class"],
                    packet["build_evidence_status"],
                    review_action,
                ]
            )
            + " |"
        )

    improvements = "\n".join(f"- {point}" for point in summary["improvement_points"])
    country_maps = "\n".join(
        f"- {country}: {path}" for country, path in summary["ui_paths"]["country_maps"].items()
    )
    firecrawl_warnings = "\n".join(
        f"- {warning}" for warning in summary["intake"]["firecrawl_warnings"][:10]
    ) or "- None"
    return (
        f"# Regional High-Value Building Scan Round {summary['round_number']}\n\n"
        f"- Mode: {summary['mode']}\n"
        f"- Regions: {', '.join(summary['regions'])}\n"
        f"- Countries: {', '.join(summary['countries']) or 'None'}\n"
        f"- Run ID: {summary['run_id']}\n"
        f"- Status: {summary['status']}\n"
        f"- Storage mode: {summary['storage_mode']}\n"
        f"- Batch target: {summary['batch_target']['min']}-{summary['batch_target']['max']}\n"
        f"- Candidates: {summary['candidate_count']}\n"
        f"- Review items: {summary['review_count']}\n"
        f"- Searches: {summary['intake']['searched_count']}\n"
        f"- Fetches: {summary['intake']['fetched_count']}\n"
        f"- Failed searches/fetches: {summary['intake']['failed_count']}\n"
        f"- Firecrawl searches: {summary['intake']['firecrawl_search_count']}\n"
        f"- Firecrawl fetches: {summary['intake']['firecrawl_fetch_count']}\n"
        f"- Firecrawl credits used: {summary['intake']['firecrawl_credits_used']}\n"
        f"- Discovery checked: {summary['intake']['discovered_count']}\n"
        f"- New evidence: {summary['intake']['new_evidence_count']}\n"
        f"- Changed evidence: {summary['intake']['changed_evidence_count']}\n"
        f"- Curation accepted: {summary['intake']['accepted_count']}\n"
        f"- Curation updated: {summary['intake']['updated_count']}\n"
        f"- Curation review: {summary['intake']['rejected_count']}\n"
        f"- Progress settled: {summary['intake']['progress_settled_count']}\n"
        f"- Progress exhausted: {summary['intake']['progress_exhausted_count']}\n"
        f"- Progress blocked: {summary['intake']['progress_blocked_count']}\n"
        f"- SQLite file: {summary['persistent_files']['sqlite_default']}\n"
        f"- Excel file: {summary['persistent_files']['excel']}\n"
        f"- UI map path: {summary['ui_paths']['map']}\n"
        f"- GeoJSON path: {summary['ui_paths']['geojson']}\n\n"
        "## Country Map Links\n\n"
        f"{country_maps}\n\n"
        "## Firecrawl Warnings\n\n"
        f"{firecrawl_warnings}\n\n"
        "## Candidate Results\n\n"
        + "\n".join(rows)
        + "\n\n"
        "## Improvement Points\n\n"
        f"{improvements}\n"
    )


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _batch_slug(countries: list[str]) -> str:
    slugs = [_slug(country) for country in countries[:3]]
    if len(countries) > 3:
        slugs.append(f"plus_{len(countries) - 3}")
    return "_".join(slugs) or "batch"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return slug or "country"
