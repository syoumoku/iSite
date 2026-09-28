from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isite2.growth.evidence_intake import (
    DEFAULT_DRAFT_PATH,
    DEFAULT_OVERLAY_PATH,
    CandidateDraft,
    append_accepted_to_overlay,
    candidate_key,
    candidate_keys,
    load_registry_overlay,
    merge_source_registries,
    validate_candidate_draft,
    write_draft_review,
    write_registry_overlay,
)
from isite2.growth.evidence_store import (
    EvidenceCurationStore,
    raw_evidence_to_candidate_draft,
)
from isite2.growth.property_identity import (
    KNOWN_PROPERTY,
    POSSIBLE_DUPLICATE,
    known_opportunity_index_from_registry,
)
from isite2.growth.raw_evidence_metric_extraction import (
    RawEvidenceMetricCleaner,
    raw_metric_cleaner_from_env,
)
from isite2.rules.config_loader import load_discovery_sources, load_source_registry


@dataclass(frozen=True)
class EvidenceCurationRunResult:
    curation_run_id: str
    new_evidence_count: int
    accepted_count: int
    updated_count: int
    rejected_count: int
    countries: list[str]
    overlay_path: Path
    draft_path: Path
    report_path: Path
    summary_path: Path
    review_actions: list[str] = field(default_factory=list)
    candidate_draft_ids: list[str] = field(default_factory=list)
    progress_groups: list[dict[str, Any]] = field(default_factory=list)
    metric_cleaning_summary: dict[str, Any] = field(default_factory=dict)


def run_pending_evidence_curation(
    *,
    store: EvidenceCurationStore,
    output_dir: Path,
    overlay_path: Path = DEFAULT_OVERLAY_PATH,
    draft_path: Path = DEFAULT_DRAFT_PATH,
    base_registry: dict[str, Any] | None = None,
    limit: int | None = None,
    metric_cleaner: RawEvidenceMetricCleaner | None = None,
) -> EvidenceCurationRunResult | None:
    pending = store.list_pending_evidence(limit=limit)
    if not pending:
        return None
    metric_cleaner = metric_cleaner if metric_cleaner is not None else raw_metric_cleaner_from_env()

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    curation_run_id = f"curation_{timestamp}"
    store.mark_curating([row.id for row in pending], curation_run_id)

    source_registry = base_registry or load_source_registry()
    overlay_registry = load_registry_overlay(overlay_path)
    effective_registry = merge_source_registries(source_registry, overlay_registry)
    existing_keys = candidate_keys(effective_registry)
    known_index = known_opportunity_index_from_registry(
        effective_registry,
        session_factory=store.session_factory,
    )

    accepted: list[CandidateDraft] = []
    rejected: list[dict[str, Any]] = []
    candidate_draft_ids: list[str] = []
    accepted_count = 0
    updated_count = 0
    countries: set[str] = set()
    review_actions: list[str] = []
    progress_stats: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    metric_cleaning_stats: dict[str, int] = {}
    metric_cleaning_samples: list[dict[str, Any]] = []

    for raw in pending:
        draft = raw_evidence_to_candidate_draft(raw)
        cleaning_issues: list[str] = []
        if metric_cleaner is not None:
            try:
                cleaning = metric_cleaner.clean(raw, draft)
                draft = cleaning.draft
                cleaning_status = cleaning.status
                cleaning_issues = cleaning.issues
                provider_name = cleaning.provider_name
                cache_status = cleaning.cache_status
            except Exception as exc:
                cleaning_status = "gpt_error"
                cleaning_issues = [f"raw metric GPT cleaning failed: {exc}"]
                provider_name = metric_cleaner.provider_name
                cache_status = None
            metric_cleaning_stats[cleaning_status] = (
                metric_cleaning_stats.get(cleaning_status, 0) + 1
            )
            if cleaning_status != "rules_accepted" and len(metric_cleaning_samples) < 25:
                metric_cleaning_samples.append(
                    {
                        "raw_evidence_id": raw.id,
                        "country": draft.country,
                        "city": draft.city,
                        "property_name": draft.property_name,
                        "scene_type": draft.scene_type,
                        "status": cleaning_status,
                        "provider_name": provider_name,
                        "cache_status": cache_status,
                        "field_group": draft.field_group,
                        "field_value": draft.field_value,
                        "issues": cleaning_issues,
                    }
                )
        countries.add(draft.country)
        validation = validate_candidate_draft(draft)
        key = candidate_key(draft.country, draft.city, draft.property_name, draft.scene_type)
        identity_match = known_index.match(
            country=draft.country,
            city=draft.city,
            property_name=draft.property_name,
            scene_type=draft.scene_type,
            latitude=draft.latitude,
            longitude=draft.longitude,
            source_url=draft.source_url,
        )
        progress_key = _progress_key(draft, raw.source_type)
        group = progress_stats.setdefault(
            progress_key,
            {
                "region": draft.region,
                "country": draft.country,
                "scene_type": draft.scene_type,
                "source_type": raw.source_type or draft.source_type or "",
                "accepted_new_count": 0,
                "updated_count": 0,
                "draft_review_count": 0,
            },
        )

        duplicate_review = (
            identity_match.status == POSSIBLE_DUPLICATE
            or (
                identity_match.status == KNOWN_PROPERTY
                and key not in existing_keys
            )
            or draft.identity_match_status == POSSIBLE_DUPLICATE
        )
        metric_cleaning_review = bool(cleaning_issues)
        if validation.accepted and duplicate_review and not metric_cleaning_review:
            status = "possible_duplicate_review"
            issues = [
                "possible duplicate property identity",
                identity_match.reason or draft.identity_match_reason or "identity match review",
            ]
            rejected_draft = _duplicate_review_draft(draft, issues, identity_match)
            rejected.append(rejected_draft)
            review_actions.append(rejected_draft["next_action"])
            group["draft_review_count"] += 1
        elif validation.accepted and not metric_cleaning_review:
            status = "candidate_accepted"
            if key in existing_keys:
                updated_count += 1
                group["updated_count"] += 1
            else:
                accepted_count += 1
                group["accepted_new_count"] += 1
                existing_keys.add(key)
            accepted.append(draft)
            issues: list[str] = []
        else:
            status = "draft_review"
            issues = [*validation.issues, *cleaning_issues]
            if not issues:
                issues = ["raw metric cleaning requires review before candidate promotion"]
            rejected_draft = _rejected_draft(draft, issues)
            rejected.append(rejected_draft)
            review_actions.append(rejected_draft["next_action"])
            group["draft_review_count"] += 1

        candidate_draft_ids.append(
            store.create_candidate_draft(
                curation_run_id=curation_run_id,
                raw_evidence_ids=[raw.id],
                draft=draft,
                status=status,
                issues=issues,
                source_type=raw.source_type,
            )
        )
        store.mark_curated(raw.id, status=status, curation_run_id=curation_run_id)

    if accepted:
        updated_overlay = append_accepted_to_overlay(overlay_registry, accepted)
        write_registry_overlay(overlay_path, updated_overlay)
        store.seed_expansion_tasks_from_candidates(
            accepted,
            discovery_config=load_discovery_sources(),
        )
    else:
        write_registry_overlay(overlay_path, overlay_registry)
    write_draft_review(draft_path, rejected)

    summary_path = output_dir / f"{curation_run_id}.json"
    report_path = output_dir / f"{curation_run_id}.md"
    result = EvidenceCurationRunResult(
        curation_run_id=curation_run_id,
        new_evidence_count=len(pending),
        accepted_count=accepted_count,
        updated_count=updated_count,
        rejected_count=len(rejected),
        countries=sorted(countries),
        overlay_path=overlay_path,
        draft_path=draft_path,
        report_path=report_path,
        summary_path=summary_path,
        review_actions=review_actions,
        candidate_draft_ids=candidate_draft_ids,
        progress_groups=list(progress_stats.values()),
        metric_cleaning_summary={
            "enabled": metric_cleaner is not None,
            "provider_name": metric_cleaner.provider_name if metric_cleaner else None,
            "status_counts": metric_cleaning_stats,
            "samples": metric_cleaning_samples,
        },
    )
    summary = _curation_summary(result, accepted, rejected)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_curation_report(summary), encoding="utf-8")
    (output_dir / "latest_curation_report.md").write_text(
        report_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return result


def _curation_summary(
    result: EvidenceCurationRunResult,
    accepted: list[CandidateDraft],
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "mode": "evidence_curation",
        "curation_run_id": result.curation_run_id,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "new_evidence_count": result.new_evidence_count,
        "accepted_count": result.accepted_count,
        "updated_count": result.updated_count,
        "rejected_count": result.rejected_count,
        "derived_refresh_required": (result.accepted_count + result.updated_count) > 0,
        "countries": result.countries,
        "overlay_path": str(result.overlay_path),
        "draft_path": str(result.draft_path),
        "report_path": str(result.report_path),
        "accepted_candidates": [
            {
                "country": draft.country,
                "city": draft.city,
                "property_name": draft.property_name,
                "scene_type": draft.scene_type,
                "source_url": draft.source_url,
                "field_group": draft.field_group,
                "indicator_name": draft.indicator_name,
            }
            for draft in accepted
        ],
        "draft_review": rejected,
        "review_actions": result.review_actions,
        "progress_groups": result.progress_groups,
        "metric_cleaning": result.metric_cleaning_summary,
        "improvement_points": [
            "Discovery now writes raw_evidence_items first; curation is triggered only by "
            "new or changed evidence, not by a fixed backlog threshold.",
            "Duplicate URLs with unchanged content_hash and source_date are suppressed "
            "before curation.",
            "Incomplete evidence stays in candidate_drafts/review output and is not "
            "promoted to map-visible candidates.",
        ],
    }


def _render_curation_report(summary: dict[str, Any]) -> str:
    review_actions = "\n".join(
        f"- {action}" for action in summary["review_actions"]
    ) or "- None"
    accepted_rows = [
        "| Property | Country | City | Scene | Evidence Field | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in summary["accepted_candidates"]:
        accepted_rows.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in [
                    candidate["property_name"],
                    candidate["country"],
                    candidate["city"],
                    candidate["scene_type"],
                    candidate["field_group"],
                    candidate["source_url"],
                ]
            )
            + " |"
        )
    metric_cleaning = summary.get("metric_cleaning") or {}
    improvements = "\n".join(f"- {point}" for point in summary["improvement_points"])
    return (
        f"# Evidence Curation Run {summary['curation_run_id']}\n\n"
        f"- New evidence items: {summary['new_evidence_count']}\n"
        f"- Accepted new candidates: {summary['accepted_count']}\n"
        f"- Updated existing candidates: {summary['updated_count']}\n"
        f"- Draft/review items: {summary['rejected_count']}\n"
        f"- Derived refresh required: {summary['derived_refresh_required']}\n"
        f"- Countries: {', '.join(summary['countries']) or 'None'}\n"
        f"- Raw GPT metric cleaning: {_metric_cleaning_line(metric_cleaning)}\n"
        f"- Overlay: {summary['overlay_path']}\n"
        f"- Draft review: {summary['draft_path']}\n\n"
        "## Accepted Or Updated Evidence\n\n"
        + "\n".join(accepted_rows)
        + "\n\n"
        "## Review Queue Actions\n\n"
        f"{review_actions}\n\n"
        "## Improvement Points\n\n"
        f"{improvements}\n"
    )


def _rejected_draft(draft: CandidateDraft, issues: list[str]) -> dict[str, Any]:
    return {
        "country": draft.country,
        "city": draft.city,
        "property_name": draft.property_name,
        "scene_type": draft.scene_type,
        "issues": issues,
        "next_action": (
            f"补查 {draft.property_name or draft.source_url} 的公开来源，补齐字段级证据、"
            "坐标来源、来源日期和国家边界核验后再进入候选池。"
        ),
        "source_url": draft.source_url,
    }


def _metric_cleaning_line(metric_cleaning: dict[str, Any]) -> str:
    if not metric_cleaning.get("enabled"):
        return "disabled"
    counts = metric_cleaning.get("status_counts") or {}
    count_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    return f"{metric_cleaning.get('provider_name') or 'unknown'} ({count_text or 'no calls'})"


def _duplicate_review_draft(
    draft: CandidateDraft,
    issues: list[str],
    identity_match: Any,
) -> dict[str, Any]:
    matched_name = (
        identity_match.record.identity.property_name
        if getattr(identity_match, "record", None) is not None
        else draft.matched_property_name
    )
    return {
        "country": draft.country,
        "city": draft.city,
        "property_name": draft.property_name,
        "scene_type": draft.scene_type,
        "issues": issues,
        "next_action": (
            f"核验 {draft.property_name} 与 {matched_name or '已知候选物业'} "
            "是否同一物业；比对名称别名、坐标、官网/地图链接后再决定合并或新增。"
        ),
        "source_url": draft.source_url,
        "matched_property_name": matched_name,
        "identity_match_reason": (
            getattr(identity_match, "reason", None) or draft.identity_match_reason
        ),
    }


def _progress_key(
    draft: CandidateDraft,
    source_type: str | None,
) -> tuple[str, str, str, str]:
    return (
        draft.region.casefold(),
        draft.country.casefold(),
        draft.scene_type.casefold(),
        (source_type or draft.source_type or "").casefold(),
    )


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
