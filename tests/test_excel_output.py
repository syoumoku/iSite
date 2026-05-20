from uuid import uuid4

from openpyxl import load_workbook
import pytest

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
    SceneModelResult,
    SitePacket,
)
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.output.excel import create_excel_workbook, write_excel_skeleton
from isite2.repositories.memory import InMemoryScanRunRepository
from isite2.rules.config_loader import load_output_template, scene_definitions


def test_excel_skeleton_matches_config_headers(tmp_path) -> None:
    output = tmp_path / "isite2_excel_skeleton.xlsx"
    write_excel_skeleton(output)

    workbook = load_workbook(output)
    excel = load_output_template()["excel"]

    assert workbook.sheetnames == excel["required_sheets"]
    assert [cell.value for cell in workbook["主表"][1]] == excel["main_columns"]
    assert [cell.value for cell in workbook["机场"][1]] == excel["main_columns"]
    assert "Google地图链接" in [cell.value for cell in workbook["主表"][1]]
    assert workbook["Proxy模型"].max_row == len(scene_definitions()) + 1
    assert workbook["方法说明"].max_row > 1


def test_excel_can_include_pipeline_packets(tmp_path) -> None:
    repository = InMemoryScanRunRepository()
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["excel"],
        },
        repository,
    )
    output = tmp_path / "isite2_excel_with_packets.xlsx"
    write_excel_skeleton(output, packets=result.packets)

    workbook = load_workbook(output)
    main_row = [cell.value for cell in workbook["主表"][2]]
    review_row = [cell.value for cell in workbook["复核队列"][2]]

    assert main_row[-1].startswith("https://www.google.com/maps/search/")
    assert "旅客吞吐量" in main_row[6]
    assert "补查" in review_row[-1]


def test_excel_excludes_blocked_quality_candidates_by_default(tmp_path) -> None:
    repository = InMemoryScanRunRepository()
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Exampleland"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["excel"],
        },
        repository,
    )
    output = tmp_path / "isite2_excel_blocked_filtered.xlsx"

    write_excel_skeleton(output, packets=result.packets)

    workbook = load_workbook(output)
    assert workbook["主表"].max_row == 1


@pytest.mark.parametrize(
    ("scene_type", "weak_metric", "strong_metric", "strong_value"),
    [
        (
            "airport_terminal",
            "gateway_role",
            "annual_passenger_throughput",
            "GCAA 2024: 2,805,347 passengers/year.",
        ),
        (
            "convention_center",
            "venue_role",
            "exhibition_area",
            "20,000 sqm exhibition area.",
        ),
        ("stadium", "main_venue_role", "seat_count", "40,000 seats."),
        ("luxury_hotel_mice", "brand", "keys", "250 rooms."),
        ("mall_mixed_use", "flagship_position", "gla", "60,000 sqm GLA."),
        ("office_government", "cbd_role", "office_gfa", "80,000 sqm office GFA."),
        ("hospital", "referral_role", "beds", "600 beds."),
        ("university", "campus_center_role", "enrollment", "25,000 students."),
        (
            "transport_hub",
            "hub_role",
            "daily_ridership",
            "120,000 riders/day.",
        ),
    ],
)
def test_excel_main_row_prefers_scene_objective_metric(
    scene_type: str,
    weak_metric: str,
    strong_metric: str,
    strong_value: str,
) -> None:
    packet = _packet_with_ordered_evidence(
        scene_type=scene_type,
        weak_metric=weak_metric,
        strong_metric=strong_metric,
        strong_value=strong_value,
    )

    workbook = create_excel_workbook([packet])
    main_row = [cell.value for cell in workbook["主表"][2]]

    assert main_row[6].startswith(f"{strong_metric}:")
    assert strong_value in main_row[6]
    assert weak_metric not in main_row[6]
    assert main_row[15] == "Generic stale role-based reason."


def test_excel_main_table_sorts_by_annual_visits_desc() -> None:
    low = _packet_with_ordered_evidence(
        scene_type="airport_terminal",
        property_name="Low Traffic Airport",
        annual_visits=1_200_000,
        weak_metric="gateway_role",
        strong_metric="annual_passenger_throughput",
        strong_value="1,200,000 passengers/year.",
    )
    missing = _packet_with_ordered_evidence(
        scene_type="airport_terminal",
        property_name="Missing Traffic Airport",
        annual_visits=None,
        weak_metric="gateway_role",
        strong_metric="annual_passenger_throughput",
        strong_value="800,000 passengers/year.",
    )
    high = _packet_with_ordered_evidence(
        scene_type="airport_terminal",
        property_name="High Traffic Airport",
        annual_visits=9_800_000,
        weak_metric="gateway_role",
        strong_metric="annual_passenger_throughput",
        strong_value="9,800,000 passengers/year.",
    )

    workbook = create_excel_workbook([low, missing, high])

    assert [workbook["主表"][row][2].value for row in range(2, 5)] == [
        "High Traffic Airport",
        "Low Traffic Airport",
        "Missing Traffic Airport",
    ]


def test_excel_scene_sheet_sorts_by_scene_primary_metric_desc() -> None:
    medium = _packet_with_ordered_evidence(
        scene_type="stadium",
        property_name="Medium Stadium",
        annual_visits=10_000_000,
        weak_metric="main_venue_role",
        strong_metric="seat_count",
        strong_value="45,000 seats.",
    )
    low = _packet_with_ordered_evidence(
        scene_type="stadium",
        property_name="Low Stadium",
        annual_visits=99_000_000,
        weak_metric="main_venue_role",
        strong_metric="seat_count",
        strong_value="20,000 seats.",
    )
    high = _packet_with_ordered_evidence(
        scene_type="stadium",
        property_name="High Stadium",
        annual_visits=1_000_000,
        weak_metric="main_venue_role",
        strong_metric="seat_count",
        strong_value="65,000 seats.",
    )

    workbook = create_excel_workbook([medium, low, high])

    assert [workbook["体育场"][row][2].value for row in range(2, 5)] == [
        "High Stadium",
        "Medium Stadium",
        "Low Stadium",
    ]


def _packet_with_ordered_evidence(
    *,
    scene_type: str,
    weak_metric: str,
    strong_metric: str,
    strong_value: str,
    property_name: str = "Objective Metric Test Property",
    annual_visits: int | None = 1_000_000,
) -> SitePacket:
    property_id = uuid4()
    scene_rule = scene_definitions()[scene_type]
    scene_form = (
        SceneForm.SEMI_OPEN
        if scene_rule["scene_form"] == SceneForm.SEMI_OPEN.value
        else SceneForm.INDOOR
    )
    return SitePacket(
        entity=PropertyEntity(
            property_id=property_id,
            country="Ghana",
            city="Accra",
            property_name=property_name,
            scene_type=scene_type,
            scene_form=scene_form,
            latitude=5.6,
            longitude=-0.1,
            geocode_precision="Property",
            google_maps_link="https://www.google.com/maps/search/?api=1&query=5.6,-0.1",
        ),
        scene=SceneModelResult(
            area_metric_name=scene_rule["area_metric"],
            primary_value_indicators=list(scene_rule["primary_indicators"]),
            proxy_basis=scene_rule["proxy_basis"][0],
            proxy_level=ProxyLevel.P1_STRONG,
            annual_visits_est=annual_visits,
        ),
        evidence=[
            EvidenceItem(
                property_id=property_id,
                field_group=weak_metric,
                indicator_name=weak_metric,
                field_value="National role / gateway description only.",
                source_name="Directory seed",
                source_tier=SourceTier.TIER_3,
                source_url="https://example.com/role",
                evidence_type=EvidenceType.DIRECT,
                cross_check_status=CrossCheckStatus.SINGLE_SOURCE,
            ),
            EvidenceItem(
                property_id=property_id,
                field_group=strong_metric,
                indicator_name=strong_metric,
                field_value=strong_value,
                source_name="Official statistics",
                source_tier=SourceTier.TIER_1,
                source_url="https://example.com/statistics",
                evidence_type=EvidenceType.DIRECT,
                cross_check_status=CrossCheckStatus.PARTIAL,
            ),
        ],
        build_status=BuildStatus(
            indoor_system_presence=IndoorSystemPresence.NO_PUBLIC_EVIDENCE,
            indoor_system_type=IndoorSystemType.UNKNOWN,
            indoor_rat=IndoorRAT.UNKNOWN,
            build_evidence_status=BuildEvidenceStatus.UNKNOWN,
        ),
        conclusion=Conclusion(
            evidence_status=EvidenceStatus.SUPPORTED,
            value_class=ValueClass.CITY_CORE,
            action_class=ActionClass.SURVEY_FIRST,
            recommended_solution=RecommendedSolution.PRRU,
            reason_to_recommend="Generic stale role-based reason.",
            next_action="补查现网室分状态和运营商证据。",
        ),
    )
