from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import load_effective_source_registry
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.localized_search_strategy import build_localized_queries
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.public_structured_sources import (
    HARD_PRIMARY,
    CachedHttpClient,
    PublicStructuredEvidence,
    PublicStructuredTarget,
    adapter_from_source_id,
    is_hard_primary_evidence,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_PATH = ROOT / "outputs" / "isite2_dev.db"
DB_URL = f"sqlite+pysqlite:///{DB_PATH}"
OUTPUT_DIR = ROOT / "outputs" / "regional_scan_loop"
CACHE_DIR = OUTPUT_DIR / "public_structured_source_cache"
SOURCE_TYPE = "free_structured_evidence_backfill"

DEFAULT_SOURCES = [
    "wikipedia_mediawiki_api",
    "ourairports_csv",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run free structured evidence adapters for existing active candidates. "
            "Firecrawl is never called by this script."
        )
    )
    parser.add_argument("--countries", default="")
    parser.add_argument("--scenes", default="")
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    parser.add_argument(
        "--target-source",
        choices=["active", "overlay", "both"],
        default="active",
    )
    parser.add_argument("--max-properties", type=int, default=40)
    parser.add_argument("--max-per-country-scene", type=int, default=5)
    parser.add_argument("--target-offset", type=int, default=0)
    parser.add_argument("--adapter-timeout", type=int, default=30)
    parser.add_argument("--execute-free-sources", action="store_true")
    parser.add_argument("--include-identity-evidence", action="store_true")
    parser.add_argument("--skip-curation", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    countries = _split(args.countries)
    scenes = _split(args.scenes)
    sources = _split(args.sources) or DEFAULT_SOURCES
    active_run_id = _active_scan_run_id()
    registry = load_effective_source_registry()
    targets = _select_targets(
        target_source=args.target_source,
        registry=registry,
        countries=countries,
        scenes=scenes,
        max_properties=args.max_properties,
        max_per_country_scene=args.max_per_country_scene,
        target_offset=args.target_offset,
    )
    client = CachedHttpClient(CACHE_DIR)
    adapters = [
        adapter_from_source_id(source_id, client, timeout=args.adapter_timeout)
        for source_id in sources
    ]
    store = EvidenceCurationStore(database_url=DB_URL)
    source_cache = store.source_cache()

    collected: list[PublicStructuredEvidence] = []
    errors: list[dict[str, Any]] = []
    raw_ids: list[str] = []
    written_or_changed = 0
    duplicate_unchanged = 0
    hard_by_property: set[str] = set()

    for target in targets:
        for adapter in adapters:
            try:
                evidences = adapter.collect(target)
            except Exception as exc:  # noqa: BLE001 - report adapter failures, keep batch moving.
                errors.append(
                    {
                        "property_id": target.property_id,
                        "property_name": target.property_name,
                        "country": target.country,
                        "scene_type": target.scene_type,
                        "source_id": getattr(adapter, "source_id", adapter.__class__.__name__),
                        "error": str(exc),
                    }
                )
                continue
            for evidence in evidences:
                collected.append(evidence)
                source_cache.put(evidence.as_page())
                if evidence.is_primary_metric:
                    hard_by_property.add(evidence.target.property_id)
                if not args.execute_free_sources:
                    continue
                if not evidence.is_primary_metric and not args.include_identity_evidence:
                    continue
                draft = evidence.to_candidate_draft(registry=registry)
                result = store.upsert_candidate_evidence(
                    draft,
                    content_text=evidence.content_text,
                    source_type=evidence.source_id,
                )
                raw_ids.append(result.raw_evidence_id)
                if result.is_new_evidence or result.is_changed_evidence:
                    written_or_changed += 1
                if result.duplicate_unchanged:
                    duplicate_unchanged += 1

    curation = None
    sync = None
    if args.execute_free_sources and not args.skip_curation:
        curation = run_pending_evidence_curation(store=store, output_dir=OUTPUT_DIR)
        if curation and not args.skip_sync:
            repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
            sync = sync_overlay_to_active_repository(repository)

    firecrawl_gap_list = _firecrawl_gap_list(targets, hard_by_property)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    gap_path = OUTPUT_DIR / f"firecrawl_gap_list_after_free_structured_{timestamp}.json"
    gap_path.write_text(
        json.dumps(firecrawl_gap_list, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "mode": SOURCE_TYPE,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "active_scan_run_id": active_run_id,
        "execute_free_sources": args.execute_free_sources,
        "firecrawl_calls_executed": 0,
        "target_count": len(targets),
        "target_source": args.target_source,
        "target_offset": args.target_offset,
        "sources": sources,
        "adapter_timeout": args.adapter_timeout,
        "collected_evidence_count": len(collected),
        "collected_primary_metric_count": sum(1 for item in collected if item.is_primary_metric),
        "collected_identity_count": sum(1 for item in collected if not item.is_primary_metric),
        "collected_by_source": dict(Counter(item.source_id for item in collected)),
        "collected_by_scene": dict(Counter(item.target.scene_type for item in collected)),
        "raw_evidence_written_or_changed": written_or_changed,
        "raw_evidence_duplicate_unchanged": duplicate_unchanged,
        "adapter_error_count": len(errors),
        "adapter_errors": errors[:40],
        "firecrawl_gap_count": len(firecrawl_gap_list),
        "firecrawl_gap_path": str(gap_path),
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
        "sample_evidence": [
            {
                "country": item.target.country,
                "city": item.target.city,
                "property_name": item.target.property_name,
                "scene_type": item.target.scene_type,
                "source_id": item.source_id,
                "field_group": item.field_group,
                "field_value": item.field_value,
                "source_url": item.source_url,
                "is_primary_metric": item.is_primary_metric,
            }
            for item in collected[:30]
        ],
        "raw_evidence_ids": raw_ids,
    }
    summary_path = OUTPUT_DIR / f"free_structured_evidence_backfill_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(_console_summary(summary), ensure_ascii=False, indent=2))


def _active_targets(
    *,
    countries: list[str],
    scenes: list[str],
    max_properties: int,
    max_per_country_scene: int,
    target_offset: int,
    missing_hard_metric_first: bool,
) -> list[PublicStructuredTarget]:
    active_run_id = _active_scan_run_id()
    if not active_run_id:
        return []
    country_filter = ""
    scene_filter = ""
    params: list[Any] = [active_run_id]
    if countries:
        country_filter = f"and sc.country in ({','.join('?' for _ in countries)})"
        params.extend(countries)
    if scenes:
        scene_filter = f"and sc.scene_type in ({','.join('?' for _ in scenes)})"
        params.extend(scenes)
    query = f"""
        select
          p.id, p.country, p.city, p.canonical_name, p.scene_type,
          p.latitude, p.longitude, p.geocode_precision, p.map_source,
          p.map_source_date, p.hero_image, sm.annual_visits_est
        from scan_candidates sc
        join properties p on p.id = sc.property_id
        left join scene_model_results sm
          on sm.property_id = p.id and sm.scan_run_id = sc.scan_run_id
        where sc.scan_run_id = ?
          and coalesce(sc.candidate_quality_status, 'ready') != 'blocked'
          {country_filter}
          {scene_filter}
        order by p.country, p.scene_type, p.canonical_name
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        evidence_rows = conn.execute(
            "select property_id, field_group from evidence_items"
        ).fetchall()
    hard_by_property: dict[str, set[str]] = defaultdict(set)
    for property_id, field_group in evidence_rows:
        hard_by_property[property_id].add(field_group)

    target_rows: list[tuple[PublicStructuredTarget, bool]] = []
    for row in rows:
        scene_type = row["scene_type"]
        hard_fields = set(HARD_PRIMARY.get(scene_type, []))
        has_hard = bool(hard_fields & hard_by_property.get(row["id"], set()))
        hero_image = json.loads(row["hero_image"]) if row.get("hero_image") else None
        target_rows.append(
            (
                PublicStructuredTarget(
                    property_id=row["id"],
                    country=row["country"],
                    city=row["city"] or "",
                    property_name=row["canonical_name"],
                    scene_type=scene_type,
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    geocode_precision=row["geocode_precision"] or "",
                    map_source=row["map_source"] or "",
                    map_source_date=row["map_source_date"] or "",
                    annual_visits_est=float(row["annual_visits_est"] or 1),
                    hero_image=hero_image,
                ),
                has_hard,
            )
        )

    if missing_hard_metric_first:
        target_rows.sort(
            key=lambda item: (
                item[1],
                item[0].country,
                item[0].scene_type,
                item[0].property_name,
            )
        )
    targets = [target for target, _has_hard in target_rows]
    return _balanced_sample(
        targets,
        max_properties=max_properties,
        max_per_country_scene=max_per_country_scene,
        target_offset=target_offset,
    )


def _overlay_targets(
    *,
    registry: dict[str, Any],
    countries: list[str],
    scenes: list[str],
) -> list[PublicStructuredTarget]:
    targets: list[PublicStructuredTarget] = []
    country_filter = {country.casefold() for country in countries}
    scene_filter = {scene.casefold() for scene in scenes}
    for country, country_registry in (registry.get("countries") or {}).items():
        if country_filter and country.casefold() not in country_filter:
            continue
        for candidate in country_registry.get("candidates") or []:
            scene_type = str(candidate.get("scene_type") or "")
            if scene_filter and scene_type.casefold() not in scene_filter:
                continue
            coordinate = candidate.get("coordinate") or {}
            if not coordinate.get("latitude") or not coordinate.get("longitude"):
                continue
            city = str(candidate.get("city") or "").strip()
            geocode_precision = str(coordinate.get("geocode_precision") or "").strip()
            if not city or city.casefold() == country.casefold() or not geocode_precision:
                continue
            targets.append(
                PublicStructuredTarget(
                    property_id=str(
                        candidate.get("property_identity_key")
                        or f"overlay:{country}:{scene_type}:{candidate.get('property_name')}"
                    ),
                    country=country,
                    city=city,
                    property_name=str(candidate.get("property_name") or ""),
                    scene_type=scene_type,
                    latitude=float(coordinate["latitude"]),
                    longitude=float(coordinate["longitude"]),
                    geocode_precision=geocode_precision,
                    map_source=str(coordinate.get("map_source") or "overlay coordinate"),
                    map_source_date=str(coordinate.get("map_source_date") or ""),
                    annual_visits_est=float(candidate.get("annual_visits") or 1),
                    hero_image=candidate.get("hero_image"),
                )
            )
    return [target for target in targets if target.property_name and target.scene_type]


def _select_targets(
    *,
    target_source: str,
    registry: dict[str, Any],
    countries: list[str],
    scenes: list[str],
    max_properties: int,
    max_per_country_scene: int,
    target_offset: int,
) -> list[PublicStructuredTarget]:
    targets: list[PublicStructuredTarget] = []
    if target_source in {"active", "both"}:
        targets.extend(
            _active_targets(
                countries=countries,
                scenes=scenes,
                max_properties=10_000,
                max_per_country_scene=10_000,
                target_offset=0,
                missing_hard_metric_first=True,
            )
        )
    if target_source in {"overlay", "both"}:
        targets.extend(_overlay_targets(registry=registry, countries=countries, scenes=scenes))

    seen: set[tuple[str, str, str]] = set()
    deduped: list[PublicStructuredTarget] = []
    for target in targets:
        key = (
            target.country.casefold(),
            target.scene_type.casefold(),
            target.property_name.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return _balanced_sample(
        deduped,
        max_properties=max_properties,
        max_per_country_scene=max_per_country_scene,
        target_offset=target_offset,
    )


def _balanced_sample(
    targets: list[PublicStructuredTarget],
    *,
    max_properties: int,
    max_per_country_scene: int,
    target_offset: int,
) -> list[PublicStructuredTarget]:
    buckets: dict[tuple[str, str], list[PublicStructuredTarget]] = defaultdict(list)
    for target in targets:
        key = (target.country, target.scene_type)
        if len(buckets[key]) < max(1, max_per_country_scene):
            buckets[key].append(target)
    ordered: list[PublicStructuredTarget] = []
    keys = sorted(buckets)
    index = 0
    while True:
        added = False
        for key in keys:
            bucket = buckets[key]
            if index >= len(bucket):
                continue
            ordered.append(bucket[index])
            added = True
        if not added:
            break
        index += 1
    offset = max(0, target_offset)
    return ordered[offset : offset + max_properties]


def _firecrawl_gap_list(
    targets: list[PublicStructuredTarget],
    hard_by_property: set[str],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for target in targets:
        if target.property_id in hard_by_property:
            continue
        queries = build_localized_queries(
            country=target.country,
            city=target.city,
            property_name=target.property_name,
            scene_type=target.scene_type,
            phases=["firecrawl_gap_fill"],
        )
        gaps.append(
            {
                "property_id": target.property_id,
                "country": target.country,
                "city": target.city,
                "property_name": target.property_name,
                "scene_type": target.scene_type,
                "required_indicators": HARD_PRIMARY.get(target.scene_type, []),
                "firecrawl_queries": [query.as_dict() for query in queries],
            }
        )
    return gaps


def _active_scan_run_id() -> str | None:
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            select scan_run_id
            from scan_candidates
            group by scan_run_id
            order by count(*) desc
            limit 1
            """
        ).fetchone()
    return str(row[0]) if row else None


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Free Structured Evidence Backfill",
        "",
        f"- Timestamp UTC: {summary['timestamp_utc']}",
        f"- Active scan run: {summary['active_scan_run_id']}",
        f"- Execute mode: {summary['execute_free_sources']}",
        "- Firecrawl calls executed: 0",
        f"- Target properties: {summary['target_count']}",
        f"- Sources: {', '.join(summary['sources'])}",
        f"- Collected evidence: {summary['collected_evidence_count']}",
        f"- Collected primary metrics: {summary['collected_primary_metric_count']}",
        f"- Collected identity evidence: {summary['collected_identity_count']}",
        f"- Raw evidence written/changed: {summary['raw_evidence_written_or_changed']}",
        f"- Adapter errors: {summary['adapter_error_count']}",
        f"- Firecrawl gap count after free pass: {summary['firecrawl_gap_count']}",
        f"- Firecrawl gap list: {summary['firecrawl_gap_path']}",
        "",
        "## Evidence By Source",
    ]
    for source_id, count in sorted(summary["collected_by_source"].items()):
        lines.append(f"- {source_id}: {count}")
    lines.extend(["", "## Evidence By Scene"])
    for scene_type, count in sorted(summary["collected_by_scene"].items()):
        lines.append(f"- {scene_type}: {count}")
    if summary.get("curation"):
        curation = summary["curation"]
        lines.extend(
            [
                "",
                "## Curation",
                f"- New evidence: {curation['new_evidence_count']}",
                f"- Accepted new: {curation['accepted_count']}",
                f"- Updated existing: {curation['updated_count']}",
                f"- Rejected: {curation['rejected_count']}",
                f"- Report: {curation['report_path']}",
            ]
        )
    return "\n".join(lines) + "\n"


def _console_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": summary["mode"],
        "execute_free_sources": summary["execute_free_sources"],
        "firecrawl_calls_executed": summary["firecrawl_calls_executed"],
        "target_count": summary["target_count"],
        "collected_evidence_count": summary["collected_evidence_count"],
        "collected_primary_metric_count": summary["collected_primary_metric_count"],
        "raw_evidence_written_or_changed": summary["raw_evidence_written_or_changed"],
        "firecrawl_gap_count": summary["firecrawl_gap_count"],
        "summary_path": summary["summary_path"],
        "report_path": summary["report_path"],
    }


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
