from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from isite2.domain.models import (
    BuildStatus,
    Conclusion,
    DemandEstimate,
    EvidenceItem,
    InferenceRecord,
    ReviewItem,
    ScanRunResult,
    ScanScope,
    SceneModelResult,
    SitePacket,
)


class ScanRunRepository(Protocol):
    def create(self, scope: ScanScope) -> ScanRunResult:
        ...

    def save(self, result: ScanRunResult) -> ScanRunResult:
        ...

    def list(self) -> list[ScanRunResult]:
        ...

    def get(self, run_id: UUID) -> ScanRunResult | None:
        ...


class PropertyRepository(Protocol):
    def list(self, filters: dict[str, Any] | None = None) -> list[SitePacket]:
        ...

    def get(self, property_id: UUID) -> SitePacket | None:
        ...


class EvidenceRepository(Protocol):
    def list_for_property(self, property_id: UUID) -> list[EvidenceItem]:
        ...


class SceneModelRepository(Protocol):
    def get_for_property(self, property_id: UUID) -> SceneModelResult | None:
        ...


class BuildStatusRepository(Protocol):
    def get_for_property(self, property_id: UUID) -> BuildStatus | None:
        ...


class DemandRepository(Protocol):
    def get_for_property(self, property_id: UUID) -> DemandEstimate | None:
        ...


class InferenceRepository(Protocol):
    def list_for_property(self, property_id: UUID) -> list[InferenceRecord]:
        ...


class ConclusionRepository(Protocol):
    def get_for_property(self, property_id: UUID) -> Conclusion | None:
        ...


class ReviewQueueRepository(Protocol):
    def list_for_property(self, property_id: UUID) -> list[ReviewItem]:
        ...


class OutputArtifactRepository(Protocol):
    def add(self, scan_run_id: UUID, artifact_type: str, path: str) -> dict[str, Any]:
        ...

    def list_for_run(self, scan_run_id: UUID) -> list[dict[str, Any]]:
        ...
