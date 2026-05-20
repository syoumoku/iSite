from uuid import uuid4

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
from isite2.domain.models import (
    BuildStatus,
    Conclusion,
    EvidenceItem,
    PropertyEntity,
    PropertyHeroImage,
    SceneModelResult,
    SitePacket,
)
from isite2.rules.gates import evidence_gate, run_all_gates


def make_packet() -> SitePacket:
    pid = uuid4()
    return SitePacket(
        entity=PropertyEntity(
            property_id=pid,
            country="X",
            city="Y",
            property_name="Airport T1",
            scene_type="airport_terminal",
            scene_form=SceneForm.INDOOR,
            latitude=1,
            longitude=2,
            geocode_precision="terminal centroid",
            hero_image=PropertyHeroImage(
                url="https://example.com/airport-t1.jpg",
                alt_text="Airport T1 terminal",
                source_name="Airport media page",
                source_url="https://example.com/airport-t1",
                source_date="2026",
            ),
        ),
        scene=SceneModelResult(
            area_metric_name="Terminal Area",
            area_metric_status="Unknown",
            proxy_basis="Airport Passenger Throughput Proxy",
            proxy_level=ProxyLevel.P1_STRONG,
        ),
        evidence=[
            EvidenceItem(
                property_id=pid,
                field_group="annual_passenger_throughput",
                field_value="10M",
                source_name="Airport report",
                source_tier=SourceTier.TIER_1,
                source_url="https://example.com",
                evidence_type=EvidenceType.DIRECT,
                cross_check_status=CrossCheckStatus.SINGLE_SOURCE,
            )
        ],
        build_status=BuildStatus(
            indoor_system_presence=IndoorSystemPresence.UNKNOWN,
            indoor_system_type=IndoorSystemType.UNKNOWN,
            indoor_rat=IndoorRAT.UNKNOWN,
            build_evidence_status=BuildEvidenceStatus.UNKNOWN,
        ),
        conclusion=Conclusion(
            evidence_status=EvidenceStatus.SUPPORTED,
            value_class=ValueClass.CITY_CORE,
            action_class=ActionClass.SURVEY_FIRST,
            recommended_solution=RecommendedSolution.PRRU,
            reason_to_recommend="门户机场，年客流已支撑，室内覆盖需求明确。",
            next_action="补查运营商室分公告。",
        ),
    )


def test_evidence_gate_passes_with_tier_1() -> None:
    packet = make_packet()
    assert evidence_gate(packet.evidence).passed


def test_all_gates_pass_for_valid_packet() -> None:
    packet = make_packet()
    assert all(g.passed for g in run_all_gates(packet))
