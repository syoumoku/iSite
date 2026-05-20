from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from isite2.db.models import (
    Base,
    BuildStatusDB,
    CandidateDraftDB,
    ConclusionDB,
    DemandEstimateDB,
    DiscoveryProgressDB,
    DiscoveryRunDB,
    DiscoveryTaskDB,
    EvidenceItemDB,
    InferenceRecordDB,
    OutputArtifactDB,
    PropertyDB,
    RagChunkDB,
    RagDocumentDB,
    RawEvidenceItemDB,
    ReviewQueueDB,
    ScanCandidateDB,
    ScanRunDB,
    SceneModelResultDB,
    SourceCacheDB,
)
from isite2.db.session import create_engine_for_url, create_session_factory
from isite2.domain.models import (
    BuildStatus,
    CandidateVisibility,
    Conclusion,
    DemandEstimate,
    EvidenceItem,
    InferenceRecord,
    PropertyEntity,
    PropertyHeroImage,
    ReviewItem,
    ScanRun,
    ScanRunResult,
    ScanScope,
    SceneModelResult,
    SitePacket,
)
from isite2.growth.property_identity import (
    ensure_property_identity_schema,
    property_identity_key,
)
from isite2.rules.coordinates import is_map_ready_coordinate
from isite2.rules.reason import make_google_maps_link


class SQLAlchemyScanRunRepository:
    def __init__(self, engine: Engine, storage_mode: str = "postgis") -> None:
        self.engine = engine
        self.storage_mode = storage_mode
        self.session_factory: sessionmaker[Session] = create_session_factory(engine)
        Base.metadata.create_all(engine)
        self._ensure_schema_compat()
        self._persisted_counts: dict[UUID, dict[str, int]] = {}

    @classmethod
    def from_url(cls, url: str, storage_mode: str | None = None) -> SQLAlchemyScanRunRepository:
        engine = create_engine_for_url(url)
        mode = storage_mode or ("postgis" if url.startswith("postgresql") else "sqlite")
        return cls(engine=engine, storage_mode=mode)

    def create(self, scope: ScanScope) -> ScanRunResult:
        return ScanRunResult(scan_run=ScanRun(scope=scope), storage_mode=self.storage_mode)

    def _ensure_schema_compat(self) -> None:
        with self.engine.begin() as connection:
            dialect = connection.dialect.name
            if dialect == "sqlite":
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql("PRAGMA table_info(properties)").all()
                }
                ensure_property_identity_schema(connection)
                if "coordinate_status" not in columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE properties "
                        "ADD COLUMN coordinate_status VARCHAR(32) NOT NULL DEFAULT 'Verified'"
                    )
                if "hero_image" not in columns:
                    connection.exec_driver_sql("ALTER TABLE properties ADD COLUMN hero_image JSON")
                scan_candidate_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(scan_candidates)"
                    ).all()
                }
                if scan_candidate_columns:
                    if "candidate_quality_status" not in scan_candidate_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE scan_candidates "
                            "ADD COLUMN candidate_quality_status VARCHAR(32) "
                            "NOT NULL DEFAULT 'ready'"
                        )
                    if "visibility" not in scan_candidate_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE scan_candidates ADD COLUMN visibility JSON"
                        )
                    if "quality_issues" not in scan_candidate_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE scan_candidates ADD COLUMN quality_issues JSON"
                        )
                review_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(review_queue)"
                    ).all()
                }
                if review_columns:
                    _sqlite_add_column_if_missing(
                        connection,
                        review_columns,
                        "review_queue",
                        "review_type",
                        "VARCHAR(64) NOT NULL DEFAULT 'general'",
                    )
                    _sqlite_add_column_if_missing(
                        connection,
                        review_columns,
                        "review_queue",
                        "severity",
                        "VARCHAR(32) NOT NULL DEFAULT 'medium'",
                    )
                    _sqlite_add_column_if_missing(
                        connection,
                        review_columns,
                        "review_queue",
                        "gate_name",
                        "VARCHAR(128)",
                    )
                    _sqlite_add_column_if_missing(
                        connection,
                        review_columns,
                        "review_queue",
                        "field_path",
                        "TEXT",
                    )
                    _sqlite_add_column_if_missing(
                        connection,
                        review_columns,
                        "review_queue",
                        "blocking_surfaces",
                        "JSON",
                    )
                    _sqlite_add_column_if_missing(
                        connection,
                        review_columns,
                        "review_queue",
                        "source_url",
                        "TEXT",
                    )
                    _sqlite_add_column_if_missing(
                        connection,
                        review_columns,
                        "review_queue",
                        "suggested_query",
                        "TEXT",
                    )
                discovery_task_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(discovery_tasks)"
                    ).all()
                }
                if discovery_task_columns:
                    if "cycle_number" not in discovery_task_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE discovery_tasks "
                            "ADD COLUMN cycle_number INTEGER NOT NULL DEFAULT 1"
                        )
                    if "next_due_at" not in discovery_task_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE discovery_tasks ADD COLUMN next_due_at DATETIME"
                        )
                    if "failure_class" not in discovery_task_columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE discovery_tasks ADD COLUMN failure_class VARCHAR(64)"
                        )
            elif dialect == "postgresql":
                ensure_property_identity_schema(connection)
                connection.exec_driver_sql(
                    "ALTER TABLE properties "
                    "ADD COLUMN IF NOT EXISTS coordinate_status TEXT NOT NULL DEFAULT 'Verified'"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS hero_image JSON"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE scan_candidates "
                    "ADD COLUMN IF NOT EXISTS candidate_quality_status TEXT "
                    "NOT NULL DEFAULT 'ready'"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS visibility JSON"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE scan_candidates ADD COLUMN IF NOT EXISTS quality_issues JSON"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE review_queue "
                    "ADD COLUMN IF NOT EXISTS review_type TEXT NOT NULL DEFAULT 'general'"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE review_queue "
                    "ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT 'medium'"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS gate_name TEXT"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS field_path TEXT"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS blocking_surfaces JSON"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS source_url TEXT"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS suggested_query TEXT"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE discovery_tasks "
                    "ADD COLUMN IF NOT EXISTS cycle_number INTEGER NOT NULL DEFAULT 1"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE discovery_tasks ADD COLUMN IF NOT EXISTS next_due_at TIMESTAMPTZ"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE discovery_tasks ADD COLUMN IF NOT EXISTS failure_class TEXT"
                )
                try:
                    connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
                    dim = int(os.getenv("RAG_EMBEDDING_DIM", "64"))
                    connection.exec_driver_sql(
                        "ALTER TABLE rag_chunks "
                        f"ADD COLUMN IF NOT EXISTS embedding_vector vector({dim})"
                    )
                except SQLAlchemyError:
                    # pgvector is a production optimization; JSON embeddings keep local
                    # and restricted Postgres environments functional.
                    pass

    def save(self, result: ScanRunResult) -> ScanRunResult:
        counts = _empty_counts()
        with self.session_factory.begin() as session:
            session.merge(
                ScanRunDB(
                    id=str(result.scan_run.run_id),
                    scope=result.scan_run.scope.model_dump(mode="json"),
                    status=result.scan_run.status,
                    created_at=result.scan_run.created_at,
                    completed_at=datetime.now(UTC),
                )
            )
            counts["scan_runs"] = 1

            seen_source_urls: set[str] = set()
            for packet in result.packets:
                property_db = self._upsert_property(session, packet.entity)
                packet.entity.property_id = UUID(str(property_db.id))
                counts["properties"] += 1

                session.add(
                    ScanCandidateDB(
                        id=str(uuid4()),
                        scan_run_id=str(result.scan_run.run_id),
                        property_id=property_db.id,
                        raw_name=packet.entity.property_name,
                        country=packet.entity.country,
                        city=packet.entity.city,
                        scene_type=packet.entity.scene_type,
                        discovery_source=packet.entity.map_source,
                        status=(
                            "blocked_quality"
                            if packet.candidate_quality_status == "blocked_quality"
                            else "packet_completed"
                        ),
                        candidate_quality_status=packet.candidate_quality_status,
                        visibility=packet.visibility.model_dump(mode="json"),
                        quality_issues=list(packet.quality_issues),
                    )
                )
                counts["scan_candidates"] += 1

                for evidence in packet.evidence:
                    evidence.property_id = packet.entity.property_id
                    session.add(_evidence_db(evidence, result.scan_run.run_id))
                    if str(evidence.source_url) not in seen_source_urls:
                        self._upsert_source_cache(session, evidence)
                        seen_source_urls.add(str(evidence.source_url))
                    counts["evidence_items"] += 1
                session.add(_scene_db(packet, result.scan_run.run_id))
                counts["scene_model_results"] += 1

                session.add(_build_db(packet, result.scan_run.run_id))
                counts["build_statuses"] += 1

                if packet.demand is not None:
                    session.add(_demand_db(packet, result.scan_run.run_id))
                    counts["demand_estimates"] += 1

                for inference in packet.inference:
                    session.add(_inference_db(packet, inference, result.scan_run.run_id))
                    counts["inference_records"] += 1

                session.add(_conclusion_db(packet, result.scan_run.run_id))
                counts["conclusions"] += 1

                for review in packet.review_queue:
                    session.add(_review_db(packet, review, result.scan_run.run_id))
                    counts["review_queue"] += 1

        counts = self.persisted_counts(result.scan_run.run_id)
        result.storage_mode = self.storage_mode
        result.persisted_counts = counts
        self._persisted_counts[result.scan_run.run_id] = counts
        return result

    def list(self) -> list[ScanRunResult]:
        with self.session_factory() as session:
            rows = session.scalars(select(ScanRunDB).order_by(ScanRunDB.created_at)).all()
            return [self._result_from_run(session, row, include_packets=False) for row in rows]

    def get(self, run_id: UUID) -> ScanRunResult | None:
        with self.session_factory() as session:
            row = session.get(ScanRunDB, str(run_id))
            if row is None:
                return None
            return self._result_from_run(session, row, include_packets=True)

    def list_properties(self, filters: dict[str, Any] | None = None) -> list[SitePacket]:
        filters = filters or {}
        scan_run_id = filters.get("scan_run_id")
        rest = {key: value for key, value in filters.items() if key != "scan_run_id"}
        candidate_filters = _candidate_row_prefilters(rest)
        with self.session_factory() as session:
            packets: list[SitePacket] = []
            if scan_run_id is None:
                packets = self._latest_packets_per_property(session, candidate_filters)
            else:
                run = session.get(ScanRunDB, str(scan_run_id))
                if run is not None:
                    packets.extend(
                        self._packets_for_run(session, UUID(str(run.id)), candidate_filters)
                    )
            return [packet for packet in packets if _packet_matches(packet, rest)]

    def list_city_summaries(
        self,
        filters: dict[str, Any] | None = None,
        *,
        include_blocked_quality: bool = False,
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        scan_run_id = filters.get("scan_run_id")
        rest = {key: value for key, value in filters.items() if key != "scan_run_id"}
        candidate_filters = _candidate_row_prefilters(rest)
        with self.session_factory() as session:
            if scan_run_id is None:
                candidates = self._latest_candidate_rows_per_property(session, candidate_filters)
            else:
                run = session.get(ScanRunDB, str(scan_run_id))
                candidates = (
                    self._candidate_rows_for_run(session, UUID(str(run.id)), candidate_filters)
                    if run is not None
                    else []
                )
            candidates = [row for row in candidates if row.property_id is not None]
            if not candidates:
                return []

            property_ids = sorted({str(row.property_id) for row in candidates if row.property_id})
            scan_run_ids = sorted({str(row.scan_run_id) for row in candidates})
            property_rows = {
                str(row.id): row
                for row in session.scalars(
                    select(PropertyDB).where(PropertyDB.id.in_(property_ids))
                ).all()
            }
            conclusion_rows = _latest_rows_by_property_run(
                session.scalars(
                    select(ConclusionDB)
                    .where(
                        ConclusionDB.property_id.in_(property_ids),
                        ConclusionDB.scan_run_id.in_(scan_run_ids),
                    )
                    .order_by(ConclusionDB.created_at.desc())
                ).all()
            )
            scene_rows = _latest_rows_by_property_run(
                session.scalars(
                    select(SceneModelResultDB)
                    .where(
                        SceneModelResultDB.property_id.in_(property_ids),
                        SceneModelResultDB.scan_run_id.in_(scan_run_ids),
                    )
                    .order_by(SceneModelResultDB.created_at.desc())
                ).all()
            )
            build_rows = _latest_rows_by_property_run(
                session.scalars(
                    select(BuildStatusDB)
                    .where(
                        BuildStatusDB.property_id.in_(property_ids),
                        BuildStatusDB.scan_run_id.in_(scan_run_ids),
                    )
                    .order_by(BuildStatusDB.created_at.desc())
                ).all()
            )
            evidence_urls: dict[tuple[str, str], set[str]] = {}
            for row in session.scalars(
                select(EvidenceItemDB).where(
                    EvidenceItemDB.property_id.in_(property_ids),
                    EvidenceItemDB.scan_run_id.in_(scan_run_ids),
                )
            ).all():
                evidence_urls.setdefault(_property_run_key(row), set()).add(row.source_url)
            review_counts: dict[tuple[str, str], int] = {}
            for row in session.scalars(
                select(ReviewQueueDB).where(
                    ReviewQueueDB.property_id.in_(property_ids),
                    ReviewQueueDB.scan_run_id.in_(scan_run_ids),
                )
            ).all():
                key = _property_run_key(row)
                review_counts[key] = review_counts.get(key, 0) + 1

            city_rows: dict[str, dict[str, Any]] = {}
            for candidate in candidates:
                property_id = str(candidate.property_id)
                property_row = property_rows.get(property_id)
                if property_row is None or not property_row.city.strip():
                    continue
                key = (property_id, str(candidate.scan_run_id))
                conclusion_row = conclusion_rows.get(key)
                scene_row = scene_rows.get(key)
                build_row = build_rows.get(key)
                if conclusion_row is None or scene_row is None or build_row is None:
                    continue
                visibility = _candidate_visibility(candidate)
                if not include_blocked_quality and not visibility.main_table_ready:
                    continue
                review_count = review_counts.get(key, 0)
                if not _candidate_summary_matches(
                    candidate,
                    property_row,
                    conclusion_row,
                    scene_row,
                    build_row,
                    visibility,
                    review_count,
                    rest,
                ):
                    continue

                city_key_value = (
                    f"{property_row.country.strip().lower()}::"
                    f"{property_row.city.strip().lower()}"
                )
                row = city_rows.setdefault(
                    city_key_value,
                    {
                        "country": property_row.country,
                        "city": property_row.city,
                        "candidate_count": 0,
                        "map_point_count": 0,
                        "review_count": 0,
                        "source_count": 0,
                        "scenes": {},
                        "property_ids": [],
                        "_source_urls": set(),
                        "_map_lat_total": 0.0,
                        "_map_lng_total": 0.0,
                        "_map_count": 0,
                        "_all_lat_total": 0.0,
                        "_all_lng_total": 0.0,
                        "_all_count": 0,
                    },
                )
                row["candidate_count"] += 1
                row["review_count"] += review_count
                row["property_ids"].append(property_id)
                row["scenes"][property_row.scene_type] = (
                    row["scenes"].get(property_row.scene_type, 0) + 1
                )
                row["_source_urls"].update(evidence_urls.get(key, set()))
                row["_all_lat_total"] += property_row.latitude
                row["_all_lng_total"] += property_row.longitude
                row["_all_count"] += 1
                if is_map_ready_coordinate(property_row.coordinate_status):
                    row["map_point_count"] += 1
                    row["_map_lat_total"] += property_row.latitude
                    row["_map_lng_total"] += property_row.longitude
                    row["_map_count"] += 1

            summaries = []
            for row in city_rows.values():
                map_count = row.pop("_map_count")
                all_count = row.pop("_all_count")
                source_urls = row.pop("_source_urls")
                map_lat_total = row.pop("_map_lat_total")
                map_lng_total = row.pop("_map_lng_total")
                all_lat_total = row.pop("_all_lat_total")
                all_lng_total = row.pop("_all_lng_total")
                if map_count > 0:
                    row["lat"] = map_lat_total / map_count
                    row["lng"] = map_lng_total / map_count
                    row["position_source"] = "map_ready_average"
                elif all_count > 0:
                    row["lat"] = all_lat_total / all_count
                    row["lng"] = all_lng_total / all_count
                    row["position_source"] = "property_average"
                else:
                    continue
                row["source_count"] = len(source_urls)
                summaries.append(row)
            return sorted(
                summaries,
                key=lambda item: (-item["candidate_count"], item["country"], item["city"]),
            )

    def list_country_summaries(
        self,
        scan_run_id: UUID | None = None,
        *,
        include_blocked_quality: bool = False,
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            if scan_run_id is None:
                candidates = self._latest_candidate_rows_per_property(session)
            else:
                run = session.get(ScanRunDB, str(scan_run_id))
                candidates = (
                    _dedupe_candidate_rows_per_property(
                        self._candidate_rows_for_run(session, UUID(str(run.id)))
                    )
                    if run is not None
                    else []
                )
            candidates = [row for row in candidates if row.property_id is not None]
            if not candidates:
                return []

            property_ids = sorted({str(row.property_id) for row in candidates if row.property_id})
            scan_run_ids = sorted({str(row.scan_run_id) for row in candidates})
            property_rows = {
                str(row.id): row
                for row in session.scalars(
                    select(PropertyDB).where(PropertyDB.id.in_(property_ids))
                ).all()
            }
            evidence_urls: dict[tuple[str, str], set[str]] = {}
            for row in session.scalars(
                select(EvidenceItemDB).where(
                    EvidenceItemDB.property_id.in_(property_ids),
                    EvidenceItemDB.scan_run_id.in_(scan_run_ids),
                )
            ).all():
                evidence_urls.setdefault(_property_run_key(row), set()).add(row.source_url)
            review_counts: dict[tuple[str, str], int] = {}
            for row in session.scalars(
                select(ReviewQueueDB).where(
                    ReviewQueueDB.property_id.in_(property_ids),
                    ReviewQueueDB.scan_run_id.in_(scan_run_ids),
                )
            ).all():
                key = _property_run_key(row)
                review_counts[key] = review_counts.get(key, 0) + 1

            country_rows: dict[str, dict[str, Any]] = {}
            for candidate in candidates:
                property_id = str(candidate.property_id)
                property_row = property_rows.get(property_id)
                if property_row is None:
                    continue
                visibility = _candidate_visibility(candidate)
                if not include_blocked_quality and not visibility.main_table_ready:
                    continue
                key = (property_id, str(candidate.scan_run_id))
                row = country_rows.setdefault(
                    property_row.country,
                    {
                        "country": property_row.country,
                        "candidate_count": 0,
                        "map_point_count": 0,
                        "coordinate_review_count": 0,
                        "review_count": 0,
                        "source_count": 0,
                        "scenes": {},
                        "_source_urls": set(),
                    },
                )
                row["candidate_count"] += 1
                if is_map_ready_coordinate(property_row.coordinate_status):
                    row["map_point_count"] += 1
                if property_row.coordinate_status == "Review Required":
                    row["coordinate_review_count"] += 1
                row["review_count"] += review_counts.get(key, 0)
                row["_source_urls"].update(evidence_urls.get(key, set()))
                row["scenes"][property_row.scene_type] = (
                    row["scenes"].get(property_row.scene_type, 0) + 1
                )

            summaries = []
            for row in country_rows.values():
                source_urls = row.pop("_source_urls")
                row["source_count"] = len(source_urls)
                summaries.append(row)
            return sorted(summaries, key=lambda item: item["country"])

    def get_property(self, property_id: UUID) -> SitePacket | None:
        with self.session_factory() as session:
            candidate = session.scalar(
                select(ScanCandidateDB)
                .where(ScanCandidateDB.property_id == str(property_id))
                .order_by(ScanCandidateDB.created_at.desc())
            )
            if candidate is None:
                return None
            packet = self._packet_from_property(
                session,
                UUID(str(candidate.scan_run_id)),
                str(property_id),
            )
            return packet

    def clear(self) -> None:
        tables = [
            OutputArtifactDB,
            ReviewQueueDB,
            ConclusionDB,
            InferenceRecordDB,
            DemandEstimateDB,
            BuildStatusDB,
            SceneModelResultDB,
            EvidenceItemDB,
            SourceCacheDB,
            RagChunkDB,
            RagDocumentDB,
            CandidateDraftDB,
            RawEvidenceItemDB,
            DiscoveryTaskDB,
            DiscoveryRunDB,
            DiscoveryProgressDB,
            ScanCandidateDB,
            PropertyDB,
            ScanRunDB,
        ]
        with self.session_factory.begin() as session:
            for table in tables:
                session.execute(delete(table))
        self._persisted_counts.clear()

    def delete_runs(self, run_ids: list[UUID]) -> int:
        ids = [str(run_id) for run_id in run_ids]
        if not ids:
            return 0
        with self.session_factory.begin() as session:
            session.execute(delete(OutputArtifactDB).where(OutputArtifactDB.scan_run_id.in_(ids)))
            session.execute(delete(ReviewQueueDB).where(ReviewQueueDB.scan_run_id.in_(ids)))
            session.execute(delete(ConclusionDB).where(ConclusionDB.scan_run_id.in_(ids)))
            session.execute(delete(InferenceRecordDB).where(InferenceRecordDB.scan_run_id.in_(ids)))
            session.execute(delete(DemandEstimateDB).where(DemandEstimateDB.scan_run_id.in_(ids)))
            session.execute(delete(BuildStatusDB).where(BuildStatusDB.scan_run_id.in_(ids)))
            session.execute(
                delete(SceneModelResultDB).where(SceneModelResultDB.scan_run_id.in_(ids))
            )
            session.execute(delete(EvidenceItemDB).where(EvidenceItemDB.scan_run_id.in_(ids)))
            session.execute(delete(ScanCandidateDB).where(ScanCandidateDB.scan_run_id.in_(ids)))
            session.execute(delete(ScanRunDB).where(ScanRunDB.id.in_(ids)))
        for run_id in run_ids:
            self._persisted_counts.pop(run_id, None)
        return len(ids)

    def add_output_artifact(
        self,
        scan_run_id: UUID,
        artifact_type: str,
        path: str,
    ) -> dict[str, Any]:
        artifact = OutputArtifactDB(
            id=str(uuid4()),
            scan_run_id=str(scan_run_id),
            artifact_type=artifact_type,
            path=path,
            filter_snapshot={},
        )
        with self.session_factory.begin() as session:
            session.add(artifact)
        return {
            "artifact_id": artifact.id,
            "scan_run_id": artifact.scan_run_id,
            "artifact_type": artifact.artifact_type,
            "path": artifact.path,
        }

    def list_output_artifacts(self, scan_run_id: UUID) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(OutputArtifactDB).where(OutputArtifactDB.scan_run_id == str(scan_run_id))
            ).all()
            return [
                {
                    "artifact_id": row.id,
                    "scan_run_id": row.scan_run_id,
                    "artifact_type": row.artifact_type,
                    "path": row.path,
                }
                for row in rows
            ]

    def persisted_counts(self, run_id: UUID) -> dict[str, int]:
        if run_id in self._persisted_counts:
            return self._persisted_counts[run_id]
        with self.session_factory() as session:
            return _counts_for_run(session, run_id)

    def _upsert_property(self, session: Session, entity: PropertyEntity) -> PropertyDB:
        identity_key = property_identity_key(
            country=entity.country,
            city=entity.city,
            property_name=entity.property_name,
            scene_type=entity.scene_type,
        )
        existing = session.scalar(
            select(PropertyDB).where(
                PropertyDB.country == entity.country,
                PropertyDB.scene_type == entity.scene_type,
                PropertyDB.property_identity_key == identity_key,
            )
        )
        if existing is None:
            existing = session.scalar(
                select(PropertyDB).where(
                    PropertyDB.country == entity.country,
                    PropertyDB.city == entity.city,
                    PropertyDB.canonical_name == entity.property_name,
                    PropertyDB.scene_type == entity.scene_type,
                )
            )
        if existing is not None:
            existing.property_identity_key = identity_key
            existing.canonical_name = entity.property_name
            existing.city = entity.city
            existing.latitude = entity.latitude
            existing.longitude = entity.longitude
            existing.geocode_precision = entity.geocode_precision
            existing.map_source = entity.map_source
            existing.map_source_date = entity.map_source_date
            existing.google_maps_link = entity.google_maps_link
            existing.coordinate_status = entity.coordinate_status
            existing.hero_image = _hero_image_payload(entity)
            return existing
        row = PropertyDB(
            id=str(entity.property_id),
            property_identity_key=identity_key,
            canonical_name=entity.property_name,
            country=entity.country,
            city=entity.city,
            scene_type=entity.scene_type,
            scene_form=entity.scene_form.value,
            latitude=entity.latitude,
            longitude=entity.longitude,
            geocode_precision=entity.geocode_precision,
            map_source=entity.map_source,
            map_source_date=entity.map_source_date,
            google_maps_link=entity.google_maps_link,
            coordinate_status=entity.coordinate_status,
            hero_image=_hero_image_payload(entity),
        )
        session.add(row)
        return row

    def _result_from_run(
        self,
        session: Session,
        row: ScanRunDB,
        include_packets: bool,
    ) -> ScanRunResult:
        run_id = UUID(str(row.id))
        packets = self._packets_for_run(session, run_id) if include_packets else []
        return ScanRunResult(
            scan_run=ScanRun(
                run_id=run_id,
                scope=ScanScope.model_validate(row.scope),
                status=row.status,
                created_at=row.created_at,
                candidate_count=len(self._candidate_rows_for_run(session, run_id)),
                review_count=len(self._review_rows_for_run(session, run_id)),
            ),
            packets=packets,
            review_items=[
                _review_item(review_row)
                for review_row in self._review_rows_for_run(session, run_id)
            ],
            storage_mode=self.storage_mode,
            persisted_counts=self.persisted_counts(run_id),
        )

    def _packets_for_run(
        self,
        session: Session,
        run_id: UUID,
        candidate_filters: list[Any] | None = None,
    ) -> list[SitePacket]:
        candidates = self._candidate_rows_for_run(session, run_id, candidate_filters)
        packets = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.property_id is None or candidate.property_id in seen:
                continue
            seen.add(candidate.property_id)
            packet = self._packet_from_property(session, run_id, candidate.property_id)
            if packet is not None:
                packets.append(packet)
        return packets

    def _packet_from_property(
        self,
        session: Session,
        run_id: UUID,
        property_id: str,
    ) -> SitePacket | None:
        property_row = session.get(PropertyDB, property_id)
        if property_row is None:
            return None
        evidence_rows = session.scalars(
            select(EvidenceItemDB).where(
                EvidenceItemDB.property_id == property_id,
                EvidenceItemDB.scan_run_id == str(run_id),
            )
        ).all()
        scene_row = session.scalar(
            select(SceneModelResultDB).where(
                SceneModelResultDB.property_id == property_id,
                SceneModelResultDB.scan_run_id == str(run_id),
            )
        )
        build_row = session.scalar(
            select(BuildStatusDB).where(
                BuildStatusDB.property_id == property_id,
                BuildStatusDB.scan_run_id == str(run_id),
            )
        )
        conclusion_row = session.scalar(
            select(ConclusionDB).where(
                ConclusionDB.property_id == property_id,
                ConclusionDB.scan_run_id == str(run_id),
            )
        )
        if scene_row is None or build_row is None or conclusion_row is None:
            return None
        demand_row = session.scalar(
            select(DemandEstimateDB).where(
                DemandEstimateDB.property_id == property_id,
                DemandEstimateDB.scan_run_id == str(run_id),
            )
        )
        inference_rows = session.scalars(
            select(InferenceRecordDB).where(
                InferenceRecordDB.property_id == property_id,
                InferenceRecordDB.scan_run_id == str(run_id),
            )
        ).all()
        review_rows = session.scalars(
            select(ReviewQueueDB).where(
                ReviewQueueDB.property_id == property_id,
                ReviewQueueDB.scan_run_id == str(run_id),
            )
        ).all()
        candidate_row = session.scalar(
            select(ScanCandidateDB)
            .where(
                ScanCandidateDB.property_id == property_id,
                ScanCandidateDB.scan_run_id == str(run_id),
            )
            .order_by(ScanCandidateDB.created_at.desc())
        )
        return SitePacket(
            entity=_property_entity(property_row),
            scene=_scene_model(scene_row),
            evidence=[_evidence_item(row) for row in evidence_rows],
            build_status=_build_status(build_row),
            demand=_demand_estimate(demand_row) if demand_row is not None else None,
            inference=[_inference_record(row) for row in inference_rows],
            conclusion=_conclusion(conclusion_row),
            review_queue=[_review_item(row) for row in review_rows],
            candidate_quality_status=(
                candidate_row.candidate_quality_status
                if candidate_row is not None
                else "ready"
            ),
            visibility=_candidate_visibility(candidate_row),
            quality_issues=(
                list(_json_payload(candidate_row.quality_issues, []))
                if candidate_row is not None
                else []
            ),
        )

    def _candidate_rows_for_run(
        self,
        session: Session,
        run_id: UUID,
        candidate_filters: list[Any] | None = None,
    ) -> list[ScanCandidateDB]:
        statement = (
            select(ScanCandidateDB)
            .where(ScanCandidateDB.scan_run_id == str(run_id))
            .order_by(ScanCandidateDB.created_at)
        )
        if candidate_filters:
            statement = statement.where(*candidate_filters)
        return session.scalars(statement).all()

    def _review_rows_for_run(self, session: Session, run_id: UUID) -> list[ReviewQueueDB]:
        return session.scalars(
            select(ReviewQueueDB).where(ReviewQueueDB.scan_run_id == str(run_id))
        ).all()

    def _latest_candidate_rows_per_property(
        self,
        session: Session,
        candidate_filters: list[Any] | None = None,
    ) -> list[ScanCandidateDB]:
        statement = (
            select(ScanCandidateDB)
            .where(ScanCandidateDB.property_id.is_not(None))
            .order_by(ScanCandidateDB.created_at.desc())
        )
        if candidate_filters:
            statement = statement.where(*candidate_filters)
        candidates = session.scalars(statement).all()
        return _dedupe_candidate_rows_per_property(candidates)

    def _latest_packets_per_property(
        self,
        session: Session,
        candidate_filters: list[Any] | None = None,
    ) -> list[SitePacket]:
        candidates = self._latest_candidate_rows_per_property(session, candidate_filters)
        packets = []
        for candidate in candidates:
            property_id = str(candidate.property_id)
            packet = self._packet_from_property(
                session,
                UUID(str(candidate.scan_run_id)),
                property_id,
            )
            if packet is not None:
                packets.append(packet)
        return packets


PostGISScanRunRepository = SQLAlchemyScanRunRepository


def _sqlite_add_column_if_missing(
    connection,
    columns: set[str],
    table: str,
    column: str,
    definition: str,
) -> None:
    if column in columns:
        return
    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    columns.add(column)


def create_postgis_first_repository(
    database_url: str,
    fallback_url: str | None = "sqlite+pysqlite:///outputs/isite2_dev.db",
) -> SQLAlchemyScanRunRepository:
    try:
        repository = SQLAlchemyScanRunRepository.from_url(database_url, storage_mode="postgis")
        with repository.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return repository
    except SQLAlchemyError:
        if fallback_url is None:
            raise
        return SQLAlchemyScanRunRepository.from_url(fallback_url, storage_mode="sqlite")


def _empty_counts() -> dict[str, int]:
    return {
        "scan_runs": 0,
        "properties": 0,
        "scan_candidates": 0,
        "evidence_items": 0,
        "scene_model_results": 0,
        "build_statuses": 0,
        "demand_estimates": 0,
        "inference_records": 0,
        "conclusions": 0,
        "review_queue": 0,
        "source_cache": 0,
    }


def _property_entity(row: PropertyDB) -> PropertyEntity:
    return PropertyEntity(
        property_id=UUID(str(row.id)),
        country=row.country,
        city=row.city,
        property_name=row.canonical_name,
        scene_type=row.scene_type,
        scene_form=row.scene_form,
        latitude=row.latitude,
        longitude=row.longitude,
        geocode_precision=row.geocode_precision,
        map_source=row.map_source,
        map_source_date=row.map_source_date,
        google_maps_link=row.google_maps_link
        or make_google_maps_link(
            row.latitude,
            row.longitude,
            property_name=row.canonical_name,
            city=row.city,
            country=row.country,
        ),
        coordinate_status=row.coordinate_status,
        hero_image=(
            PropertyHeroImage.model_validate(row.hero_image)
            if row.hero_image is not None
            else None
        ),
    )


def _candidate_visibility(row: ScanCandidateDB | None) -> CandidateVisibility:
    if row is None or not row.visibility:
        return CandidateVisibility()
    payload = _json_payload(row.visibility, {})
    return CandidateVisibility.model_validate(payload)


def _hero_image_payload(entity: PropertyEntity) -> dict[str, Any] | None:
    if entity.hero_image is None:
        return None
    return entity.hero_image.model_dump(mode="json")


def _latest_packet_per_property(packets: list[SitePacket]) -> list[SitePacket]:
    latest: dict[UUID, SitePacket] = {}
    for packet in packets:
        latest[packet.entity.property_id] = packet
    return list(latest.values())


def _evidence_db(evidence: EvidenceItem, run_id: UUID) -> EvidenceItemDB:
    return EvidenceItemDB(
        id=str(uuid4()),
        property_id=str(evidence.property_id),
        scan_run_id=str(run_id),
        field_group=evidence.field_group,
        indicator_name=evidence.indicator_name,
        field_value=evidence.field_value,
        unit=evidence.unit,
        evidence_type=evidence.evidence_type.value,
        source_name=evidence.source_name,
        source_tier=evidence.source_tier.value,
        source_url=str(evidence.source_url),
        source_date=evidence.source_date,
        cross_check_status=evidence.cross_check_status.value,
        assumption_note=evidence.assumption_note,
    )


def _evidence_item(row: EvidenceItemDB) -> EvidenceItem:
    return EvidenceItem(
        property_id=UUID(str(row.property_id)),
        field_group=row.field_group,
        field_value=row.field_value,
        indicator_name=row.indicator_name,
        unit=row.unit,
        source_name=row.source_name,
        source_tier=row.source_tier,
        source_url=row.source_url,
        source_date=row.source_date,
        evidence_type=row.evidence_type,
        cross_check_status=row.cross_check_status,
        assumption_note=row.assumption_note,
    )


def _scene_db(packet: SitePacket, run_id: UUID) -> SceneModelResultDB:
    scene = packet.scene
    return SceneModelResultDB(
        id=str(uuid4()),
        property_id=str(packet.entity.property_id),
        scan_run_id=str(run_id),
        area_metric_name=scene.area_metric_name,
        area_metric_value=scene.area_metric_value,
        area_metric_unit=scene.area_metric_unit,
        area_metric_status=scene.area_metric_status,
        primary_value_indicators=scene.primary_value_indicators,
        secondary_value_indicators=scene.secondary_value_indicators,
        proxy_basis=scene.proxy_basis,
        proxy_level=scene.proxy_level.value,
        annual_visits_raw=scene.annual_visits_raw,
        annual_visits_est=scene.annual_visits_est,
        metric_availability_level=scene.metric_availability_level,
        assumption_note=scene.assumption_note,
    )


def _scene_model(row: SceneModelResultDB) -> SceneModelResult:
    return SceneModelResult(
        area_metric_name=row.area_metric_name,
        area_metric_value=row.area_metric_value,
        area_metric_unit=row.area_metric_unit,
        area_metric_status=row.area_metric_status,
        primary_value_indicators=_json_payload(row.primary_value_indicators, []),
        secondary_value_indicators=_json_payload(row.secondary_value_indicators, []),
        proxy_basis=row.proxy_basis,
        proxy_level=row.proxy_level,
        annual_visits_raw=row.annual_visits_raw,
        annual_visits_est=row.annual_visits_est,
        metric_availability_level=row.metric_availability_level,
        assumption_note=row.assumption_note,
    )


def _build_db(packet: SitePacket, run_id: UUID) -> BuildStatusDB:
    build = packet.build_status
    return BuildStatusDB(
        id=str(uuid4()),
        property_id=str(packet.entity.property_id),
        scan_run_id=str(run_id),
        indoor_system_presence=build.indoor_system_presence.value,
        indoor_system_type=build.indoor_system_type.value,
        indoor_rat=build.indoor_rat.value,
        build_evidence_status=build.build_evidence_status.value,
        build_source=build.build_source,
        build_source_date=build.build_source_date,
        operator_name=build.operator_name,
    )


def _build_status(row: BuildStatusDB) -> BuildStatus:
    return BuildStatus(
        indoor_system_presence=row.indoor_system_presence,
        indoor_system_type=row.indoor_system_type,
        indoor_rat=row.indoor_rat,
        build_evidence_status=row.build_evidence_status,
        build_source=row.build_source,
        build_source_date=row.build_source_date,
        operator_name=row.operator_name,
    )


def _demand_db(packet: SitePacket, run_id: UUID) -> DemandEstimateDB:
    demand = packet.demand or DemandEstimate()
    return DemandEstimateDB(
        id=str(uuid4()),
        property_id=str(packet.entity.property_id),
        scan_run_id=str(run_id),
        daily_visits=demand.daily_visits,
        attach_rate=demand.attach_rate,
        indoor_capture=demand.indoor_capture,
        busy_hour_factor=demand.busy_hour_factor,
        gb_per_user_busy_hour=demand.gb_per_user_busy_hour,
        busy_hour_users=demand.busy_hour_users,
        busy_hour_traffic_gb=demand.busy_hour_traffic_gb,
        busy_hour_bandwidth_mbps=demand.busy_hour_bandwidth_mbps,
        cannot_calculate_reason=demand.cannot_calculate_reason,
    )


def _demand_estimate(row: DemandEstimateDB) -> DemandEstimate:
    return DemandEstimate(
        daily_visits=row.daily_visits,
        attach_rate=row.attach_rate,
        indoor_capture=row.indoor_capture,
        busy_hour_factor=row.busy_hour_factor,
        gb_per_user_busy_hour=row.gb_per_user_busy_hour,
        busy_hour_users=row.busy_hour_users,
        busy_hour_traffic_gb=row.busy_hour_traffic_gb,
        busy_hour_bandwidth_mbps=row.busy_hour_bandwidth_mbps,
        cannot_calculate_reason=row.cannot_calculate_reason,
    )


def _inference_db(
    packet: SitePacket,
    inference: InferenceRecord,
    run_id: UUID,
) -> InferenceRecordDB:
    return InferenceRecordDB(
        id=str(uuid4()),
        property_id=str(packet.entity.property_id),
        scan_run_id=str(run_id),
        inferred_field=inference.inferred_field,
        inferred_value=inference.inferred_value,
        inference_basis=inference.inference_basis,
        inference_chain=inference.inference_chain,
        inference_confidence=inference.inference_confidence,
    )


def _inference_record(row: InferenceRecordDB) -> InferenceRecord:
    return InferenceRecord(
        inferred_field=row.inferred_field,
        inferred_value=row.inferred_value,
        inference_basis=row.inference_basis,
        inference_chain=row.inference_chain,
        inference_confidence=row.inference_confidence,
    )


def _conclusion_db(packet: SitePacket, run_id: UUID) -> ConclusionDB:
    conclusion = packet.conclusion
    return ConclusionDB(
        id=str(uuid4()),
        property_id=str(packet.entity.property_id),
        scan_run_id=str(run_id),
        evidence_status=conclusion.evidence_status.value,
        value_class=conclusion.value_class.value,
        action_class=conclusion.action_class.value,
        recommended_solution=conclusion.recommended_solution.value,
        reason_to_recommend=conclusion.reason_to_recommend,
        risk_review_reason=conclusion.risk_review_reason,
        next_action=conclusion.next_action,
    )


def _conclusion(row: ConclusionDB) -> Conclusion:
    return Conclusion(
        evidence_status=row.evidence_status,
        value_class=row.value_class,
        action_class=row.action_class,
        recommended_solution=row.recommended_solution,
        reason_to_recommend=row.reason_to_recommend,
        risk_review_reason=row.risk_review_reason,
        next_action=row.next_action,
    )


def _review_db(packet: SitePacket, review: ReviewItem, run_id: UUID) -> ReviewQueueDB:
    return ReviewQueueDB(
        id=str(uuid4()),
        property_id=str(packet.entity.property_id),
        scan_run_id=str(run_id),
        reason=review.reason,
        next_action=review.next_action,
        status=review.status,
        review_type=review.review_type,
        severity=review.severity,
        gate_name=review.gate_name,
        field_path=review.field_path,
        blocking_surfaces=list(review.blocking_surfaces),
        source_url=review.source_url,
        suggested_query=review.suggested_query,
    )


def _review_item(row: ReviewQueueDB) -> ReviewItem:
    blocking_surfaces = _json_payload(row.blocking_surfaces, [])
    return ReviewItem(
        reason=row.reason,
        next_action=row.next_action,
        status=row.status,
        review_type=row.review_type,
        severity=row.severity,
        gate_name=row.gate_name,
        field_path=row.field_path,
        blocking_surfaces=list(blocking_surfaces),
        source_url=row.source_url,
        suggested_query=row.suggested_query,
    )


def _json_payload(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _counts_for_run(session: Session, run_id: UUID) -> dict[str, int]:
    property_ids = {
        row.property_id
        for row in session.scalars(
            select(ScanCandidateDB).where(ScanCandidateDB.scan_run_id == str(run_id))
        ).all()
        if row.property_id
    }
    counts = _empty_counts()
    counts["scan_runs"] = 1 if session.get(ScanRunDB, str(run_id)) is not None else 0
    counts["properties"] = len(property_ids)
    count_tables = [
        ("scan_candidates", ScanCandidateDB),
        ("evidence_items", EvidenceItemDB),
        ("scene_model_results", SceneModelResultDB),
        ("build_statuses", BuildStatusDB),
        ("demand_estimates", DemandEstimateDB),
        ("inference_records", InferenceRecordDB),
        ("conclusions", ConclusionDB),
        ("review_queue", ReviewQueueDB),
    ]
    for key, table in count_tables:
        counts[key] = len(
            session.scalars(select(table).where(table.scan_run_id == str(run_id))).all()
        )
    counts["source_cache"] = len(_source_cache_urls(session))
    return counts


def _source_cache_urls(session: Session) -> set[str]:
    return set(session.scalars(select(SourceCacheDB.source_url)).all())


def _candidate_row_prefilters(filters: dict[str, Any]) -> list[Any]:
    column_by_key = {
        "country": ScanCandidateDB.country,
        "city": ScanCandidateDB.city,
        "scene_type": ScanCandidateDB.scene_type,
    }
    return [
        column == str(value)
        for key, column in column_by_key.items()
        if (value := filters.get(key)) is not None and value != ""
    ]


def _dedupe_candidate_rows_per_property(candidates: list[ScanCandidateDB]) -> list[ScanCandidateDB]:
    latest = []
    seen: set[str] = set()
    for candidate in candidates:
        property_id = str(candidate.property_id)
        if property_id in seen:
            continue
        latest.append(candidate)
        seen.add(property_id)
    return latest


def _property_run_key(row) -> tuple[str, str]:
    return (str(row.property_id), str(row.scan_run_id))


def _latest_rows_by_property_run(rows: list[Any]) -> dict[tuple[str, str], Any]:
    latest: dict[tuple[str, str], Any] = {}
    for row in rows:
        latest.setdefault(_property_run_key(row), row)
    return latest


def _candidate_summary_matches(
    candidate: ScanCandidateDB,
    property_row: PropertyDB,
    conclusion_row: ConclusionDB,
    scene_row: SceneModelResultDB,
    build_row: BuildStatusDB,
    visibility: CandidateVisibility,
    review_count: int,
    filters: dict[str, Any],
) -> bool:
    checks = {
        "country": property_row.country,
        "city": property_row.city,
        "scene_type": property_row.scene_type,
        "evidence_status": conclusion_row.evidence_status,
        "value_class": conclusion_row.value_class,
        "action_class": conclusion_row.action_class,
        "recommended_solution": conclusion_row.recommended_solution,
        "indoor_system_presence": build_row.indoor_system_presence,
        "indoor_rat": build_row.indoor_rat,
        "proxy_level": scene_row.proxy_level,
        "has_review_issue": review_count > 0,
        "candidate_quality_status": candidate.candidate_quality_status,
        "main_table_ready": visibility.main_table_ready,
        "map_ready": visibility.map_ready,
        "export_ready": visibility.export_ready,
    }
    return all(
        str(checks[key]) == str(value)
        for key, value in filters.items()
        if key in checks and value is not None and value != ""
    )


def _packet_matches(packet: SitePacket, filters: dict[str, Any]) -> bool:
    checks = {
        "country": packet.entity.country,
        "city": packet.entity.city,
        "scene_type": packet.entity.scene_type,
        "evidence_status": packet.conclusion.evidence_status,
        "value_class": packet.conclusion.value_class,
        "action_class": packet.conclusion.action_class,
        "recommended_solution": packet.conclusion.recommended_solution,
        "indoor_system_presence": packet.build_status.indoor_system_presence,
        "indoor_rat": packet.build_status.indoor_rat,
        "proxy_level": packet.scene.proxy_level,
        "has_review_issue": bool(packet.review_queue),
        "candidate_quality_status": packet.candidate_quality_status,
        "main_table_ready": packet.visibility.main_table_ready,
        "map_ready": packet.visibility.map_ready,
        "export_ready": packet.visibility.export_ready,
    }
    return all(
        str(checks[key]) == str(value)
        for key, value in filters.items()
        if key in checks and value is not None and value != ""
    )


def _source_cache_page_text(evidence: EvidenceItem) -> str:
    return (
        f"{evidence.source_name} | {evidence.field_group} | "
        f"{evidence.field_value} | registry/cache seed"
    )


def _source_tier_value(evidence: EvidenceItem) -> str:
    return evidence.source_tier.value


def _evidence_source_url(evidence: EvidenceItem) -> str:
    return str(evidence.source_url)


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _source_cache_row(evidence: EvidenceItem, text: str) -> SourceCacheDB:
    return SourceCacheDB(
        source_url=_evidence_source_url(evidence),
        source_name=evidence.source_name,
        source_tier=_source_tier_value(evidence),
        source_date=evidence.source_date,
        fetched_at=_utc_now(),
        content_hash=_source_hash(text),
        content_text=text,
        robots_allowed=True,
    )


def _merge_source_cache(session: Session, evidence: EvidenceItem) -> None:
    text = _source_cache_page_text(evidence)
    row = _source_cache_row(evidence, text)
    session.merge(row)


SQLAlchemyScanRunRepository._upsert_source_cache = staticmethod(_merge_source_cache)
