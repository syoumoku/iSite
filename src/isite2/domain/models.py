from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator

from isite2.domain.enums import (
    ActionClass,
    BuildEvidenceStatus,
    CrossCheckStatus,
    EvidenceStatus,
    EvidenceType,
    IndoorRAT,
    IndoorSystemPresence,
    IndoorSystemType,
    ProxyLevel,
    RecommendedSolution,
    SceneForm,
    SourceTier,
    ValueClass,
)


class ScanScope(BaseModel):
    level: str = Field(pattern="^(global|region|country|city)$")
    regions: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    full_scan: bool = True
    scene_types: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=lambda: ["excel", "geojson"])
    custom_filters: dict[str, Any] = Field(default_factory=dict)


class ScanRun(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    scope: ScanScope
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    candidate_count: int = 0
    review_count: int = 0


class PropertyHeroImage(BaseModel):
    url: HttpUrl | str
    alt_text: str
    source_name: str
    source_url: HttpUrl | str
    source_date: str | None = None
    license: str | None = None


class PropertyEntity(BaseModel):
    property_id: UUID = Field(default_factory=uuid4)
    country: str
    city: str
    property_name: str
    scene_type: str
    scene_form: SceneForm
    latitude: float
    longitude: float
    geocode_precision: str
    map_source: str | None = None
    map_source_date: str | None = None
    google_maps_link: str | None = None
    coordinate_status: str = "Verified"
    hero_image: PropertyHeroImage | None = None

    @field_validator("latitude")
    @classmethod
    def valid_latitude(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return value

    @field_validator("longitude")
    @classmethod
    def valid_longitude(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return value


class EvidenceItem(BaseModel):
    property_id: UUID
    field_group: str
    field_value: str
    indicator_name: str | None = None
    unit: str | None = None
    source_name: str
    source_tier: SourceTier
    source_url: HttpUrl | str
    source_date: str | None = None
    evidence_type: EvidenceType
    cross_check_status: CrossCheckStatus = CrossCheckStatus.NOT_CHECKED
    assumption_note: str | None = None


class SceneModelResult(BaseModel):
    area_metric_name: str
    area_metric_value: float | None = None
    area_metric_unit: str | None = None
    area_metric_status: str = "Unknown"
    primary_value_indicators: list[str] = Field(default_factory=list)
    secondary_value_indicators: list[str] = Field(default_factory=list)
    proxy_basis: str
    proxy_level: ProxyLevel
    annual_visits_raw: float | None = None
    annual_visits_est: float | None = None
    metric_availability_level: str | None = None
    assumption_note: str | None = None


class BuildStatus(BaseModel):
    indoor_system_presence: IndoorSystemPresence
    indoor_system_type: IndoorSystemType
    indoor_rat: IndoorRAT
    build_evidence_status: BuildEvidenceStatus
    build_source: str | None = None
    build_source_date: str | None = None
    operator_name: str | None = None


class DemandEstimate(BaseModel):
    daily_visits: float | None = None
    attach_rate: float | None = None
    indoor_capture: float | None = None
    busy_hour_factor: float | None = None
    gb_per_user_busy_hour: float | None = None
    busy_hour_users: float | None = None
    busy_hour_traffic_gb: float | None = None
    busy_hour_bandwidth_mbps: float | None = None
    cannot_calculate_reason: str | None = None


class InferenceRecord(BaseModel):
    inferred_field: str
    inferred_value: str
    inference_basis: str
    inference_chain: str
    inference_confidence: str = Field(pattern="^(Conservative|Moderate)$")


class Conclusion(BaseModel):
    evidence_status: EvidenceStatus
    value_class: ValueClass
    action_class: ActionClass
    recommended_solution: RecommendedSolution
    reason_to_recommend: str
    risk_review_reason: str | None = None
    next_action: str


class ReviewItem(BaseModel):
    reason: str
    next_action: str
    status: str = "open"
    review_type: str = "general"
    severity: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    gate_name: str | None = None
    field_path: str | None = None
    blocking_surfaces: list[str] = Field(default_factory=list)
    source_url: str | None = None
    suggested_query: str | None = None


class CandidateVisibility(BaseModel):
    raw_pool: bool = True
    review_required: bool = False
    main_table_ready: bool = True
    map_ready: bool = True
    export_ready: bool = True


class SitePacket(BaseModel):
    entity: PropertyEntity
    scene: SceneModelResult
    evidence: list[EvidenceItem] = Field(default_factory=list)
    build_status: BuildStatus
    demand: DemandEstimate | None = None
    inference: list[InferenceRecord] = Field(default_factory=list)
    conclusion: Conclusion
    review_queue: list[ReviewItem] = Field(default_factory=list)
    candidate_quality_status: str = Field(
        default="ready",
        pattern="^(ready|review_required|blocked_quality)$",
    )
    visibility: CandidateVisibility = Field(default_factory=CandidateVisibility)
    quality_issues: list[str] = Field(default_factory=list)


class ScanRunResult(BaseModel):
    scan_run: ScanRun
    packets: list[SitePacket] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    storage_mode: str | None = None
    persisted_counts: dict[str, int] = Field(default_factory=dict)
