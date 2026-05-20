from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class ScanRunDB(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="created", nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), default="0.1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    candidates: Mapped[list[ScanCandidateDB]] = relationship(back_populates="scan_run")


class PropertyDB(Base):
    __tablename__ = "properties"
    __table_args__ = (
        Index(
            "ux_properties_country_scene_identity",
            "country",
            "scene_type",
            "property_identity_key",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_identity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    scene_form: Mapped[str] = mapped_column(String(32), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    geocode_precision: Mapped[str] = mapped_column(Text, nullable=False)
    map_source: Mapped[str | None] = mapped_column(Text)
    map_source_date: Mapped[str | None] = mapped_column(Text)
    google_maps_link: Mapped[str | None] = mapped_column(Text)
    coordinate_status: Mapped[str] = mapped_column(String(32), default="Verified", nullable=False)
    hero_image: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PropertyAliasDB(Base):
    __tablename__ = "property_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScanCandidateDB(Base):
    __tablename__ = "scan_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), nullable=False)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"))
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str | None] = mapped_column(String(128))
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    discovery_source: Mapped[str | None] = mapped_column(Text)
    discovery_rank: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="discovered", nullable=False)
    candidate_quality_status: Mapped[str] = mapped_column(
        String(32),
        default="ready",
        nullable=False,
    )
    visibility: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quality_issues: Mapped[list[str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    scan_run: Mapped[ScanRunDB] = relationship(back_populates="candidates")


class EvidenceItemDB(Base):
    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    field_group: Mapped[str] = mapped_column(Text, nullable=False)
    indicator_name: Mapped[str | None] = mapped_column(Text)
    field_value: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_date: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cross_check_status: Mapped[str] = mapped_column(String(64), default="Not Checked")
    assumption_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SceneModelResultDB(Base):
    __tablename__ = "scene_model_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    area_metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    area_metric_value: Mapped[float | None] = mapped_column(Float)
    area_metric_unit: Mapped[str | None] = mapped_column(Text)
    area_metric_status: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_value_indicators: Mapped[list[str]] = mapped_column(JSON, default=list)
    secondary_value_indicators: Mapped[list[str]] = mapped_column(JSON, default=list)
    proxy_basis: Mapped[str] = mapped_column(Text, nullable=False)
    proxy_level: Mapped[str] = mapped_column(String(64), nullable=False)
    annual_visits_raw: Mapped[float | None] = mapped_column(Float)
    annual_visits_est: Mapped[float | None] = mapped_column(Float)
    metric_availability_level: Mapped[str | None] = mapped_column(Text)
    assumption_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BuildStatusDB(Base):
    __tablename__ = "build_statuses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    indoor_system_presence: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_system_type: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_rat: Mapped[str] = mapped_column(String(32), nullable=False)
    build_evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    build_source: Mapped[str | None] = mapped_column(Text)
    build_source_date: Mapped[str | None] = mapped_column(Text)
    operator_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DemandEstimateDB(Base):
    __tablename__ = "demand_estimates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    daily_visits: Mapped[float | None] = mapped_column(Float)
    attach_rate: Mapped[float | None] = mapped_column(Float)
    indoor_capture: Mapped[float | None] = mapped_column(Float)
    busy_hour_factor: Mapped[float | None] = mapped_column(Float)
    gb_per_user_busy_hour: Mapped[float | None] = mapped_column(Float)
    busy_hour_users: Mapped[float | None] = mapped_column(Float)
    busy_hour_traffic_gb: Mapped[float | None] = mapped_column(Float)
    busy_hour_bandwidth_mbps: Mapped[float | None] = mapped_column(Float)
    cannot_calculate_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InferenceRecordDB(Base):
    __tablename__ = "inference_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    inferred_field: Mapped[str] = mapped_column(Text, nullable=False)
    inferred_value: Mapped[str] = mapped_column(Text, nullable=False)
    inference_basis: Mapped[str] = mapped_column(Text, nullable=False)
    inference_chain: Mapped[str] = mapped_column(Text, nullable=False)
    inference_confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConclusionDB(Base):
    __tablename__ = "conclusions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    value_class: Mapped[str] = mapped_column(String(64), nullable=False)
    action_class: Mapped[str] = mapped_column(String(64), nullable=False)
    recommended_solution: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_to_recommend: Mapped[str] = mapped_column(Text, nullable=False)
    risk_review_reason: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewQueueDB(Base):
    __tablename__ = "review_queue"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"))
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    review_type: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    severity: Mapped[str] = mapped_column(String(32), default="medium", nullable=False)
    gate_name: Mapped[str | None] = mapped_column(String(128))
    field_path: Mapped[str | None] = mapped_column(Text)
    blocking_surfaces: Mapped[list[str] | None] = mapped_column(JSON)
    source_url: Mapped[str | None] = mapped_column(Text)
    suggested_query: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutputArtifactDB(Base):
    __tablename__ = "output_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    filter_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceCacheDB(Base):
    __tablename__ = "source_cache"

    source_url: Mapped[str] = mapped_column(Text, primary_key=True)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    source_date: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    robots_allowed: Mapped[bool] = mapped_column(default=True, nullable=False)


class RawEvidenceItemDB(Base):
    __tablename__ = "raw_evidence_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str | None] = mapped_column(String(128))
    property_name: Mapped[str | None] = mapped_column(Text)
    scene_type: Mapped[str | None] = mapped_column(String(128))
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    source_date: Mapped[str | None] = mapped_column(Text)
    field_group: Mapped[str | None] = mapped_column(Text)
    indicator_name: Mapped[str | None] = mapped_column(Text)
    field_value: Mapped[str | None] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String(32), default="Direct", nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    geocode_precision: Mapped[str | None] = mapped_column(Text)
    map_source: Mapped[str | None] = mapped_column(Text)
    map_source_date: Mapped[str | None] = mapped_column(Text)
    annual_visits: Mapped[float | None] = mapped_column(Float)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False)
    curation_needed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    curation_run_id: Mapped[str | None] = mapped_column(String(64))
    curated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CandidateDraftDB(Base):
    __tablename__ = "candidate_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    curation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    country: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(128))
    property_name: Mapped[str | None] = mapped_column(Text)
    scene_type: Mapped[str | None] = mapped_column(String(128))
    source_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveryProgressDB(Base):
    __tablename__ = "discovery_progress"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    no_new_cycles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_new_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_new_last_cycle: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draft_review_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveryTaskDB(Base):
    __tablename__ = "discovery_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    query_template: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_run_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    failure_class: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveryRunDB(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    worker_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    searched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    known_property_skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    known_url_skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    possible_duplicate_review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_opportunity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    existing_property_evidence_update_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    firecrawl_requests_saved_estimate: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    countries: Mapped[list[str]] = mapped_column(JSON, default=list)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)


class HumanFeedbackDB(Base):
    __tablename__ = "human_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"))
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuleVersionDB(Base):
    __tablename__ = "rule_versions"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InferenceCorrectionDB(Base):
    __tablename__ = "inference_corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    inferred_field: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FreshnessTaskDB(Base):
    __tablename__ = "freshness_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceReliabilityDB(Base):
    __tablename__ = "source_reliability"

    source_name: Mapped[str] = mapped_column(Text, primary_key=True)
    source_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    successful_extractions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_extractions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)


class RagDocumentDB(Base):
    __tablename__ = "rag_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(Text)
    source_tier: Mapped[str | None] = mapped_column(String(32))
    source_date: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_evidence_id: Mapped[str | None] = mapped_column(String(36))
    property_id: Mapped[str | None] = mapped_column(String(36))
    scan_run_id: Mapped[str | None] = mapped_column(String(36))
    country: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(128))
    scene_type: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    chunks: Mapped[list[RagChunkDB]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class RagChunkDB(Base):
    __tablename__ = "rag_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("rag_documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(Text)
    source_tier: Mapped[str | None] = mapped_column(String(32))
    source_date: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_evidence_id: Mapped[str | None] = mapped_column(String(36))
    property_id: Mapped[str | None] = mapped_column(String(36))
    scan_run_id: Mapped[str | None] = mapped_column(String(36))
    country: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(128))
    scene_type: Mapped[str | None] = mapped_column(String(128))
    field_group: Mapped[str | None] = mapped_column(Text)
    indicator_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    document: Mapped[RagDocumentDB] = relationship(back_populates="chunks")
