from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    city_id: Mapped[str | None] = mapped_column(String(128))
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


class CityCanonicalUnitDB(Base):
    __tablename__ = "city_canonical_units"
    __table_args__ = (
        Index("idx_city_canonical_units_country_name", "country", "canonical_name"),
    )

    city_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    official_name: Mapped[str | None] = mapped_column(String(128))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    grouping_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_area_1: Mapped[str | None] = mapped_column(String(128))
    admin_area_2: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_authority: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_date: Mapped[str | None] = mapped_column(String(32))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    mapping_version: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CityLocalityMappingDB(Base):
    __tablename__ = "city_locality_mappings"
    __table_args__ = (
        UniqueConstraint(
            "country",
            "locality_normalized",
            name="ux_city_locality_mapping_country_locality",
        ),
        Index("idx_city_locality_mapping_city", "city_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    locality_name: Mapped[str] = mapped_column(String(128), nullable=False)
    locality_normalized: Mapped[str] = mapped_column(String(128), nullable=False)
    city_id: Mapped[str] = mapped_column(
        ForeignKey("city_canonical_units.city_id"),
        nullable=False,
    )
    mapping_method: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_area_1: Mapped[str | None] = mapped_column(String(128))
    admin_area_2: Mapped[str | None] = mapped_column(String(128))
    source_authority: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_date: Mapped[str | None] = mapped_column(String(32))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    mapping_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PropertyCityAssignmentDB(Base):
    __tablename__ = "property_city_assignments"
    __table_args__ = (
        Index("idx_property_city_assignments_city", "country", "city_id"),
        Index("idx_property_city_assignments_status", "mapping_status"),
    )

    property_id: Mapped[str] = mapped_column(
        ForeignKey("properties.id"),
        primary_key=True,
    )
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city_id: Mapped[str | None] = mapped_column(ForeignKey("city_canonical_units.city_id"))
    canonical_city: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    source_city: Mapped[str] = mapped_column(String(128), nullable=False)
    locality: Mapped[str | None] = mapped_column(String(128))
    admin_area_1: Mapped[str | None] = mapped_column(String(128))
    admin_area_2: Mapped[str | None] = mapped_column(String(128))
    mapping_status: Mapped[str] = mapped_column(String(32), nullable=False)
    mapping_method: Mapped[str] = mapped_column(String(64), nullable=False)
    grouping_basis: Mapped[str | None] = mapped_column(String(64))
    source_authority: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_date: Mapped[str | None] = mapped_column(String(32))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    mapping_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PropertyAliasDB(Base):
    __tablename__ = "property_aliases"
    __table_args__ = (Index("idx_property_aliases_property", "property_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScanCandidateDB(Base):
    __tablename__ = "scan_candidates"
    __table_args__ = (
        Index(
            "idx_scan_candidates_scan_run_property_created",
            "scan_run_id",
            "property_id",
            "created_at",
        ),
        Index(
            "idx_scan_candidates_country_scene_status",
            "country",
            "scene_type",
            "candidate_quality_status",
        ),
    )

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
    __table_args__ = (
        Index("idx_evidence_property_run", "property_id", "scan_run_id"),
    )

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
    __table_args__ = (
        Index("idx_scene_property_run", "property_id", "scan_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    area_metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    area_metric_value: Mapped[float | None] = mapped_column(Float)
    area_metric_unit: Mapped[str | None] = mapped_column(Text)
    area_metric_status: Mapped[str] = mapped_column(Text, nullable=False)
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
    __table_args__ = (
        Index("idx_build_property_run", "property_id", "scan_run_id"),
    )

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
    __table_args__ = (
        Index("idx_demand_property_run", "property_id", "scan_run_id"),
    )

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


class TrafficEstimateV2DB(Base):
    __tablename__ = "traffic_estimates_v2"
    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "scan_run_id",
            "model_version",
            "input_hash",
            name="ux_traffic_v2_property_run_model_input",
        ),
        Index("idx_traffic_v2_property_run", "property_id", "scan_run_id"),
        Index("idx_traffic_v2_model_method", "model_version", "estimate_method"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    scan_run_id: Mapped[str | None] = mapped_column(ForeignKey("scan_runs.id"))
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    estimate_method: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_metric_key: Mapped[str | None] = mapped_column(String(128))
    selected_metric_value: Mapped[float | None] = mapped_column(Float)
    selected_metric_unit: Mapped[str | None] = mapped_column(String(64))
    selected_evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    annual_visits_p10: Mapped[float | None] = mapped_column(Float)
    annual_visits_p50: Mapped[float | None] = mapped_column(Float)
    annual_visits_p90: Mapped[float | None] = mapped_column(Float)
    typical_day_visits_p10: Mapped[float | None] = mapped_column(Float)
    typical_day_visits_p50: Mapped[float | None] = mapped_column(Float)
    typical_day_visits_p90: Mapped[float | None] = mapped_column(Float)
    peak_day_visits_p10: Mapped[float | None] = mapped_column(Float)
    peak_day_visits_p50: Mapped[float | None] = mapped_column(Float)
    peak_day_visits_p90: Mapped[float | None] = mapped_column(Float)
    busy_hour_users_p10: Mapped[float | None] = mapped_column(Float)
    busy_hour_users_p50: Mapped[float | None] = mapped_column(Float)
    busy_hour_users_p90: Mapped[float | None] = mapped_column(Float)
    busy_hour_traffic_gb_p10: Mapped[float | None] = mapped_column(Float)
    busy_hour_traffic_gb_p50: Mapped[float | None] = mapped_column(Float)
    busy_hour_traffic_gb_p90: Mapped[float | None] = mapped_column(Float)
    busy_hour_bandwidth_mbps_p10: Mapped[float | None] = mapped_column(Float)
    busy_hour_bandwidth_mbps_p50: Mapped[float | None] = mapped_column(Float)
    busy_hour_bandwidth_mbps_p90: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    activation_status: Mapped[str] = mapped_column(
        String(64),
        default="not_evaluated",
        nullable=False,
    )
    activation_reason: Mapped[str | None] = mapped_column(Text)
    v1_annual_visits_est: Mapped[float | None] = mapped_column(Float)
    v1_annual_visits_raw: Mapped[float | None] = mapped_column(Float)
    v1_demand_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parameter_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    qa_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    cannot_calculate_reason: Mapped[str | None] = mapped_column(Text)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ComplaintObservationDB(Base):
    __tablename__ = "complaint_observations"
    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "source_url",
            "content_hash",
            name="ux_complaint_property_source_content",
        ),
        Index("idx_complaint_property_observed", "property_id", "observed_at"),
        Index("idx_complaint_country_category", "country", "category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sanitized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    severity_weight: Mapped[float] = mapped_column(Float, nullable=False)
    match_status: Mapped[str] = mapped_column(String(32), nullable=False)
    classification_method: Mapped[str] = mapped_column(String(32), nullable=False)
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    source_manifest_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PropertyComplaintRollupDB(Base):
    __tablename__ = "property_complaint_rollups"
    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "period_days",
            "input_hash",
            name="ux_complaint_rollup_property_period_input",
        ),
        Index("idx_complaint_rollup_property", "property_id"),
        Index("idx_complaint_rollup_pressure", "pressure_level", "confidence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_complaint_count: Mapped[int] = mapped_column(Integer, nullable=False)
    weighted_complaint_count: Mapped[float] = mapped_column(Float, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    category_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    pressure_level: Mapped[str] = mapped_column(String(32), nullable=False)
    pressure_percentile: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    latest_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NetworkPerformanceObservationDB(Base):
    __tablename__ = "network_performance_observations"
    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "service_type",
            "period",
            name="ux_network_perf_property_type_period",
        ),
        Index(
            "idx_network_perf_property_type_period",
            "property_id",
            "service_type",
            "period",
        ),
        Index("idx_network_perf_country_type_period", "country", "service_type", "period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    quadkey: Mapped[str] = mapped_column(String(32), nullable=False)
    tile_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    tile_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    avg_download_mbps: Mapped[float] = mapped_column(Float, nullable=False)
    avg_upload_mbps: Mapped[float] = mapped_column(Float, nullable=False)
    avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    avg_loaded_latency_down_ms: Mapped[float | None] = mapped_column(Float)
    avg_loaded_latency_up_ms: Mapped[float | None] = mapped_column(Float)
    tests: Mapped[int] = mapped_column(Integer, nullable=False)
    devices: Mapped[int] = mapped_column(Integer, nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_checksum: Mapped[str | None] = mapped_column(String(128))
    source_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    license: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NetworkPerformanceTileDB(Base):
    __tablename__ = "network_performance_tiles"
    __table_args__ = (
        UniqueConstraint(
            "country",
            "service_type",
            "period",
            "quadkey",
            name="ux_network_perf_tile_country_type_period_quadkey",
        ),
        Index(
            "idx_network_perf_tile_country_type_period",
            "country",
            "service_type",
            "period",
        ),
        Index(
            "idx_network_perf_tile_country_type_class",
            "country",
            "service_type",
            "performance_class",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    quadkey: Mapped[str] = mapped_column(String(32), nullable=False)
    tile_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    tile_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    avg_download_mbps: Mapped[float] = mapped_column(Float, nullable=False)
    avg_upload_mbps: Mapped[float] = mapped_column(Float, nullable=False)
    avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    avg_loaded_latency_down_ms: Mapped[float | None] = mapped_column(Float)
    avg_loaded_latency_up_ms: Mapped[float | None] = mapped_column(Float)
    tests: Mapped[int] = mapped_column(Integer, nullable=False)
    devices: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    performance_class: Mapped[str] = mapped_column(String(32), nullable=False)
    download_percentile: Mapped[float | None] = mapped_column(Float)
    loaded_latency_percentile: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_checksum: Mapped[str | None] = mapped_column(String(128))
    source_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    license: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PropertyNetworkPerformanceRollupDB(Base):
    __tablename__ = "property_network_performance_rollups"
    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "service_type",
            "period",
            name="ux_network_rollup_property_type_period",
        ),
        Index("idx_network_rollup_property", "property_id"),
        Index(
            "idx_network_rollup_country_type_class",
            "country",
            "service_type",
            "performance_class",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("network_performance_observations.id"),
        nullable=False,
    )
    performance_class: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    download_percentile: Mapped[float | None] = mapped_column(Float)
    loaded_latency_percentile: Mapped[float | None] = mapped_column(Float)
    trend: Mapped[str | None] = mapped_column(String(32))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InferenceRecordDB(Base):
    __tablename__ = "inference_records"
    __table_args__ = (
        Index("idx_inference_property_run", "property_id", "scan_run_id"),
    )

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
    __table_args__ = (
        Index("idx_conclusions_property_run", "property_id", "scan_run_id"),
    )

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
    __table_args__ = (
        Index("idx_review_property_run", "property_id", "scan_run_id"),
        Index("idx_review_status_property_run", "status", "property_id", "scan_run_id"),
    )

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


class LocalizedTextCacheDB(Base):
    __tablename__ = "localized_text_cache"
    __table_args__ = (
        Index(
            "ux_localized_text_cache_key",
            "source_text_hash",
            "text_kind",
            "source_locale",
            "target_locale",
            "schema_version",
            unique=True,
        ),
        Index(
            "idx_localized_text_cache_lookup",
            "target_locale",
            "text_kind",
            "source_text_hash",
            "schema_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_locale: Mapped[str] = mapped_column(String(16), nullable=False)
    target_locale: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider_name: Mapped[str | None] = mapped_column(Text)
    provider_model: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PublicApiPropertyIndexDB(Base):
    __tablename__ = "public_api_property_index"
    __table_args__ = (
        Index("idx_public_api_property_index_country_scene", "country", "scene_type"),
        Index("idx_public_api_property_index_city_scene", "country", "city", "scene_type"),
        Index(
            "idx_public_api_property_index_status",
            "candidate_quality_status",
            "main_table_ready",
            "map_ready",
        ),
        Index("idx_public_api_property_index_filters", "evidence_status", "value_class"),
        Index("idx_public_api_property_index_search", "search_text_normalized"),
    )

    property_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    property_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    search_text_normalized: Mapped[str] = mapped_column(Text, default="", nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    city_id: Mapped[str | None] = mapped_column(String(128))
    city_assignment: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    longitude: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    google_maps_link: Mapped[str | None] = mapped_column(Text)
    geocode_precision: Mapped[str | None] = mapped_column(Text)
    map_source: Mapped[str | None] = mapped_column(Text)
    coordinate_status: Mapped[str | None] = mapped_column(String(64))
    evidence_status: Mapped[str] = mapped_column(String(64), nullable=False)
    value_class: Mapped[str] = mapped_column(String(64), nullable=False)
    action_class: Mapped[str] = mapped_column(String(64), nullable=False)
    recommended_solution: Mapped[str] = mapped_column(String(64), nullable=False)
    annual_visits_est: Mapped[float | None] = mapped_column(Float)
    annual_visits_p10: Mapped[float | None] = mapped_column(Float)
    annual_visits_p50: Mapped[float | None] = mapped_column(Float)
    annual_visits_p90: Mapped[float | None] = mapped_column(Float)
    traffic_model_version: Mapped[str | None] = mapped_column(String(64))
    proxy_level: Mapped[str] = mapped_column(String(64), nullable=False)
    busy_hour_traffic_gb: Mapped[float | None] = mapped_column(Float)
    complaint_pressure: Mapped[str | None] = mapped_column(String(32))
    network_validation_priority: Mapped[str | None] = mapped_column(String(32))
    network_data_freshness: Mapped[str | None] = mapped_column(String(32))
    feature_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    indoor_system_presence: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_rat: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    main_table_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    map_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    export_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    map_coordinate_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_review_issue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    main_metric_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class PublicApiPropertyPacketDB(Base):
    __tablename__ = "public_api_property_packets"
    __table_args__ = (
        Index("idx_public_api_property_packets_property", "property_id"),
        Index("idx_public_api_property_packets_locale_property", "locale", "property_id"),
        Index(
            "idx_public_api_property_packets_locale_country_scene",
            "locale",
            "country",
            "scene_type",
        ),
        Index(
            "idx_public_api_property_packets_locale_city_scene",
            "locale",
            "country",
            "city",
            "scene_type",
        ),
        Index(
            "idx_public_api_property_packets_locale_status",
            "locale",
            "candidate_quality_status",
            "main_table_ready",
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    property_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scan_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    city_id: Mapped[str | None] = mapped_column(String(128))
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(64), nullable=False)
    value_class: Mapped[str] = mapped_column(String(64), nullable=False)
    action_class: Mapped[str] = mapped_column(String(64), nullable=False)
    recommended_solution: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_system_presence: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_rat: Mapped[str] = mapped_column(String(64), nullable=False)
    proxy_level: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    main_table_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    map_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    export_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_review_issue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    packet_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class PublicApiMapFeatureDB(Base):
    __tablename__ = "public_api_map_features"
    __table_args__ = (
        Index("idx_public_api_map_features_property", "property_id"),
        Index(
            "idx_public_api_map_features_locale_country_scene",
            "locale",
            "country",
            "scene_type",
        ),
        Index(
            "idx_public_api_map_features_locale_status",
            "locale",
            "candidate_quality_status",
            "map_ready",
            "map_coordinate_ready",
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    property_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scan_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    city_id: Mapped[str | None] = mapped_column(String(128))
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(64), nullable=False)
    value_class: Mapped[str] = mapped_column(String(64), nullable=False)
    action_class: Mapped[str] = mapped_column(String(64), nullable=False)
    recommended_solution: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_system_presence: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_rat: Mapped[str] = mapped_column(String(64), nullable=False)
    proxy_level: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    map_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    map_coordinate_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_review_issue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class PublicApiReviewQueueRowDB(Base):
    __tablename__ = "public_api_review_queue_rows"
    __table_args__ = (
        Index("idx_public_api_review_rows_property", "property_id"),
        Index("idx_public_api_review_rows_locale_status", "locale", "status"),
        Index(
            "idx_public_api_review_rows_locale_country_scene",
            "locale",
            "country",
            "scene_type",
        ),
        Index(
            "idx_public_api_review_rows_locale_filters",
            "locale",
            "status",
            "country",
            "scene_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(36), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    property_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scan_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    city_id: Mapped[str | None] = mapped_column(String(128))
    scene_type: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(64), nullable=False)
    value_class: Mapped[str] = mapped_column(String(64), nullable=False)
    action_class: Mapped[str] = mapped_column(String(64), nullable=False)
    indoor_system_presence: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    row_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


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
