from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class HumanFeedbackRecord(BaseModel):
    feedback_id: UUID = Field(default_factory=uuid4)
    property_id: UUID | None = None
    field_name: str
    old_value: str | None = None
    new_value: str
    reviewer: str | None = None
    note: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RuleVersion(BaseModel):
    version: str
    description: str
    config_snapshot: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FreshnessTask(BaseModel):
    task_id: UUID = Field(default_factory=uuid4)
    source_url: str
    reason: str
    status: str = "queued"
    due_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InferenceCorrection(BaseModel):
    correction_id: UUID = Field(default_factory=uuid4)
    property_id: UUID
    inferred_field: str
    old_value: str
    corrected_value: str
    evidence_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceReliabilityScore(BaseModel):
    source_name: str
    source_tier: str
    successful_extractions: int = 0
    failed_extractions: int = 0
    conflict_count: int = 0
    score: float = 0.5
