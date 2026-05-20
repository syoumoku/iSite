from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from isite2.connectors.extraction import extract_indicators
from isite2.connectors.firecrawl import FirecrawlPublicEvidenceProvider, firecrawl_subprocess_env
from isite2.connectors.models import FetchedPage, SearchResult
from isite2.domain.enums import SourceTier
from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import CandidateDraft, load_effective_source_registry
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import KNOWN_PROPERTY
from isite2.growth.regional_targets import REGION_COUNTRIES
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository
from isite2.rules.config_loader import scene_definitions
from isite2.rules.metric_period import annual_metric_period_issue

DB_PATH = Path("outputs/isite2_dev.db")
DB_URL = f"sqlite+pysqlite:///{DB_PATH}"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
FIRECRAWL_DIR = Path(".firecrawl") / "full_evidence_backfill_20260513"
SOURCE_TYPE = "firecrawl_full_evidence_backfill"
TODAY = "2026-05-13"
LOW_EVIDENCE_LABEL = "low_primary_metric_evidence"
HARD_EVIDENCE_LABEL = "hard_primary_metric_available"
AUDIT_EVENT_VERSION = 1

HARD_PRIMARY: dict[str, list[str]] = {
    "airport_terminal": [
        "annual_passenger_throughput",
        "passenger_throughput",
        "terminal_capacity",
        "international_passenger_share",
    ],
    "convention_center": [
        "exhibition_area",
        "meeting_area",
        "annual_events",
        "international_event_frequency",
        "peak_event_capacity",
        "plenary_capacity",
    ],
    "stadium": ["seat_count", "event_days", "international_events", "peak_event_capacity"],
    "luxury_hotel_mice": ["keys", "rooms", "meeting_ballroom_area", "ballroom_capacity"],
    "mall_mixed_use": ["gla", "annual_footfall", "footfall"],
    "office_government": ["office_nla", "office_gfa"],
    "hospital": ["beds", "outpatient_volume"],
    "university": ["enrollment", "students", "student_count", "campus_population"],
    "transport_hub": ["daily_ridership", "ridership", "interchange_volume", "line_count"],
    "cruise_port": ["passenger_throughput", "annual_passenger_throughput"],
}

QUERY_TEMPLATES: dict[str, list[str]] = {
    "airport_terminal": [
        "{property_name} {country} annual passenger traffic official",
        "{property_name} passenger throughput airport statistics",
    ],
    "stadium": [
        "{property_name} seat capacity official",
        "{property_name} stadium capacity",
    ],
    "convention_center": [
        "{property_name} exhibition area capacity official",
        "{property_name} venue details exhibition space",
    ],
    "mall_mixed_use": [
        "{property_name} gross leasable area GLA",
        "{property_name} annual footfall visitors",
    ],
    "luxury_hotel_mice": [
        "{property_name} number of rooms official",
        "{property_name} rooms keys meeting space",
    ],
    "office_government": [
        "{property_name} office GFA NLA square meters",
        "{property_name} gross floor area office",
    ],
    "hospital": [
        "{property_name} beds official",
        "{property_name} outpatient visits official",
    ],
    "university": [
        "{property_name} enrollment students official",
        "{property_name} student count",
    ],
    "transport_hub": [
        "{property_name} daily ridership passengers",
        "{property_name} line count platforms",
    ],
}

SCENE_PRIORITY = {
    "airport_terminal": 0,
    "stadium": 1,
    "convention_center": 2,
    "mall_mixed_use": 3,
    "luxury_hotel_mice": 4,
    "transport_hub": 5,
    "office_government": 6,
    "hospital": 7,
    "university": 8,
    "cruise_port": 9,
}


@dataclass
class AuditLog:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, **payload: object) -> dict:
        event = {
            "event_version": AUDIT_EVENT_VERSION,
            "event_type": event_type,
            "source_type": SOURCE_TYPE,
            "logged_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


@dataclass
class AuditState:
    completed_query_keys: set[str] = field(default_factory=set)
    accepted_query_keys: set[str] = field(default_factory=set)
    failed_query_keys: set[str] = field(default_factory=set)
    accepted_property_ids: set[str] = field(default_factory=set)
    search_results_by_query: dict[str, list[dict]] = field(default_factory=dict)
    fetched_pages_by_url: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_event_paths(cls, paths: list[Path]) -> "AuditState":
        state = cls()
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                state.ingest(event)
        return state

    def ingest(self, event: dict) -> None:
        event_type = str(event.get("event_type") or "")
        query_key = str(event.get("query_key") or "")
        if event_type == "search_completed" and query_key:
            results = event.get("results")
            if isinstance(results, list):
                self.search_results_by_query[query_key] = [
                    item for item in results if isinstance(item, dict)
                ]
        elif event_type == "search_failed" and query_key:
            self.failed_query_keys.add(query_key)
        elif event_type == "query_completed" and query_key:
            self.completed_query_keys.add(query_key)
            if event.get("accepted") is True:
                self.accepted_query_keys.add(query_key)
                property_id = str(event.get("property_id") or "")
                if property_id:
                    self.accepted_property_ids.add(property_id)
        elif event_type == "evidence_written" and query_key:
            self.completed_query_keys.add(query_key)
            self.accepted_query_keys.add(query_key)
            property_id = str(event.get("property_id") or "")
            if property_id:
                self.accepted_property_ids.add(property_id)
        elif event_type == "evidence_retracted" and query_key:
            self.completed_query_keys.discard(query_key)
            self.accepted_query_keys.discard(query_key)
            property_id = str(event.get("property_id") or "")
            if property_id:
                self.accepted_property_ids.discard(property_id)
        elif event_type == "fetch_completed":
            url = str(event.get("url") or event.get("source_url") or "")
            if url:
                self.fetched_pages_by_url[_url_key(url)] = event

    def query_terminal(self, query_key: str, *, retry_failures: bool = False) -> bool:
        if query_key in self.completed_query_keys or query_key in self.accepted_query_keys:
            return True
        return query_key in self.failed_query_keys and not retry_failures

    def query_accepted(self, query_key: str) -> bool:
        return query_key in self.accepted_query_keys

    def logged_search_results(self, query_key: str) -> list[dict] | None:
        return self.search_results_by_query.get(query_key)

    def logged_page(self, url: str) -> dict | None:
        return self.fetched_pages_by_url.get(_url_key(url))


def main() -> None:
    args = _parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIRECRAWL_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    audit_log_path = (
        Path(args.audit_log_path)
        if args.audit_log_path
        else FIRECRAWL_DIR / f"{SOURCE_TYPE}_{run_id}_events.jsonl"
    )
    audit = AuditLog(audit_log_path)
    resume_paths = [] if args.no_resume or args.force_retry else _audit_event_paths(FIRECRAWL_DIR)
    state = AuditState.from_event_paths(resume_paths)
    pages_dir = FIRECRAWL_DIR / "pages"
    countries = _split_filter(args.countries)
    scenes = _split_filter(args.scenes)
    before = evidence_gap_summary()
    targets = _select_targets(
        _target_pool(
            mode=args.target_mode,
            countries=countries,
            scenes=scenes,
            min_distinct_source_urls=args.min_distinct_source_urls,
        ),
        args.max_properties,
    )
    store = EvidenceCurationStore(database_url=DB_URL)
    registry = load_effective_source_registry()
    drafts: list[CandidateDraft] = []
    raw_ids = []
    written_or_changed = 0
    start_remaining = _remaining_firecrawl_credits()
    budget_stop = False
    terminal_query_skips = 0
    reused_search_result_sets = 0
    reused_pages = 0
    fetched_pages = 0
    extraction_rejections = 0
    fetch_failures = 0
    provider = FirecrawlPublicEvidenceProvider(
        enabled=True,
        scrape_search_results=True,
        timeout_ms=args.timeout_ms,
        max_attempts=1,
    )
    audit.append(
        "run_started",
        run_id=run_id,
        max_searches=args.max_searches,
        max_properties=args.max_properties,
        target_mode=args.target_mode,
        countries=sorted(countries),
        scenes=sorted(scenes),
        min_distinct_source_urls=args.min_distinct_source_urls,
        result_limit=args.result_limit,
        credit_budget=args.credit_budget,
        credit_start_remaining=start_remaining,
        resume_enabled=not args.no_resume and not args.force_retry,
        resume_event_paths=[str(path) for path in resume_paths],
    )

    searches_used = 0
    for prop in targets:
        if budget_stop:
            break
        if prop["id"] in state.accepted_property_ids and not args.force_retry:
            audit.append(
                "property_skipped_existing_audit_evidence",
                run_id=run_id,
                property_id=prop["id"],
                property_name=prop["canonical_name"],
                country=prop["country"],
                scene_type=prop["scene_type"],
            )
            continue
        found = False
        for query in _queries_for_property(prop):
            if found or budget_stop:
                break
            query_key = _query_key(prop, query)
            if state.query_accepted(query_key) and not args.force_retry:
                terminal_query_skips += 1
                found = True
                audit.append(
                    "query_skipped_terminal",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    query=query,
                    reason="accepted_evidence_already_logged",
                )
                continue
            if state.query_terminal(query_key, retry_failures=args.retry_failures) and not args.force_retry:
                terminal_query_skips += 1
                audit.append(
                    "query_skipped_terminal",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    query=query,
                    reason="query_already_completed_or_failed",
                )
                continue

            logged_results = None if args.force_retry else state.logged_search_results(query_key)
            if logged_results is not None:
                results = _search_results_from_payloads(logged_results)
                reused_search_result_sets += 1
                audit.append(
                    "search_reused_from_audit",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    query=query,
                    result_count=len(results),
                )
            else:
                if searches_used >= args.max_searches:
                    break
                if _budget_exhausted(
                    start_remaining=start_remaining,
                    budget=args.credit_budget,
                    request_count=provider.stats.search_requests + provider.stats.scrape_requests,
                    check_interval=1,
                ):
                    budget_stop = True
                    audit.append(
                        "budget_stop_before_search",
                        run_id=run_id,
                        query_key=query_key,
                        property_id=prop["id"],
                        property_name=prop["canonical_name"],
                        query=query,
                        credit_budget=args.credit_budget,
                        credit_start_remaining=start_remaining,
                        credit_remaining=_remaining_firecrawl_credits(),
                    )
                    break
                audit.append(
                    "search_started",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    country=prop["country"],
                    scene_type=prop["scene_type"],
                    query=query,
                    result_limit=args.result_limit,
                )
                try:
                    results = provider.search(
                        query,
                        limit=args.result_limit,
                        country="",
                        location=prop["country"],
                    )
                except Exception as exc:  # pragma: no cover - live provider guardrail
                    event = audit.append(
                        "search_failed",
                        run_id=run_id,
                        query_key=query_key,
                        property_id=prop["id"],
                        property_name=prop["canonical_name"],
                        country=prop["country"],
                        scene_type=prop["scene_type"],
                        query=query,
                        error=str(exc),
                    )
                    state.ingest(event)
                    continue
                searches_used += 1
                event = audit.append(
                    "search_completed",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    country=prop["country"],
                    scene_type=prop["scene_type"],
                    query=query,
                    result_count=len(results),
                    results=[_search_result_payload(result) for result in results],
                    firecrawl_stats=_provider_stats(provider),
                )
                state.ingest(event)
                if _budget_exhausted(
                    start_remaining=start_remaining,
                    budget=args.credit_budget,
                    request_count=provider.stats.search_requests + provider.stats.scrape_requests,
                    check_interval=args.credit_check_interval,
                ):
                    budget_stop = True
                    audit.append(
                        "budget_stop_after_search",
                        run_id=run_id,
                        query_key=query_key,
                        property_id=prop["id"],
                        property_name=prop["canonical_name"],
                        query=query,
                        credit_budget=args.credit_budget,
                        credit_start_remaining=start_remaining,
                        credit_remaining=_remaining_firecrawl_credits(),
                    )
            for result in results:
                if budget_stop:
                    break
                page = None
                logged_page = None if args.force_retry else state.logged_page(str(result.url))
                if logged_page is not None:
                    page = _page_from_logged_event(logged_page)
                    if page is not None:
                        reused_pages += 1
                        audit.append(
                            "fetch_reused_from_audit",
                            run_id=run_id,
                            query_key=query_key,
                            property_id=prop["id"],
                            property_name=prop["canonical_name"],
                            query=query,
                            url=str(result.url),
                            content_file=logged_page.get("content_file"),
                        )
                if page is None:
                    if _budget_exhausted(
                        start_remaining=start_remaining,
                        budget=args.credit_budget,
                        request_count=provider.stats.search_requests + provider.stats.scrape_requests,
                        check_interval=1,
                    ):
                        budget_stop = True
                        audit.append(
                            "budget_stop_before_fetch",
                            run_id=run_id,
                            query_key=query_key,
                            property_id=prop["id"],
                            property_name=prop["canonical_name"],
                            query=query,
                            url=str(result.url),
                            credit_budget=args.credit_budget,
                            credit_start_remaining=start_remaining,
                            credit_remaining=_remaining_firecrawl_credits(),
                        )
                        break
                    audit.append(
                        "fetch_started",
                        run_id=run_id,
                        query_key=query_key,
                        property_id=prop["id"],
                        property_name=prop["canonical_name"],
                        query=query,
                        url=str(result.url),
                        title=result.title,
                    )
                    try:
                        page = provider.fetch_page(str(result.url))
                    except Exception as exc:  # pragma: no cover - live provider guardrail
                        fetch_failures += 1
                        audit.append(
                            "fetch_failed",
                            run_id=run_id,
                            query_key=query_key,
                            property_id=prop["id"],
                            property_name=prop["canonical_name"],
                            query=query,
                            url=str(result.url),
                            error=str(exc),
                        )
                        continue
                    fetched_pages += 1
                    event = audit.append(
                        "fetch_completed",
                        run_id=run_id,
                        query_key=query_key,
                        property_id=prop["id"],
                        property_name=prop["canonical_name"],
                        query=query,
                        url=str(result.url),
                        **_persist_fetched_page(page, pages_dir),
                        firecrawl_stats=_provider_stats(provider),
                    )
                    state.ingest(event)
                extraction = _best_extraction(prop, page)
                if extraction is None:
                    extraction_rejections += 1
                    audit.append(
                        "extraction_rejected",
                        run_id=run_id,
                        query_key=query_key,
                        property_id=prop["id"],
                        property_name=prop["canonical_name"],
                        query=query,
                        url=str(result.url),
                        source_url=str(page.source_url),
                        reason="no_plausible_hard_primary_metric",
                    )
                    continue
                draft = _draft_for_existing(prop, extraction, page.content_text, registry)
                drafts.append(draft)
                write_result = store.upsert_candidate_evidence(draft, source_type=SOURCE_TYPE)
                raw_ids.append(write_result.raw_evidence_id)
                if write_result.is_new_evidence or write_result.is_changed_evidence:
                    written_or_changed += 1
                event = audit.append(
                    "evidence_written",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    country=prop["country"],
                    scene_type=prop["scene_type"],
                    query=query,
                    url=draft.source_url,
                    raw_evidence_id=write_result.raw_evidence_id,
                    write_status=write_result.status,
                    is_new_evidence=write_result.is_new_evidence,
                    is_changed_evidence=write_result.is_changed_evidence,
                    duplicate_unchanged=write_result.duplicate_unchanged,
                    field_group=draft.field_group,
                    field_value=draft.field_value,
                )
                state.ingest(event)
                event = audit.append(
                    "query_completed",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    query=query,
                    accepted=True,
                    accepted_url=draft.source_url,
                    raw_evidence_id=write_result.raw_evidence_id,
                )
                state.ingest(event)
                found = True
                break
            if not found and not budget_stop:
                event = audit.append(
                    "query_completed",
                    run_id=run_id,
                    query_key=query_key,
                    property_id=prop["id"],
                    property_name=prop["canonical_name"],
                    query=query,
                    accepted=False,
                    result_count=len(results),
                )
                state.ingest(event)

    curation = None
    sync = None
    if drafts and not args.no_sync:
        curation = run_pending_evidence_curation(store=store, output_dir=OUTPUT_DIR)
        repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
        sync = sync_overlay_to_active_repository(repository)

    labels = apply_low_evidence_labels(
        scan_run_id=str(sync.run_id) if sync and sync.run_id else None
    )
    after = evidence_gap_summary()
    summary = {
        "mode": SOURCE_TYPE,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "max_searches": args.max_searches,
        "target_mode": args.target_mode,
        "target_countries": sorted(countries),
        "target_scenes": sorted(scenes),
        "min_distinct_source_urls": args.min_distinct_source_urls,
        "searches_used": searches_used,
        "budget_stop": budget_stop,
        "credit_budget": args.credit_budget,
        "credit_start_remaining": start_remaining,
        "credit_end_remaining": _remaining_firecrawl_credits(),
        "audit_log_path": str(audit_log_path),
        "resume_event_paths": [str(path) for path in resume_paths],
        "terminal_query_skips": terminal_query_skips,
        "reused_search_result_sets": reused_search_result_sets,
        "reused_pages": reused_pages,
        "fetched_pages_logged": fetched_pages,
        "fetch_failures": fetch_failures,
        "extraction_rejections": extraction_rejections,
        "firecrawl_provider_stats": {
            "search_requests": provider.stats.search_requests,
            "scrape_requests": provider.stats.scrape_requests,
            "credits_used_reported_by_api": provider.stats.credits_used,
            "warnings": provider.stats.warnings,
        },
        "target_count": len(targets),
        "draft_count": len(drafts),
        "drafts_by_scene": dict(Counter(d.scene_type for d in drafts)),
        "raw_evidence_written_or_changed": written_or_changed,
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
            "blocked_candidate_count": sync.blocked_candidate_count,
            "skipped_reason": sync.skipped_reason,
            "derived_refresh": sync.derived_refresh,
        },
        "labels": labels,
        "gap_summary_before": before,
        "gap_summary_after": after,
        "search_log_path": str(audit_log_path),
        "raw_evidence_ids": raw_ids,
    }
    summary_path = OUTPUT_DIR / f"{SOURCE_TYPE}_{run_id}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    audit.append(
        "run_finished",
        run_id=run_id,
        summary_path=str(summary_path),
        report_path=str(report_path),
        searches_used=searches_used,
        budget_stop=budget_stop,
        raw_evidence_ids=raw_ids,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def evidence_gap_summary() -> dict:
    properties = _properties_with_evidence()
    totals: Counter[str] = Counter()
    hard: Counter[str] = Counter()
    weak: Counter[str] = Counter()
    for prop in properties:
        scene = prop["scene_type"]
        totals[scene] += 1
        if _has_hard_primary(prop["evidence"], scene):
            hard[scene] += 1
        else:
            weak[scene] += 1
    return {
        "total_properties": len(properties),
        "scene_summary": {
            scene: {
                "total": totals[scene],
                "hard_primary_metric_properties": hard[scene],
                "low_primary_metric_evidence_properties": weak[scene],
            }
            for scene in sorted(totals)
        },
        "low_primary_metric_total": sum(weak.values()),
    }


def apply_low_evidence_labels(scan_run_id: str | None) -> dict:
    properties = _properties_with_evidence(scan_run_id=scan_run_id)
    scenes = scene_definitions()
    weak_ids = []
    hard_ids = []
    primary_by_scene = {
        scene: list(config.get("primary_indicators", [])) for scene, config in scenes.items()
    }
    for prop in properties:
        if _has_hard_primary(prop["evidence"], prop["scene_type"]):
            hard_ids.append(prop["id"])
        else:
            weak_ids.append(prop["id"])
    now = datetime.now(UTC).isoformat()
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("DELETE FROM review_queue WHERE review_type = ?", (LOW_EVIDENCE_LABEL,))
        for prop in properties:
            label = HARD_EVIDENCE_LABEL if prop["id"] in hard_ids else LOW_EVIDENCE_LABEL
            connection.execute(
                """
                UPDATE scene_model_results
                SET metric_availability_level = ?,
                    primary_value_indicators = ?
                WHERE property_id = ?
                  AND (? IS NULL OR scan_run_id = ?)
                """,
                (
                    label,
                    json.dumps(primary_by_scene.get(prop["scene_type"], []), ensure_ascii=False),
                    prop["id"],
                    scan_run_id,
                    scan_run_id,
                ),
            )
        for prop in properties:
            if prop["id"] not in weak_ids:
                continue
            connection.execute(
                """
                INSERT INTO review_queue (
                    id, property_id, scan_run_id, reason, next_action, status,
                    review_type, severity, gate_name, field_path, blocking_surfaces,
                    source_url, suggested_query, owner, created_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, 'open', ?, 'medium', ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    str(uuid4()),
                    prop["id"],
                    prop["scan_run_id"],
                    "低证据标签：未找到场景硬主指标证据；描述性角色证据不计入主指标。",
                    _review_action(prop),
                    LOW_EVIDENCE_LABEL,
                    "primary_metric_evidence_gate",
                    "evidence_items[].field_group",
                    json.dumps(["main_table", "export"], ensure_ascii=False),
                    _first_source_url(prop["evidence"]),
                    _review_query(prop),
                    now,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return {
        "hard_primary_metric_properties": len(hard_ids),
        "low_primary_metric_evidence_properties": len(weak_ids),
        "review_items_replaced": len(weak_ids),
        "label": LOW_EVIDENCE_LABEL,
    }


def _missing_properties() -> list[dict]:
    return [
        prop
        for prop in _properties_with_evidence()
        if not _has_hard_primary(prop["evidence"], prop["scene_type"])
    ]


def _target_pool(
    *,
    mode: str,
    countries: set[str],
    scenes: set[str],
    min_distinct_source_urls: int,
) -> list[dict]:
    properties = _filter_properties(
        _properties_with_evidence(),
        countries=countries,
        scenes=scenes,
    )
    if mode == "all":
        return properties
    if mode == "low-source-diversity":
        return [
            prop
            for prop in properties
            if _distinct_source_url_count(prop["evidence"]) < min_distinct_source_urls
        ]
    return [
        prop
        for prop in properties
        if not _has_hard_primary(prop["evidence"], prop["scene_type"])
    ]


def _filter_properties(
    properties: list[dict],
    *,
    countries: set[str],
    scenes: set[str],
) -> list[dict]:
    return [
        prop
        for prop in properties
        if (not countries or str(prop["country"]).casefold() in countries)
        and (not scenes or str(prop["scene_type"]).casefold() in scenes)
    ]


def _distinct_source_url_count(evidence_rows: list[dict]) -> int:
    return len(
        {
            _url_key(str(evidence.get("source_url") or ""))
            for evidence in evidence_rows
            if evidence.get("source_url")
        }
        - {""}
    )


def _select_targets(properties: list[dict], limit: int) -> list[dict]:
    return sorted(
        properties,
        key=lambda prop: (
            SCENE_PRIORITY.get(prop["scene_type"], 99),
            -float(prop.get("annual_visits_est") or 0),
            prop["country"],
            prop["canonical_name"],
        ),
    )[:limit]


def _properties_with_evidence(scan_run_id: str | None = None) -> list[dict]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    if scan_run_id is None:
        rows = connection.execute(
            """
            SELECT p.*, sc.scan_run_id, sm.annual_visits_est, e.field_group,
                   e.indicator_name, e.field_value, e.source_url, e.source_name
            FROM properties p
            JOIN scan_candidates sc ON sc.property_id = p.id
            LEFT JOIN scene_model_results sm
              ON sm.property_id = p.id AND sm.scan_run_id = sc.scan_run_id
            LEFT JOIN evidence_items e
              ON e.property_id = p.id AND e.scan_run_id = sc.scan_run_id
            WHERE sc.created_at = (
                SELECT MAX(sc2.created_at)
                FROM scan_candidates sc2
                WHERE sc2.property_id = p.id
            )
            ORDER BY p.country, p.scene_type, p.canonical_name
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT p.*, sc.scan_run_id, sm.annual_visits_est, e.field_group,
                   e.indicator_name, e.field_value, e.source_url, e.source_name
            FROM properties p
            JOIN scan_candidates sc ON sc.property_id = p.id AND sc.scan_run_id = ?
            LEFT JOIN scene_model_results sm
              ON sm.property_id = p.id AND sm.scan_run_id = sc.scan_run_id
            LEFT JOIN evidence_items e
              ON e.property_id = p.id AND e.scan_run_id = sc.scan_run_id
            ORDER BY p.country, p.scene_type, p.canonical_name
            """,
            (scan_run_id,),
        ).fetchall()
    connection.close()

    by_id: dict[str, dict] = {}
    for row in rows:
        prop = by_id.setdefault(
            row["id"],
            {
                "id": row["id"],
                "scan_run_id": row["scan_run_id"],
                "canonical_name": row["canonical_name"],
                "country": row["country"],
                "city": row["city"],
                "scene_type": row["scene_type"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "geocode_precision": row["geocode_precision"],
                "map_source": row["map_source"],
                "map_source_date": row["map_source_date"],
                "hero_image": row["hero_image"],
                "annual_visits_est": row["annual_visits_est"],
                "evidence": [],
            },
        )
        if row["field_group"] or row["indicator_name"] or row["field_value"]:
            prop["evidence"].append(
                {
                    "field_group": row["field_group"],
                    "indicator_name": row["indicator_name"],
                    "field_value": row["field_value"] or "",
                    "source_url": row["source_url"],
                    "source_name": row["source_name"],
                }
            )
    return list(by_id.values())


def _has_hard_primary(evidence_rows: list[dict], scene_type: str) -> bool:
    accepted = set(HARD_PRIMARY.get(scene_type, []))
    if not accepted:
        return False
    for evidence in evidence_rows:
        tokens = {evidence.get("field_group"), evidence.get("indicator_name")} - {None, ""}
        if tokens & accepted and _has_digit(str(evidence.get("field_value") or "")):
            return True
    return False


def _queries_for_property(prop: dict) -> list[str]:
    templates = QUERY_TEMPLATES.get(prop["scene_type"], [])
    return [
        template.format(
            property_name=prop["canonical_name"],
            country=prop["country"],
            city=prop["city"] or "",
        )
        for template in templates
    ]


def _best_extraction(prop: dict, page) -> object | None:
    indicators = HARD_PRIMARY.get(prop["scene_type"], [])
    for extraction in extract_indicators(page, prop["canonical_name"], indicators):
        if not extraction.field_value.strip():
            continue
        if _plausible(prop["scene_type"], extraction.field_group, extraction.field_value):
            if not _extraction_is_property_specific(prop, page, extraction):
                continue
            return extraction
    return None


def _plausible(scene_type: str, field_group: str, field_value: str) -> bool:
    text = field_value.casefold()
    if not _has_digit(text):
        return False
    if _looks_like_chart_axis_value(text):
        return False
    number = _scaled_first_number(text)
    if number is None:
        return False
    if annual_metric_period_issue(field_group, field_value):
        return False
    if scene_type == "airport_terminal":
        if field_group == "international_passenger_share":
            return "%" in text and 0 < number <= 100
        return (
            field_group in HARD_PRIMARY["airport_terminal"]
            and ("passenger" in text or "pax" in text)
            and number >= 1_000
        )
    if scene_type == "stadium":
        return ("seat" in text or "capacity" in text or "spectator" in text) and number >= 500
    if scene_type == "convention_center":
        has_context = any(
            token in text
            for token in ["sqm", "m2", "square", "delegates", "attendees", "events", "people"]
        )
        if not has_context:
            return False
        if field_group in {"exhibition_area", "meeting_area"}:
            return number >= 500
        return number >= 10
    if scene_type == "luxury_hotel_mice":
        return ("room" in text or "key" in text or "guest" in text) and number >= 20
    if scene_type == "mall_mixed_use":
        has_context = any(
            token in text for token in ["sqm", "m2", "square", "visitor", "footfall", "visits"]
        )
        if not has_context:
            return False
        if field_group == "gla":
            return number >= 1_000
        return number >= 10_000
    if scene_type == "office_government":
        return (
            any(token in text for token in ["sqm", "m2", "square", "office", "gfa", "nla"])
            and number >= 500
        )
    return True


GENERIC_SOURCE_PATH_TOKENS = [
    "list_of_",
    "/list-",
    "/traffic-data",
    "/airports.php",
    "/airports/",
    "/data/",
    "worlddata.info",
    "statbase.org",
]

PROPERTY_TOKEN_STOPWORDS = {
    "airport",
    "international",
    "terminal",
    "stadium",
    "station",
    "railway",
    "metro",
    "mall",
    "shopping",
    "centre",
    "center",
    "convention",
    "exhibition",
    "hotel",
    "resort",
    "university",
    "hospital",
    "office",
    "government",
    "tower",
    "building",
    "sports",
    "national",
    "complex",
    "mega",
    "city",
    "the",
    "and",
    "of",
    "de",
    "da",
    "do",
    "del",
    "el",
    "al",
    "la",
    "le",
}


def _extraction_is_property_specific(prop: dict, page: FetchedPage, extraction) -> bool:
    content = page.content_text or ""
    source_url = str(page.source_url or "")
    source_text = f"{source_url} {page.source_name or ''}"
    if not _mentions_property(prop["canonical_name"], f"{content} {source_text}"):
        return False
    if not _mentions_country(prop["country"], f"{content} {source_text}"):
        return False
    if _value_near_property(prop["canonical_name"], content, extraction.field_value):
        return True
    if _source_url_mentions_property(prop["canonical_name"], source_url):
        return not _is_generic_source_url(source_url)
    return False


def _mentions_property(property_name: str, text: str) -> bool:
    tokens = _distinctive_property_tokens(property_name)
    if not tokens:
        return False
    normalized = _normalized_search_text(text)
    matched = sum(1 for token in tokens if token in normalized)
    required = len(tokens) if len(tokens) <= 2 else 2
    return matched >= required


def _mentions_country(country: str, text: str) -> bool:
    normalized = _normalized_search_text(text)
    return _normalized_search_text(country) in normalized


def _source_url_mentions_property(property_name: str, source_url: str) -> bool:
    return _mentions_property(property_name, source_url.replace("-", " ").replace("_", " "))


def _value_near_property(property_name: str, content: str, field_value: str) -> bool:
    if not content or not field_value:
        return False
    snippets = _metric_windows(content, field_value, window_chars=260)
    return any(_mentions_property(property_name, snippet) for snippet in snippets)


def _metric_windows(content: str, field_value: str, window_chars: int) -> list[str]:
    windows: list[str] = []
    patterns = [re.escape(field_value)]
    number = _first_number(field_value)
    if number is not None:
        if number.is_integer():
            patterns.append(re.escape(str(int(number))))
            patterns.append(re.escape(f"{int(number):,}"))
        else:
            patterns.append(re.escape(str(number)))
    for pattern in dict.fromkeys(patterns):
        if not pattern:
            continue
        for match in re.finditer(pattern, content, flags=re.IGNORECASE):
            start = _context_start(content, match.start(), window_chars)
            end = _context_end(content, match.end(), window_chars)
            windows.append(content[start:end])
            if len(windows) >= 8:
                return windows
    return windows


def _context_start(content: str, position: int, window_chars: int) -> int:
    floor = max(0, position - window_chars)
    punctuation = [
        content.rfind(token, floor, position)
        for token in [".", "\n", "!", "?", ";"]
    ]
    boundary = max(punctuation)
    return boundary + 1 if boundary >= floor else floor


def _context_end(content: str, position: int, window_chars: int) -> int:
    ceiling = min(len(content), position + window_chars)
    boundaries = [
        idx
        for idx in (content.find(token, position, ceiling) for token in [".", "\n", "!", "?", ";"])
        if idx != -1
    ]
    return min(boundaries) + 1 if boundaries else ceiling


def _distinctive_property_tokens(property_name: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", property_name.casefold())
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in PROPERTY_TOKEN_STOPWORDS
    ]


def _normalized_search_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").casefold()))


def _is_generic_source_url(source_url: str) -> bool:
    lower = source_url.casefold()
    return any(token in lower for token in GENERIC_SOURCE_PATH_TOKENS)


def _draft_for_existing(prop: dict, extraction, content_text: str, registry: dict) -> CandidateDraft:
    annual_visits = _annual_visits_from_value(extraction.field_group, extraction.field_value)
    return CandidateDraft(
        region=_region_for_country(prop["country"]),
        country=prop["country"],
        city=prop["city"],
        property_name=prop["canonical_name"],
        scene_type=prop["scene_type"],
        annual_visits=annual_visits,
        latitude=float(prop["latitude"]),
        longitude=float(prop["longitude"]),
        geocode_precision=prop["geocode_precision"],
        map_source=prop["map_source"] or "",
        map_source_date=prop["map_source_date"] or "",
        field_group=extraction.field_group,
        indicator_name=extraction.field_group,
        field_value=_clean_field_value(extraction.field_value),
        source_name=str(extraction.source_name),
        source_tier=_tier_value(extraction.source_tier),
        source_url=str(extraction.source_url),
        source_date=extraction.source_date or "",
        evidence_type="Direct",
        bbox=registry["countries"][prop["country"]]["bbox"],
        source_type=SOURCE_TYPE,
        content_text=content_text[:8000],
        identity_match_status=KNOWN_PROPERTY,
        matched_property_id=prop["id"],
        matched_property_name=prop["canonical_name"],
        identity_match_reason="active property matched by property_id for full evidence backfill",
        hero_image=json.loads(prop["hero_image"]) if prop["hero_image"] else None,
    )


def _annual_visits_from_value(field_group: str, field_value: str) -> float | None:
    if field_group not in {
        "annual_visits",
        "annual_passenger_throughput",
        "passenger_throughput",
        "annual_footfall",
        "footfall",
        "daily_ridership",
        "interchange_volume",
    }:
        return None
    if annual_metric_period_issue(field_group, field_value):
        return None
    number = _first_number(field_value)
    if number is None:
        return None
    lower = field_value.casefold()
    if "million" in lower:
        number *= 1_000_000
    return float(number)


def _clean_field_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:500]


def _first_number(value: str) -> float | None:
    match = re.search(r"\d[\d,.]*", value)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _scaled_first_number(value: str) -> float | None:
    number = _first_number(value)
    if number is None:
        return None
    if "million" in value.casefold():
        number *= 1_000_000
    return number


def _looks_like_chart_axis_value(value: str) -> bool:
    digit_chars = re.sub(r"\D", "", value)
    return len(digit_chars) > 14 and bool(re.search(r"20\d{2}20\d{2}", digit_chars))


def _has_digit(value: str) -> bool:
    return bool(re.search(r"\d", value))


def _tier_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _region_for_country(country: str) -> str:
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return "Unknown"


def _first_source_url(evidence_rows: list[dict]) -> str | None:
    for evidence in evidence_rows:
        if evidence.get("source_url"):
            return evidence["source_url"]
    return None


def _review_query(prop: dict) -> str:
    indicators = " OR ".join(HARD_PRIMARY.get(prop["scene_type"], []))
    return f"{prop['canonical_name']} {prop['country']} {indicators} official"


def _review_action(prop: dict) -> str:
    scene = prop["scene_type"]
    if scene == "airport_terminal":
        return (
            f"补查 {prop['canonical_name']} 的机场运营方或民航主管机构年度客流统计；"
            "记录 passenger throughput 数值、年份、来源链接和发布日期。"
        )
    if scene == "stadium":
        return f"核验 {prop['canonical_name']} 官方/场馆数据库座位容量，并记录来源链接和日期。"
    if scene == "convention_center":
        return f"补查 {prop['canonical_name']} 官方 venue factsheet 的展览面积或峰值容量。"
    if scene == "mall_mixed_use":
        return f"补查 {prop['canonical_name']} 运营方/开发商披露的 GLA 或年度客流。"
    if scene == "luxury_hotel_mice":
        return f"补查 {prop['canonical_name']} 官方客房数、会议面积或宴会厅容量。"
    return f"补查 {prop['canonical_name']} 的场景主指标量化证据，并记录来源链接和日期。"


def _query_key(prop: dict, query: str) -> str:
    return "|".join(
        [
            str(prop["id"]),
            str(prop["country"]).strip().casefold(),
            str(prop["scene_type"]).strip().casefold(),
            re.sub(r"\s+", " ", query).strip().casefold(),
        ]
    )


def _url_key(url: str) -> str:
    value = str(url or "").strip()
    if "#" in value:
        value = value.split("#", 1)[0]
    return value.rstrip("/").casefold()


def _audit_event_paths(directory: Path) -> list[Path]:
    return sorted(directory.glob(f"{SOURCE_TYPE}_*_events.jsonl"))


def _search_result_payload(result: SearchResult) -> dict:
    return {
        "title": result.title,
        "url": str(result.url),
        "source_name": result.source_name,
        "snippet": result.snippet,
    }


def _search_results_from_payloads(payloads: list[dict]) -> list[SearchResult]:
    results: list[SearchResult] = []
    for payload in payloads:
        url = str(payload.get("url") or "").strip()
        if not url:
            continue
        results.append(
            SearchResult(
                title=str(payload.get("title") or url),
                url=url,
                source_name=str(payload.get("source_name") or "Firecrawl Search"),
                snippet=str(payload.get("snippet") or ""),
            )
        )
    return results


def _provider_stats(provider: FirecrawlPublicEvidenceProvider) -> dict:
    return {
        "search_requests": provider.stats.search_requests,
        "scrape_requests": provider.stats.scrape_requests,
        "credits_used_reported_by_api": provider.stats.credits_used,
        "warnings": list(provider.stats.warnings),
    }


def _persist_fetched_page(page: FetchedPage, pages_dir: Path) -> dict:
    pages_dir.mkdir(parents=True, exist_ok=True)
    content = page.content_text or ""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    content_path = pages_dir / f"{content_hash}.md"
    if not content_path.exists():
        content_path.write_text(content, encoding="utf-8")
    return {
        "source_url": str(page.source_url),
        "source_name": page.source_name,
        "source_tier": _tier_value(page.source_tier),
        "source_date": page.source_date,
        "fetched_at": page.fetched_at.isoformat(),
        "robots_allowed": page.robots_allowed,
        "content_sha256": content_hash,
        "content_chars": len(content),
        "content_file": str(content_path),
    }


def _page_from_logged_event(event: dict) -> FetchedPage | None:
    content_file = event.get("content_file")
    if not content_file:
        return None
    path = Path(str(content_file))
    if not path.exists():
        return None
    fetched_at = _parse_datetime(event.get("fetched_at"))
    return FetchedPage(
        source_url=str(event.get("source_url") or event.get("url") or ""),
        source_name=str(event.get("source_name") or "Firecrawl Search"),
        source_tier=_source_tier(event.get("source_tier")),
        source_date=event.get("source_date") if event.get("source_date") else None,
        fetched_at=fetched_at,
        content_text=path.read_text(encoding="utf-8"),
        robots_allowed=bool(event.get("robots_allowed", True)),
    )


def _parse_datetime(value: object) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _source_tier(value: object) -> SourceTier:
    try:
        return SourceTier(str(value))
    except ValueError:
        return SourceTier.TIER_3


def _render_report(summary: dict) -> str:
    before_lines = _scene_lines(summary["gap_summary_before"])
    after_lines = _scene_lines(summary["gap_summary_after"])
    return (
        "# Firecrawl Full Evidence Backfill\n\n"
        f"- Firecrawl searches used: {summary['searches_used']} / {summary['max_searches']}\n"
        f"- Durable audit log: {summary['audit_log_path']}\n"
        f"- Reused logged search result sets: {summary['reused_search_result_sets']}\n"
        f"- Reused logged pages: {summary['reused_pages']}\n"
        f"- Drafts written: {summary['draft_count']} {summary['drafts_by_scene']}\n"
        f"- Raw evidence written/changed: {summary['raw_evidence_written_or_changed']}\n"
        f"- Low evidence labels: {summary['labels']['low_primary_metric_evidence_properties']}\n"
        f"- Search log: {summary['search_log_path']}\n\n"
        "## Before\n"
        f"{before_lines}\n\n"
        "## After\n"
        f"{after_lines}\n"
    )


def _scene_lines(gap_summary: dict) -> str:
    return "\n".join(
        "- {scene}: hard={hard}/{total}, low={low}".format(
            scene=scene,
            hard=stats["hard_primary_metric_properties"],
            total=stats["total"],
            low=stats["low_primary_metric_evidence_properties"],
        )
        for scene, stats in gap_summary["scene_summary"].items()
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-searches", type=int, default=120)
    parser.add_argument("--max-properties", type=int, default=80)
    parser.add_argument("--result-limit", type=int, default=3)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument(
        "--countries",
        default="",
        help="Comma-separated country filter. Empty means all active countries.",
    )
    parser.add_argument(
        "--scenes",
        default="",
        help="Comma-separated scene_type filter. Empty means all scenes.",
    )
    parser.add_argument(
        "--target-mode",
        choices=["missing-hard-primary", "low-source-diversity", "all"],
        default="missing-hard-primary",
        help=(
            "missing-hard-primary fills primary metric gaps; low-source-diversity "
            "adds second-source evidence; all scans every filtered property."
        ),
    )
    parser.add_argument("--min-distinct-source-urls", type=int, default=2)
    parser.add_argument(
        "--credit-budget",
        type=int,
        default=1000,
        help="Maximum Firecrawl credits to spend. Use 0 for a no-live-request dry run; use -1 to disable the guard.",
    )
    parser.add_argument("--credit-check-interval", type=int, default=5)
    parser.add_argument("--audit-log-path", type=str, default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--force-retry", action="store_true")
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--no-sync", action="store_true")
    return parser.parse_args()


def _split_filter(value: str) -> set[str]:
    return {
        item.strip().casefold()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    }


def _remaining_firecrawl_credits() -> int | None:
    try:
        completed = subprocess.run(
            ["firecrawl", "credit-usage", "--json"],
            check=True,
            capture_output=True,
            env=firecrawl_subprocess_env(),
            text=True,
            timeout=20,
        )
        payload = json.loads(completed.stdout)
        return int(payload.get("data", {}).get("remainingCredits"))
    except Exception:
        return None


def _budget_exhausted(
    *,
    start_remaining: int | None,
    budget: int,
    request_count: int,
    check_interval: int,
) -> bool:
    if budget == 0:
        return True
    if budget < 0 or start_remaining is None:
        return False
    if check_interval > 1 and request_count % check_interval:
        return False
    remaining = _remaining_firecrawl_credits()
    return remaining is not None and start_remaining - remaining >= budget


if __name__ == "__main__":
    main()
