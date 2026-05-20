from __future__ import annotations

from dataclasses import dataclass, field

from isite2.domain.enums import SourceTier
from isite2.domain.models import EvidenceItem, SitePacket
from isite2.rules.candidate_quality import BLOCKED_QUALITY, evaluate_packet_quality


@dataclass
class GateResult:
    gate_name: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    blocking: bool = False
    blocking_surfaces: list[str] = field(default_factory=list)


def entity_gate(packet: SitePacket) -> GateResult:
    issues: list[str] = []
    entity = packet.entity
    if not entity.country:
        issues.append("country missing")
    if not entity.city:
        issues.append("city missing")
    if not entity.property_name:
        issues.append("property_name missing")
    if not entity.scene_type:
        issues.append("scene_type missing")
    if entity.latitude is None or entity.longitude is None:
        issues.append("lat/lon missing")
    if not entity.geocode_precision:
        issues.append("geocode_precision missing")
    return GateResult("entity_gate", not issues, issues)


def evidence_gate(evidence: list[EvidenceItem]) -> GateResult:
    issues: list[str] = []
    tier12_count = sum(e.source_tier in {SourceTier.TIER_1, SourceTier.TIER_2} for e in evidence)
    tier3_count = sum(e.source_tier == SourceTier.TIER_3 for e in evidence)
    if tier12_count < 1 and tier3_count < 2:
        issues.append("requires at least 1 Tier 1/2 source or 2 independent Tier 3 sources")
    if not evidence:
        issues.append("no evidence items")
    return GateResult("evidence_gate", not issues, issues)


def build_status_gate(packet: SitePacket) -> GateResult:
    build = packet.build_status
    issues: list[str] = []
    for field_name in [
        "indoor_system_presence",
        "indoor_system_type",
        "indoor_rat",
        "build_evidence_status",
    ]:
        if getattr(build, field_name) in (None, ""):
            issues.append(f"{field_name} missing")
    return GateResult("build_status_gate", not issues, issues)


def candidate_quality_gate(packet: SitePacket) -> GateResult:
    quality = evaluate_packet_quality(packet)
    return GateResult(
        "candidate_quality_gate",
        quality.status != BLOCKED_QUALITY,
        quality.issues,
        blocking=quality.status == BLOCKED_QUALITY,
        blocking_surfaces=quality.blocking_surfaces,
    )


def run_all_gates(packet: SitePacket) -> list[GateResult]:
    return [
        entity_gate(packet),
        evidence_gate(packet.evidence),
        build_status_gate(packet),
        candidate_quality_gate(packet),
    ]
