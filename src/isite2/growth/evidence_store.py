from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from isite2.connectors.models import FetchedPage
from isite2.db.models import (
    Base,
    CandidateDraftDB,
    DiscoveryProgressDB,
    DiscoveryRunDB,
    DiscoveryTaskDB,
    RawEvidenceItemDB,
    SourceCacheDB,
)
from isite2.db.session import create_engine_for_url, create_session_factory
from isite2.domain.enums import SourceTier
from isite2.growth.discovery_priority import discovery_task_priority
from isite2.growth.evidence_intake import CandidateDraft, candidate_key
from isite2.growth.property_identity import (
    ensure_property_identity_schema,
    normalize_source_url,
    normalize_text,
)
from isite2.growth.regional_targets import REGION_COUNTRIES
from isite2.repositories import DEFAULT_SQLITE_FALLBACK_URL

EXHAUSTED_NO_NEW_CYCLES = 2
_DISCOVERY_RUN_STATS_COLUMNS = (
    "known_property_skipped_count",
    "known_url_skipped_count",
    "possible_duplicate_review_count",
    "new_opportunity_count",
    "existing_property_evidence_update_count",
    "firecrawl_requests_saved_estimate",
)


@dataclass(frozen=True)
class RawEvidenceWriteResult:
    raw_evidence_id: str
    is_new_evidence: bool
    is_changed_evidence: bool
    duplicate_unchanged: bool
    status: str


@dataclass(frozen=True)
class DiscoveryTaskLease:
    id: str
    region: str
    country: str
    scene_type: str
    source_type: str
    query_template: str
    query: str
    priority: int
    cycle_number: int = 1


@dataclass(frozen=True)
class DiscoveryProgressSettlement:
    settled_groups: list[dict[str, Any]]
    exhausted_groups: list[dict[str, Any]]
    blocked_groups: list[dict[str, Any]]


class EvidenceCurationStore:
    def __init__(
        self,
        engine: Engine | None = None,
        database_url: str | None = None,
    ) -> None:
        if engine is None:
            url = database_url or DEFAULT_SQLITE_FALLBACK_URL
            _ensure_sqlite_parent(url)
            engine = create_engine_for_url(url)
        self.engine = engine
        self.session_factory: sessionmaker[Session] = create_session_factory(engine)
        Base.metadata.create_all(engine)
        self._ensure_schema_compat()

    @classmethod
    def from_repository(cls, repository: Any) -> EvidenceCurationStore:
        engine = getattr(repository, "engine", None)
        return cls(engine=engine) if engine is not None else cls()

    def source_cache(self) -> DatabaseSourceCache:
        return DatabaseSourceCache(self)

    def _ensure_schema_compat(self) -> None:
        with self.engine.begin() as connection:
            dialect = connection.dialect.name
            ensure_property_identity_schema(connection)
            if dialect == "sqlite":
                raw_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(raw_evidence_items)"
                    ).all()
                }
                if "source_type" not in raw_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE raw_evidence_items ADD COLUMN source_type VARCHAR(64)"
                    )
                draft_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(candidate_drafts)"
                    ).all()
                }
                if "source_type" not in draft_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE candidate_drafts ADD COLUMN source_type VARCHAR(64)"
                    )
                task_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(discovery_tasks)"
                    ).all()
                }
                if task_columns:
                    if "cycle_number" not in task_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE discovery_tasks "
                            "ADD COLUMN cycle_number INTEGER NOT NULL DEFAULT 1"
                        )
                    if "next_due_at" not in task_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE discovery_tasks ADD COLUMN next_due_at DATETIME"
                        )
                    if "failure_class" not in task_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE discovery_tasks ADD COLUMN failure_class VARCHAR(64)"
                        )
                run_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(discovery_runs)"
                    ).all()
                }
                for column in _DISCOVERY_RUN_STATS_COLUMNS:
                    if column not in run_columns:
                        connection.exec_driver_sql(
                            f"ALTER TABLE discovery_runs "
                            f"ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                        )
            elif dialect == "postgresql":
                connection.exec_driver_sql(
                    "ALTER TABLE raw_evidence_items "
                    "ADD COLUMN IF NOT EXISTS source_type VARCHAR(64)"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE candidate_drafts "
                    "ADD COLUMN IF NOT EXISTS source_type VARCHAR(64)"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE discovery_tasks "
                    "ADD COLUMN IF NOT EXISTS cycle_number INTEGER NOT NULL DEFAULT 1"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE discovery_tasks ADD COLUMN IF NOT EXISTS next_due_at TIMESTAMPTZ"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE discovery_tasks ADD COLUMN IF NOT EXISTS failure_class VARCHAR(64)"
                )
                for column in _DISCOVERY_RUN_STATS_COLUMNS:
                    connection.exec_driver_sql(
                        f"ALTER TABLE discovery_runs "
                        f"ADD COLUMN IF NOT EXISTS {column} INTEGER NOT NULL DEFAULT 0"
                    )

    def upsert_candidate_evidence(
        self,
        draft: CandidateDraft,
        content_text: str | None = None,
        source_type: str | None = None,
        discovery_task_id: str | None = None,
    ) -> RawEvidenceWriteResult:
        content_text = content_text if content_text is not None else draft.content_text
        source_type = source_type if source_type is not None else draft.source_type
        discovery_task_id = (
            discovery_task_id
            if discovery_task_id is not None
            else draft.discovery_task_id
        )
        dedupe_key = raw_evidence_dedupe_key(draft)
        content_hash = raw_evidence_content_hash(draft, content_text=content_text)
        with self.session_factory.begin() as session:
            existing = session.scalars(
                select(RawEvidenceItemDB)
                .where(RawEvidenceItemDB.dedupe_key == dedupe_key)
                .order_by(RawEvidenceItemDB.created_at.desc())
            ).first()
            if (
                existing is not None
                and existing.content_hash == content_hash
                and (existing.source_date or "") == (draft.source_date or "")
                and existing.status != "retracted_quality_guard"
            ):
                return RawEvidenceWriteResult(
                    raw_evidence_id=existing.id,
                    is_new_evidence=False,
                    is_changed_evidence=False,
                    duplicate_unchanged=True,
                    status=existing.status,
                )

            raw = RawEvidenceItemDB(
                id=str(uuid4()),
                region=draft.region,
                country=draft.country,
                city=draft.city,
                property_name=draft.property_name,
                scene_type=draft.scene_type,
                source_type=source_type,
                source_url=draft.source_url,
                source_name=draft.source_name,
                source_tier=draft.source_tier,
                source_date=draft.source_date,
                field_group=draft.field_group,
                indicator_name=draft.indicator_name,
                field_value=draft.field_value,
                evidence_type=draft.evidence_type,
                latitude=draft.latitude,
                longitude=draft.longitude,
                geocode_precision=draft.geocode_precision,
                map_source=draft.map_source,
                map_source_date=draft.map_source_date,
                annual_visits=draft.annual_visits,
                content_hash=content_hash,
                dedupe_key=dedupe_key,
                status="new",
                curation_needed=True,
                payload={
                    "bbox": draft.bbox,
                    "content_text": content_text or "",
                    "source_type": source_type,
                    "discovery_task_id": discovery_task_id,
                    "identity_match_status": draft.identity_match_status,
                    "matched_property_id": draft.matched_property_id,
                    "matched_property_name": draft.matched_property_name,
                    "identity_match_reason": draft.identity_match_reason,
                    "hero_image": draft.hero_image,
                },
            )
            session.add(raw)
            return RawEvidenceWriteResult(
                raw_evidence_id=raw.id,
                is_new_evidence=True,
                is_changed_evidence=existing is not None,
                duplicate_unchanged=False,
                status=raw.status,
            )

    def list_pending_evidence(self, limit: int | None = None) -> list[RawEvidenceItemDB]:
        statement = (
            select(RawEvidenceItemDB)
            .where(
                RawEvidenceItemDB.status == "new",
                RawEvidenceItemDB.curation_needed.is_(True),
            )
            .order_by(RawEvidenceItemDB.created_at)
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self.session_factory() as session:
            return list(session.scalars(statement).all())

    def mark_curating(self, raw_evidence_ids: list[str], curation_run_id: str) -> None:
        if not raw_evidence_ids:
            return
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            rows = session.scalars(
                select(RawEvidenceItemDB).where(RawEvidenceItemDB.id.in_(raw_evidence_ids))
            ).all()
            for row in rows:
                row.status = "curating"
                row.curation_run_id = curation_run_id
                row.updated_at = now

    def mark_curated(
        self,
        raw_evidence_id: str,
        status: str,
        curation_run_id: str,
    ) -> None:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.get(RawEvidenceItemDB, raw_evidence_id)
            if row is None:
                return
            row.status = status
            row.curation_needed = False
            row.curation_run_id = curation_run_id
            row.curated_at = now
            row.updated_at = now

    def create_candidate_draft(
        self,
        *,
        curation_run_id: str,
        raw_evidence_ids: list[str],
        draft: CandidateDraft,
        status: str,
        issues: list[str],
        source_type: str | None = None,
    ) -> str:
        row = CandidateDraftDB(
            id=str(uuid4()),
            curation_run_id=curation_run_id,
            raw_evidence_ids=raw_evidence_ids,
            country=draft.country,
            city=draft.city,
            property_name=draft.property_name,
            scene_type=draft.scene_type,
            source_type=source_type,
            status=status,
            issues=issues,
            candidate_payload=draft.registry_candidate(),
        )
        with self.session_factory.begin() as session:
            session.add(row)
        return row.id

    def raw_status_counts(self) -> dict[str, int]:
        with self.session_factory() as session:
            rows = session.scalars(select(RawEvidenceItemDB)).all()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    def list_raw_evidence(
        self,
        *,
        status: str | None = None,
        country: str | None = None,
        scene_type: str | None = None,
        source_type: str | None = None,
        curation_run_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        statement = select(RawEvidenceItemDB).order_by(RawEvidenceItemDB.created_at.desc())
        if status:
            statement = statement.where(RawEvidenceItemDB.status == status)
        if country:
            statement = statement.where(RawEvidenceItemDB.country == country)
        if scene_type:
            statement = statement.where(RawEvidenceItemDB.scene_type == scene_type)
        if source_type:
            statement = statement.where(RawEvidenceItemDB.source_type == source_type)
        if curation_run_id:
            statement = statement.where(RawEvidenceItemDB.curation_run_id == curation_run_id)
        statement = statement.limit(limit)
        with self.session_factory() as session:
            return [_raw_evidence_payload(row) for row in session.scalars(statement).all()]

    def list_candidate_drafts(
        self,
        *,
        status: str | None = None,
        country: str | None = None,
        scene_type: str | None = None,
        source_type: str | None = None,
        curation_run_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        statement = select(CandidateDraftDB).order_by(CandidateDraftDB.created_at.desc())
        if status:
            statement = statement.where(CandidateDraftDB.status == status)
        if country:
            statement = statement.where(CandidateDraftDB.country == country)
        if scene_type:
            statement = statement.where(CandidateDraftDB.scene_type == scene_type)
        if source_type:
            statement = statement.where(CandidateDraftDB.source_type == source_type)
        if curation_run_id:
            statement = statement.where(CandidateDraftDB.curation_run_id == curation_run_id)
        statement = statement.limit(limit)
        with self.session_factory() as session:
            return [_candidate_draft_payload(row) for row in session.scalars(statement).all()]

    def seed_discovery_tasks(
        self,
        *,
        regions: list[str],
        target_countries: list[str],
        discovery_config: dict[str, Any],
    ) -> int:
        source_priorities = {
            source_type: int(config.get("priority", 100))
            for source_type, config in discovery_config.get("source_types", {}).items()
        }
        scenes = discovery_config.get("scenes", {})
        created = 0
        with self.session_factory.begin() as session:
            for country in target_countries:
                region = _region_for_country(country, regions)
                for scene_type, scene_config in scenes.items():
                    scene_label = scene_config.get("scene_label", scene_type)
                    templates_by_source = scene_config.get("query_templates", {})
                    for source_type, templates in templates_by_source.items():
                        progress = _get_or_create_progress(
                            session,
                            region=region,
                            country=country,
                            scene_type=scene_type,
                            source_type=source_type,
                        )
                        if progress.status == "exhausted":
                            continue
                        for template_index, template in enumerate(templates or []):
                            query = template.format(
                                country=country,
                                scene_type=scene_type,
                                scene_label=scene_label,
                            )
                            priority = discovery_task_priority(
                                scene_config=scene_config,
                                source_priorities=source_priorities,
                                source_type=source_type,
                                template_index=template_index,
                            )
                            existing = session.scalars(
                                select(DiscoveryTaskDB).where(
                                    DiscoveryTaskDB.country == country,
                                    DiscoveryTaskDB.scene_type == scene_type,
                                    DiscoveryTaskDB.source_type == source_type,
                                    DiscoveryTaskDB.query_template == template,
                                    DiscoveryTaskDB.cycle_number == progress.cycle_number,
                                )
                            ).first()
                            if existing is not None:
                                if existing.priority != priority:
                                    existing.priority = priority
                                continue
                            session.add(
                                DiscoveryTaskDB(
                                    id=str(uuid4()),
                                    region=region,
                                    country=country,
                                    scene_type=scene_type,
                                    source_type=source_type,
                                    query_template=template,
                                    query=query,
                                    priority=priority,
                                    cycle_number=progress.cycle_number,
                                    status="queued",
                                )
                            )
                            created += 1
        return created

    def claim_discovery_tasks(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int = 300,
        recheck_after_seconds: int = 86_400,
    ) -> list[DiscoveryTaskLease]:
        if limit <= 0:
            return []
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(seconds=recheck_after_seconds)
        due = or_(
            DiscoveryTaskDB.next_due_at.is_(None),
            DiscoveryTaskDB.next_due_at <= now,
        )
        claimable = or_(
            DiscoveryTaskDB.status == "queued",
            DiscoveryTaskDB.status == "failed",
            and_(
                DiscoveryTaskDB.status == "leased",
                DiscoveryTaskDB.lease_expires_at.is_not(None),
                DiscoveryTaskDB.lease_expires_at <= now,
            ),
            and_(
                DiscoveryTaskDB.status == "completed",
                DiscoveryTaskDB.updated_at <= stale_cutoff,
            ),
        )
        with self.session_factory.begin() as session:
            rows = session.scalars(
                select(DiscoveryTaskDB)
                .where(due, claimable)
                .order_by(DiscoveryTaskDB.priority, DiscoveryTaskDB.updated_at)
                .limit(limit)
            ).all()
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            leases: list[DiscoveryTaskLease] = []
            for row in rows:
                progress = _find_progress(
                    session,
                    country=row.country,
                    scene_type=row.scene_type,
                    source_type=row.source_type,
                    cycle_number=row.cycle_number,
                )
                if progress is not None and progress.status == "exhausted":
                    continue
                row.status = "leased"
                row.lease_owner = worker_id
                row.lease_expires_at = lease_expires_at
                row.attempts += 1
                row.updated_at = now
                leases.append(_discovery_task_lease(row))
            return leases

    def start_discovery_run(self, *, worker_id: str) -> str:
        run_id = str(uuid4())
        with self.session_factory.begin() as session:
            session.add(
                DiscoveryRunDB(
                    id=run_id,
                    worker_id=worker_id,
                    status="running",
                )
            )
        return run_id

    def complete_discovery_task(
        self,
        *,
        task_id: str,
        run_id: str,
        status: str,
        error: str | None = None,
        failure_class: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.get(DiscoveryTaskDB, task_id)
            if row is None:
                return
            row.status = status
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_run_id = run_id
            row.last_error = error
            row.failure_class = failure_class
            row.next_due_at = (
                _retry_due_at(now, failure_class) if status == "failed" else None
            )
            row.updated_at = now

    def finish_discovery_run(
        self,
        *,
        run_id: str,
        status: str,
        searched_count: int,
        fetched_count: int,
        discovered_count: int,
        new_count: int,
        changed_count: int,
        duplicate_count: int,
        failed_count: int,
        countries: list[str],
        errors: list[str],
        known_property_skipped_count: int = 0,
        known_url_skipped_count: int = 0,
        possible_duplicate_review_count: int = 0,
        new_opportunity_count: int = 0,
        existing_property_evidence_update_count: int = 0,
        firecrawl_requests_saved_estimate: int = 0,
    ) -> None:
        with self.session_factory.begin() as session:
            row = session.get(DiscoveryRunDB, run_id)
            if row is None:
                return
            row.status = status
            row.completed_at = datetime.now(UTC)
            row.searched_count = searched_count
            row.fetched_count = fetched_count
            row.discovered_count = discovered_count
            row.new_count = new_count
            row.changed_count = changed_count
            row.duplicate_count = duplicate_count
            row.failed_count = failed_count
            row.known_property_skipped_count = known_property_skipped_count
            row.known_url_skipped_count = known_url_skipped_count
            row.possible_duplicate_review_count = possible_duplicate_review_count
            row.new_opportunity_count = new_opportunity_count
            row.existing_property_evidence_update_count = (
                existing_property_evidence_update_count
            )
            row.firecrawl_requests_saved_estimate = firecrawl_requests_saved_estimate
            row.countries = sorted(set(countries))
            row.errors = errors[:50]

    def settle_completed_progress_groups(
        self,
        group_stats: dict[tuple[str, str, str, str], dict[str, int]] | None = None,
    ) -> DiscoveryProgressSettlement:
        """Advance clean discovery cycles after their evidence has been curated."""
        stats = group_stats or {}
        now = datetime.now(UTC)
        settled_groups: list[dict[str, Any]] = []
        exhausted_groups: list[dict[str, Any]] = []
        blocked_groups: list[dict[str, Any]] = []
        with self.session_factory.begin() as session:
            progress_rows = session.scalars(
                select(DiscoveryProgressDB)
                .where(DiscoveryProgressDB.status != "exhausted")
                .order_by(
                    DiscoveryProgressDB.region,
                    DiscoveryProgressDB.country,
                    DiscoveryProgressDB.scene_type,
                    DiscoveryProgressDB.source_type,
                )
            ).all()
            for progress in progress_rows:
                tasks = session.scalars(
                    select(DiscoveryTaskDB).where(
                        DiscoveryTaskDB.country == progress.country,
                        DiscoveryTaskDB.scene_type == progress.scene_type,
                        DiscoveryTaskDB.source_type == progress.source_type,
                        DiscoveryTaskDB.cycle_number == progress.cycle_number,
                    )
                ).all()
                if not tasks or any(task.status in {"queued", "leased"} for task in tasks):
                    continue
                failure_classes = {
                    task.failure_class or "unknown"
                    for task in tasks
                    if task.status == "failed"
                }
                if failure_classes:
                    progress.status = "blocked_review"
                    progress.last_error = "; ".join(sorted(failure_classes))
                    progress.updated_at = now
                    blocked_groups.append(_discovery_progress_payload(progress))
                    continue

                key = _progress_key(
                    progress.region,
                    progress.country,
                    progress.scene_type,
                    progress.source_type,
                )
                group = stats.get(key, {})
                accepted_new = int(group.get("accepted_new_count", 0))
                draft_review = int(group.get("draft_review_count", 0))
                progress.accepted_new_last_cycle = accepted_new
                progress.accepted_new_total += accepted_new
                progress.draft_review_total += draft_review
                progress.last_completed_at = now
                progress.last_error = None
                if accepted_new > 0:
                    progress.no_new_cycles = 0
                    progress.status = "active"
                else:
                    progress.no_new_cycles += 1
                    progress.status = "blocked_review" if draft_review else "active"
                if progress.no_new_cycles >= EXHAUSTED_NO_NEW_CYCLES:
                    progress.status = "exhausted"
                    progress.exhausted_at = now
                else:
                    progress.cycle_number += 1
                progress.updated_at = now
                payload = _discovery_progress_payload(progress)
                settled_groups.append(payload)
                if progress.status == "exhausted":
                    exhausted_groups.append(payload)
        return DiscoveryProgressSettlement(
            settled_groups=settled_groups,
            exhausted_groups=exhausted_groups,
            blocked_groups=blocked_groups,
        )

    def seed_expansion_tasks_from_candidates(
        self,
        drafts: list[CandidateDraft],
        discovery_config: dict[str, Any],
    ) -> int:
        """Add city-scene follow-up searches when a new candidate expands a market."""
        created = 0
        source_priorities = {
            source_type: int(config.get("priority", 100))
            for source_type, config in discovery_config.get("source_types", {}).items()
        }
        with self.session_factory.begin() as session:
            for draft in drafts:
                scene_config = discovery_config.get("scenes", {}).get(draft.scene_type, {})
                scene_label = scene_config.get("scene_label", draft.scene_type)
                templates = {
                    "operator_venue": [
                        "{city} {scene_label} official capacity {country}",
                    ],
                    "secondary_reference": [
                        "{city} largest {scene_label} {country}",
                    ],
                }
                for source_type, query_templates in templates.items():
                    progress = _get_or_create_progress(
                        session,
                        region=draft.region,
                        country=draft.country,
                        scene_type=draft.scene_type,
                        source_type=source_type,
                    )
                    if progress.status == "exhausted":
                        continue
                    for template_index, template in enumerate(query_templates):
                        query = template.format(
                            city=draft.city,
                            country=draft.country,
                            scene_label=scene_label,
                        )
                        existing = session.scalars(
                            select(DiscoveryTaskDB).where(
                                DiscoveryTaskDB.country == draft.country,
                                DiscoveryTaskDB.scene_type == draft.scene_type,
                                DiscoveryTaskDB.source_type == source_type,
                                DiscoveryTaskDB.query_template == template,
                                DiscoveryTaskDB.query == query,
                                DiscoveryTaskDB.cycle_number == progress.cycle_number,
                            )
                        ).first()
                        if existing is not None:
                            continue
                        session.add(
                            DiscoveryTaskDB(
                                id=str(uuid4()),
                                region=draft.region,
                                country=draft.country,
                                scene_type=draft.scene_type,
                                source_type=source_type,
                                query_template=template,
                                query=query,
                                priority=source_priorities.get(source_type, 100)
                                + 50
                                + template_index,
                                cycle_number=progress.cycle_number,
                                status="queued",
                            )
                        )
                        created += 1
        return created

    def all_target_progress_exhausted(self, target_countries: list[str]) -> bool:
        country_set = {country.casefold() for country in target_countries}
        with self.session_factory() as session:
            rows = session.scalars(select(DiscoveryProgressDB)).all()
            target_rows = [
                row for row in rows if row.country.casefold() in country_set
            ]
        return bool(target_rows) and all(row.status == "exhausted" for row in target_rows)

    def discovery_status(self) -> dict[str, Any]:
        with self.session_factory() as session:
            tasks = session.scalars(select(DiscoveryTaskDB)).all()
            progress_rows = session.scalars(
                select(DiscoveryProgressDB).order_by(
                    DiscoveryProgressDB.region,
                    DiscoveryProgressDB.country,
                    DiscoveryProgressDB.scene_type,
                    DiscoveryProgressDB.source_type,
                )
            ).all()
            latest_run = session.scalars(
                select(DiscoveryRunDB).order_by(DiscoveryRunDB.started_at.desc()).limit(1)
            ).first()
            pending_evidence_count = len(
                session.scalars(
                    select(RawEvidenceItemDB).where(
                        RawEvidenceItemDB.status == "new",
                        RawEvidenceItemDB.curation_needed.is_(True),
                    )
                ).all()
            )
        task_counts: dict[str, int] = {}
        for task in tasks:
            task_counts[task.status] = task_counts.get(task.status, 0) + 1
        progress_status_counts: dict[str, int] = {}
        for progress in progress_rows:
            progress_status_counts[progress.status] = (
                progress_status_counts.get(progress.status, 0) + 1
            )
        return {
            "task_backlog": task_counts,
            "progress_status": progress_status_counts,
            "progress": [_discovery_progress_payload(row) for row in progress_rows],
            "pending_evidence_count": pending_evidence_count,
            "raw_evidence_status": self.raw_status_counts(),
            "latest_run": _discovery_run_payload(latest_run) if latest_run else None,
        }


def raw_evidence_to_candidate_draft(raw: RawEvidenceItemDB) -> CandidateDraft:
    payload = raw.payload or {}
    return CandidateDraft(
        region=raw.region,
        country=raw.country,
        city=raw.city or "",
        property_name=raw.property_name or "",
        scene_type=raw.scene_type or "",
        annual_visits=float(raw.annual_visits) if raw.annual_visits is not None else None,
        latitude=float(raw.latitude or 0),
        longitude=float(raw.longitude or 0),
        geocode_precision=raw.geocode_precision or "",
        map_source=raw.map_source or "",
        map_source_date=raw.map_source_date or "",
        field_group=raw.field_group or "",
        indicator_name=raw.indicator_name or "",
        field_value=raw.field_value or "",
        source_name=raw.source_name,
        source_tier=raw.source_tier,
        source_url=raw.source_url,
        source_date=raw.source_date or "",
        evidence_type=raw.evidence_type,
        bbox=payload.get("bbox") or {},
        source_type=raw.source_type or payload.get("source_type"),
        content_text=payload.get("content_text"),
        discovery_task_id=payload.get("discovery_task_id"),
        identity_match_status=payload.get("identity_match_status") or "new_opportunity",
        matched_property_id=payload.get("matched_property_id"),
        matched_property_name=payload.get("matched_property_name"),
        identity_match_reason=payload.get("identity_match_reason"),
        hero_image=payload.get("hero_image"),
    )


class DatabaseSourceCache:
    def __init__(self, store: EvidenceCurationStore) -> None:
        self.store = store

    def get(self, url: str) -> FetchedPage | None:
        with self.store.session_factory() as session:
            row = session.get(SourceCacheDB, url)
            if row is None:
                return None
            return FetchedPage(
                source_url=row.source_url,
                source_name=row.source_name,
                source_tier=SourceTier(row.source_tier),
                source_date=row.source_date,
                fetched_at=row.fetched_at,
                content_text=row.content_text,
                robots_allowed=row.robots_allowed,
            )

    def put(self, page: FetchedPage) -> FetchedPage:
        content_hash = hashlib.sha256(page.content_text.encode("utf-8")).hexdigest()
        with self.store.session_factory.begin() as session:
            existing = session.get(SourceCacheDB, str(page.source_url))
            if existing is None:
                session.add(
                    SourceCacheDB(
                        source_url=str(page.source_url),
                        source_name=page.source_name,
                        source_tier=getattr(page.source_tier, "value", str(page.source_tier)),
                        source_date=page.source_date,
                        fetched_at=page.fetched_at,
                        content_hash=content_hash,
                        content_text=page.content_text,
                        robots_allowed=page.robots_allowed,
                    )
                )
            else:
                existing.source_name = page.source_name
                existing.source_tier = getattr(page.source_tier, "value", str(page.source_tier))
                existing.source_date = page.source_date
                existing.fetched_at = page.fetched_at
                existing.content_hash = content_hash
                existing.content_text = page.content_text
                existing.robots_allowed = page.robots_allowed
        return page

    def content_hash(self, url: str) -> str | None:
        with self.store.session_factory() as session:
            row = session.get(SourceCacheDB, url)
            return row.content_hash if row is not None else None


def _progress_key(
    region: str,
    country: str,
    scene_type: str,
    source_type: str,
) -> tuple[str, str, str, str]:
    return (
        region.casefold(),
        country.casefold(),
        scene_type.casefold(),
        source_type.casefold(),
    )


def _get_or_create_progress(
    session: Session,
    *,
    region: str,
    country: str,
    scene_type: str,
    source_type: str,
) -> DiscoveryProgressDB:
    progress = session.scalars(
        select(DiscoveryProgressDB).where(
            DiscoveryProgressDB.country == country,
            DiscoveryProgressDB.scene_type == scene_type,
            DiscoveryProgressDB.source_type == source_type,
        )
    ).first()
    if progress is not None:
        return progress
    progress = DiscoveryProgressDB(
        id=str(uuid4()),
        region=region,
        country=country,
        scene_type=scene_type,
        source_type=source_type,
        status="active",
        cycle_number=1,
    )
    session.add(progress)
    return progress


def _find_progress(
    session: Session,
    *,
    country: str,
    scene_type: str,
    source_type: str,
    cycle_number: int,
) -> DiscoveryProgressDB | None:
    return session.scalars(
        select(DiscoveryProgressDB).where(
            DiscoveryProgressDB.country == country,
            DiscoveryProgressDB.scene_type == scene_type,
            DiscoveryProgressDB.source_type == source_type,
            DiscoveryProgressDB.cycle_number == cycle_number,
        )
    ).first()


def _retry_due_at(now: datetime, failure_class: str | None) -> datetime:
    if failure_class == "rate_limit":
        return now + timedelta(minutes=30)
    if failure_class in {"network", "http_error"}:
        return now + timedelta(hours=1)
    if failure_class == "robots":
        return now + timedelta(days=7)
    return now + timedelta(hours=6)


def _discovery_progress_payload(row: DiscoveryProgressDB) -> dict[str, Any]:
    return {
        "id": row.id,
        "region": row.region,
        "country": row.country,
        "scene_type": row.scene_type,
        "source_type": row.source_type,
        "status": row.status,
        "cycle_number": row.cycle_number,
        "no_new_cycles": row.no_new_cycles,
        "accepted_new_total": row.accepted_new_total,
        "accepted_new_last_cycle": row.accepted_new_last_cycle,
        "draft_review_total": row.draft_review_total,
        "last_completed_at": (
            row.last_completed_at.isoformat() if row.last_completed_at else None
        ),
        "exhausted_at": row.exhausted_at.isoformat() if row.exhausted_at else None,
        "last_error": row.last_error,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _raw_evidence_payload(row: RawEvidenceItemDB) -> dict[str, Any]:
    return {
        "id": row.id,
        "region": row.region,
        "country": row.country,
        "city": row.city,
        "property_name": row.property_name,
        "scene_type": row.scene_type,
        "source_type": row.source_type,
        "source_url": row.source_url,
        "source_name": row.source_name,
        "source_tier": row.source_tier,
        "source_date": row.source_date,
        "field_group": row.field_group,
        "indicator_name": row.indicator_name,
        "field_value": row.field_value,
        "content_hash": row.content_hash,
        "dedupe_key": row.dedupe_key,
        "status": row.status,
        "curation_needed": row.curation_needed,
        "curation_run_id": row.curation_run_id,
        "curated_at": row.curated_at.isoformat() if row.curated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "identity_match_status": (row.payload or {}).get("identity_match_status"),
        "matched_property_name": (row.payload or {}).get("matched_property_name"),
    }


def _candidate_draft_payload(row: CandidateDraftDB) -> dict[str, Any]:
    return {
        "id": row.id,
        "curation_run_id": row.curation_run_id,
        "raw_evidence_ids": row.raw_evidence_ids,
        "country": row.country,
        "city": row.city,
        "property_name": row.property_name,
        "scene_type": row.scene_type,
        "source_type": row.source_type,
        "status": row.status,
        "issues": row.issues,
        "candidate_payload": row.candidate_payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def raw_evidence_dedupe_key(draft: CandidateDraft) -> str:
    country, city, property_name, scene_type = candidate_key(
        draft.country,
        draft.city,
        draft.property_name,
        draft.scene_type,
    )
    parts = [
        country,
        city,
        property_name,
        scene_type,
        normalize_text(draft.field_group),
        normalize_text(draft.indicator_name),
        normalize_source_url(draft.source_url),
    ]
    return "|".join(parts)


def raw_evidence_content_hash(
    draft: CandidateDraft,
    content_text: str | None = None,
) -> str:
    payload = {
        "country": draft.country,
        "city": draft.city,
        "property_name": draft.property_name,
        "scene_type": draft.scene_type,
        "annual_visits": draft.annual_visits,
        "latitude": draft.latitude,
        "longitude": draft.longitude,
        "geocode_precision": draft.geocode_precision,
        "map_source": draft.map_source,
        "map_source_date": draft.map_source_date,
        "field_group": draft.field_group,
        "indicator_name": draft.indicator_name,
        "field_value": draft.field_value,
        "source_name": draft.source_name,
        "source_tier": draft.source_tier,
        "source_url": draft.source_url,
        "source_date": draft.source_date,
        "evidence_type": draft.evidence_type,
        "content_text": content_text or "",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _discovery_task_lease(row: DiscoveryTaskDB) -> DiscoveryTaskLease:
    return DiscoveryTaskLease(
        id=row.id,
        region=row.region,
        country=row.country,
        scene_type=row.scene_type,
        source_type=row.source_type,
        query_template=row.query_template,
        query=row.query,
        priority=row.priority,
        cycle_number=row.cycle_number,
    )


def _discovery_run_payload(row: DiscoveryRunDB) -> dict[str, Any]:
    return {
        "id": row.id,
        "worker_id": row.worker_id,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "searched_count": row.searched_count,
        "fetched_count": row.fetched_count,
        "discovered_count": row.discovered_count,
        "new_count": row.new_count,
        "changed_count": row.changed_count,
        "duplicate_count": row.duplicate_count,
        "failed_count": row.failed_count,
        "known_property_skipped_count": row.known_property_skipped_count,
        "known_url_skipped_count": row.known_url_skipped_count,
        "possible_duplicate_review_count": row.possible_duplicate_review_count,
        "new_opportunity_count": row.new_opportunity_count,
        "existing_property_evidence_update_count": (
            row.existing_property_evidence_update_count
        ),
        "firecrawl_requests_saved_estimate": row.firecrawl_requests_saved_estimate,
        "countries": row.countries or [],
        "errors": row.errors or [],
    }


def _region_for_country(country: str, regions: list[str]) -> str:
    for region in regions:
        if country in REGION_COUNTRIES.get(region, []):
            return region
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return regions[0] if regions else "Unknown"


def _ensure_sqlite_parent(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    marker = ":///"
    if marker not in url:
        return
    path_text = url.split(marker, 1)[1]
    if path_text in {":memory:", ""}:
        return
    Path(path_text).parent.mkdir(parents=True, exist_ok=True)
