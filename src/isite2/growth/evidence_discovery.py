from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from isite2.growth.evidence_intake import (
    CandidateDraft,
    EvidenceIntakeProvider,
)
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.hybrid_discovery import HybridPublicDiscoveryProvider
from isite2.growth.regional_targets import countries_for_regions


@dataclass(frozen=True)
class EvidenceDiscoveryResult:
    attempted: bool
    discovered_count: int
    new_count: int
    changed_count: int
    duplicate_unchanged_count: int
    raw_evidence_ids: list[str] = field(default_factory=list)
    discovery_run_id: str | None = None
    seeded_task_count: int = 0
    leased_task_count: int = 0
    searched_count: int = 0
    fetched_count: int = 0
    failed_count: int = 0
    firecrawl_search_count: int = 0
    firecrawl_fetch_count: int = 0
    firecrawl_credits_used: float = 0
    firecrawl_warnings: list[str] = field(default_factory=list)
    known_property_skipped_count: int = 0
    known_url_skipped_count: int = 0
    possible_duplicate_review_count: int = 0
    new_opportunity_count: int = 0
    existing_property_evidence_update_count: int = 0
    firecrawl_requests_saved_estimate: int = 0
    countries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    report_path: Path | None = None
    summary_path: Path | None = None

    @property
    def curation_needed(self) -> bool:
        return self.new_count > 0 or self.changed_count > 0


def discover_public_evidence(
    *,
    regions: list[str],
    store: EvidenceCurationStore,
    provider: EvidenceIntakeProvider | None = None,
    output_dir: Path | None = None,
    max_searches_per_cycle: int = 20,
    max_fetches_per_cycle: int = 40,
    worker_id: str | None = None,
    target_countries: list[str] | None = None,
    new_opportunities_only: bool = True,
    force_rescan_existing: bool = False,
) -> EvidenceDiscoveryResult:
    region_countries = countries_for_regions(regions)
    selected_countries = _select_target_countries(region_countries, target_countries)
    discovery_provider = provider or HybridPublicDiscoveryProvider(
        store=store,
        max_searches_per_cycle=max_searches_per_cycle,
        max_fetches_per_cycle=max_fetches_per_cycle,
        worker_id=worker_id,
        target_countries=selected_countries,
        new_opportunities_only=new_opportunities_only,
        force_rescan_existing=force_rescan_existing,
    )
    drafts: list[CandidateDraft] = discovery_provider.discover(regions, selected_countries)

    raw_evidence_ids: list[str] = []
    new_count = 0
    changed_count = 0
    duplicate_unchanged_count = 0
    for draft in drafts:
        result = store.upsert_candidate_evidence(draft)
        raw_evidence_ids.append(result.raw_evidence_id)
        if result.duplicate_unchanged:
            duplicate_unchanged_count += 1
        elif result.is_changed_evidence:
            changed_count += 1
        elif result.is_new_evidence:
            new_count += 1

    finalize_run = getattr(discovery_provider, "finalize_run", None)
    if callable(finalize_run):
        finalize_run(
            discovered_count=len(drafts),
            new_count=new_count,
            changed_count=changed_count,
            duplicate_count=duplicate_unchanged_count,
        )
    stats = getattr(discovery_provider, "last_stats", None)
    discovery_result = EvidenceDiscoveryResult(
        attempted=True,
        discovered_count=len(drafts),
        new_count=new_count,
        changed_count=changed_count,
        duplicate_unchanged_count=duplicate_unchanged_count,
        raw_evidence_ids=raw_evidence_ids,
        discovery_run_id=getattr(stats, "run_id", None),
        seeded_task_count=getattr(stats, "seeded_tasks", 0),
        leased_task_count=getattr(stats, "leased_tasks", 0),
        searched_count=getattr(stats, "searched_count", 0),
        fetched_count=getattr(stats, "fetched_count", 0),
        failed_count=getattr(stats, "failed_count", 0),
        firecrawl_search_count=getattr(stats, "firecrawl_search_count", 0),
        firecrawl_fetch_count=getattr(stats, "firecrawl_fetch_count", 0),
        firecrawl_credits_used=getattr(stats, "firecrawl_credits_used", 0),
        firecrawl_warnings=list(getattr(stats, "firecrawl_warnings", []) or []),
        known_property_skipped_count=getattr(stats, "known_property_skipped_count", 0),
        known_url_skipped_count=getattr(stats, "known_url_skipped_count", 0),
        possible_duplicate_review_count=getattr(
            stats,
            "possible_duplicate_review_count",
            0,
        ),
        new_opportunity_count=getattr(stats, "new_opportunity_count", 0),
        existing_property_evidence_update_count=getattr(
            stats,
            "existing_property_evidence_update_count",
            0,
        ),
        firecrawl_requests_saved_estimate=getattr(
            stats,
            "firecrawl_requests_saved_estimate",
            0,
        ),
        countries=sorted(getattr(stats, "countries", set()) or []),
        errors=list(getattr(stats, "errors", []) or []),
    )
    if output_dir is not None:
        return write_discovery_report(output_dir, discovery_result)
    return discovery_result


def write_discovery_report(
    output_dir: Path,
    result: EvidenceDiscoveryResult,
) -> EvidenceDiscoveryResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = result.discovery_run_id or f"discovery_{timestamp}"
    summary_path = output_dir / f"{run_id}.json"
    report_path = output_dir / f"{run_id}.md"
    summary = {
        "mode": "public_evidence_discovery",
        "discovery_run_id": result.discovery_run_id,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "seeded_task_count": result.seeded_task_count,
        "leased_task_count": result.leased_task_count,
        "searched_count": result.searched_count,
        "fetched_count": result.fetched_count,
        "firecrawl_search_count": result.firecrawl_search_count,
        "firecrawl_fetch_count": result.firecrawl_fetch_count,
        "firecrawl_credits_used": result.firecrawl_credits_used,
        "firecrawl_warnings": result.firecrawl_warnings[:50],
        "known_property_skipped_count": result.known_property_skipped_count,
        "known_url_skipped_count": result.known_url_skipped_count,
        "possible_duplicate_review_count": result.possible_duplicate_review_count,
        "new_opportunity_count": result.new_opportunity_count,
        "existing_property_evidence_update_count": (
            result.existing_property_evidence_update_count
        ),
        "firecrawl_requests_saved_estimate": result.firecrawl_requests_saved_estimate,
        "discovered_count": result.discovered_count,
        "new_count": result.new_count,
        "changed_count": result.changed_count,
        "duplicate_unchanged_count": result.duplicate_unchanged_count,
        "failed_count": result.failed_count,
        "countries": result.countries,
        "raw_evidence_ids": result.raw_evidence_ids,
        "errors": result.errors[:50],
        "improvement_points": [
            "Discovery is now driven by country-scene-source tasks with leases, so multiple "
            "workers can search without intentionally duplicating the same task.",
            "New evidence is persisted only when the URL, field payload, content hash, or "
            "source date changes.",
            "Seed catalog remains as bootstrap/backfill while live public providers expand "
            "coverage over time.",
            "Known property and known URL filters now run before page fetches to reduce "
            "repeat Firecrawl/API spend in follow-up rounds.",
            "Web/ChatGPT search is the first discovery lane; Firecrawl is reserved for "
            "gap-fill scraping when public search results do not provide enough evidence, "
            "coordinate, or media context.",
        ],
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_discovery_report(summary), encoding="utf-8")
    (output_dir / "latest_discovery_report.md").write_text(
        report_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return EvidenceDiscoveryResult(
        attempted=result.attempted,
        discovered_count=result.discovered_count,
        new_count=result.new_count,
        changed_count=result.changed_count,
        duplicate_unchanged_count=result.duplicate_unchanged_count,
        raw_evidence_ids=result.raw_evidence_ids,
        discovery_run_id=result.discovery_run_id,
        seeded_task_count=result.seeded_task_count,
        leased_task_count=result.leased_task_count,
        searched_count=result.searched_count,
        fetched_count=result.fetched_count,
        failed_count=result.failed_count,
        firecrawl_search_count=result.firecrawl_search_count,
        firecrawl_fetch_count=result.firecrawl_fetch_count,
        firecrawl_credits_used=result.firecrawl_credits_used,
        firecrawl_warnings=result.firecrawl_warnings,
        known_property_skipped_count=result.known_property_skipped_count,
        known_url_skipped_count=result.known_url_skipped_count,
        possible_duplicate_review_count=result.possible_duplicate_review_count,
        new_opportunity_count=result.new_opportunity_count,
        existing_property_evidence_update_count=(
            result.existing_property_evidence_update_count
        ),
        firecrawl_requests_saved_estimate=result.firecrawl_requests_saved_estimate,
        countries=result.countries,
        errors=result.errors,
        report_path=report_path,
        summary_path=summary_path,
    )


def _render_discovery_report(summary: dict[str, object]) -> str:
    errors = summary.get("errors") or []
    error_lines = "\n".join(f"- {error}" for error in errors) or "- None"
    warnings = summary.get("firecrawl_warnings") or []
    warning_lines = "\n".join(f"- {warning}" for warning in warnings) or "- None"
    improvements = "\n".join(
        f"- {point}" for point in summary.get("improvement_points", [])
    )
    countries = ", ".join(summary.get("countries", []) or []) or "None"
    return (
        f"# Public Evidence Discovery Run {summary.get('discovery_run_id') or ''}\n\n"
        f"- Seeded tasks: {summary['seeded_task_count']}\n"
        f"- Leased tasks: {summary['leased_task_count']}\n"
        f"- Searches: {summary['searched_count']}\n"
        f"- Fetches: {summary['fetched_count']}\n"
        f"- Firecrawl searches: {summary['firecrawl_search_count']}\n"
        f"- Firecrawl fetches: {summary['firecrawl_fetch_count']}\n"
        f"- Firecrawl credits used: {summary['firecrawl_credits_used']}\n"
        f"- Known properties skipped before fetch: {summary['known_property_skipped_count']}\n"
        f"- Known URLs skipped before fetch: {summary['known_url_skipped_count']}\n"
        f"- Possible duplicate reviews: {summary['possible_duplicate_review_count']}\n"
        f"- New opportunity drafts: {summary['new_opportunity_count']}\n"
        f"- Existing property evidence updates: "
        f"{summary['existing_property_evidence_update_count']}\n"
        f"- Estimated Firecrawl/API fetches saved: "
        f"{summary['firecrawl_requests_saved_estimate']}\n"
        f"- Candidate drafts discovered: {summary['discovered_count']}\n"
        f"- New evidence items: {summary['new_count']}\n"
        f"- Changed evidence items: {summary['changed_count']}\n"
        f"- Duplicate unchanged items: {summary['duplicate_unchanged_count']}\n"
        f"- Failed searches/fetches: {summary['failed_count']}\n"
        f"- Countries: {countries}\n\n"
        "## Errors And Skips\n\n"
        f"{error_lines}\n\n"
        "## Firecrawl Warnings\n\n"
        f"{warning_lines}\n\n"
        "## Improvement Points\n\n"
        f"{improvements}\n"
    )


def _select_target_countries(
    region_countries: list[str],
    target_countries: list[str] | None,
) -> list[str]:
    if not target_countries:
        return region_countries
    allowed = {country.casefold() for country in target_countries}
    return [country for country in region_countries if country.casefold() in allowed]
