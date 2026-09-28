from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from isite2.domain.models import ScanRun, ScanRunResult, ScanScope
from isite2.property_search import rank_property_search_rows
from isite2.public_reports import country_export_state_hash


class InMemoryScanRunRepository:
    def __init__(self) -> None:
        self._runs: dict[UUID, ScanRunResult] = {}
        self._artifacts: dict[UUID, list[dict[str, Any]]] = {}

    def create(self, scope: ScanScope) -> ScanRunResult:
        result = ScanRunResult(scan_run=ScanRun(scope=scope), storage_mode="memory")
        self._runs[result.scan_run.run_id] = result
        return result

    def save(self, result: ScanRunResult) -> ScanRunResult:
        result.storage_mode = "memory"
        result.persisted_counts = {
            "scan_runs": 1,
            "properties": len(result.packets),
            "scan_candidates": len(result.packets),
            "evidence_items": sum(len(packet.evidence) for packet in result.packets),
            "scene_model_results": len(result.packets),
            "build_statuses": len(result.packets),
            "demand_estimates": sum(packet.demand is not None for packet in result.packets),
            "inference_records": sum(len(packet.inference) for packet in result.packets),
            "conclusions": len(result.packets),
            "review_queue": sum(len(packet.review_queue) for packet in result.packets),
            "source_cache": len(_source_urls(result)),
        }
        self._runs[result.scan_run.run_id] = result
        return result

    def list(self) -> list[ScanRunResult]:
        return sorted(self._runs.values(), key=lambda item: item.scan_run.created_at)

    def get(self, run_id: UUID) -> ScanRunResult | None:
        return self._runs.get(run_id)

    def list_properties(self, filters: dict[str, Any] | None = None) -> list:
        filters = filters or {}
        scan_run_id = filters.get("scan_run_id")
        runs = [
            result
            for result in self.list()
            if scan_run_id is None or str(result.scan_run.run_id) == str(scan_run_id)
        ]
        packets = [packet for result in runs for packet in result.packets]
        if scan_run_id is None:
            packets = _latest_packet_per_property(packets)
        rest = {key: value for key, value in filters.items() if key != "scan_run_id"}
        return [packet for packet in packets if _packet_matches(packet, rest)]

    def get_property(self, property_id: UUID) -> object | None:
        for packet in self.list_properties():
            if packet.entity.property_id == property_id:
                return packet
        return None

    def search_properties(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        packets = [
            packet
            for packet in self.list_properties()
            if packet.visibility.main_table_ready
        ]
        return rank_property_search_rows(
            [
                {
                    "property_id": str(packet.entity.property_id),
                    "property_name": packet.entity.property_name,
                    "aliases": list(packet.entity.aliases),
                    "country": packet.entity.country,
                    "city": packet.entity.city,
                    "city_id": (
                        packet.entity.city_assignment.city_id
                        if packet.entity.city_assignment is not None
                        else None
                    ),
                    "scene_type": packet.entity.scene_type,
                }
                for packet in packets
            ],
            query,
            limit=limit,
        )

    def country_export_state_hash(self, country: str, *, public: bool = False) -> str:
        latest: dict[str, tuple[str, Any]] = {}
        for result in self.list():
            for packet in result.packets:
                if packet.entity.country != country:
                    continue
                latest[str(packet.entity.property_id)] = (str(result.scan_run.run_id), packet)
        return country_export_state_hash(
            [
                {
                    "property_id": property_id,
                    "scan_run_id": scan_run_id,
                    "export_ready": packet.visibility.export_ready,
                }
                for property_id, (scan_run_id, packet) in latest.items()
            ]
        )

    def clear(self) -> None:
        self._runs.clear()
        self._artifacts.clear()

    def add_output_artifact(
        self,
        scan_run_id: UUID,
        artifact_type: str,
        path: str,
        filter_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact = {
            "artifact_id": str(uuid4()),
            "scan_run_id": str(scan_run_id),
            "artifact_type": artifact_type,
            "path": path,
            "filter_snapshot": filter_snapshot or {},
        }
        self._artifacts.setdefault(scan_run_id, []).append(artifact)
        return artifact

    def list_output_artifacts(self, scan_run_id: UUID) -> list[dict[str, Any]]:
        return self._artifacts.get(scan_run_id, [])

    def persisted_counts(self, run_id: UUID) -> dict[str, int]:
        result = self.get(run_id)
        return result.persisted_counts if result is not None else {}


_DEFAULT_REPOSITORY = InMemoryScanRunRepository()


def get_default_repository() -> InMemoryScanRunRepository:
    return _DEFAULT_REPOSITORY


def _source_urls(result: ScanRunResult) -> set[str]:
    return {
        str(evidence.source_url)
        for packet in result.packets
        for evidence in packet.evidence
    }


def _latest_packet_per_property(packets: list) -> list:
    latest: dict[tuple[str, str, str, str], object] = {}
    for packet in packets:
        key = (
            packet.entity.country,
            packet.entity.city,
            packet.entity.property_name,
            packet.entity.scene_type,
        )
        latest[key] = packet
    return list(latest.values())


def _packet_matches(packet, filters: dict[str, Any]) -> bool:
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
