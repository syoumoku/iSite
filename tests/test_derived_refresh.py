from __future__ import annotations

from sqlalchemy import text

from isite2.growth.derived_refresh import (
    HARD_EVIDENCE_LABEL,
    LOW_EVIDENCE_LABEL,
    RuleSafetyFallbackProvider,
    _best_metric,
    _numeric_value_and_unit,
    refresh_active_derived_info,
)
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


class FakeGptDerivedProvider:
    provider_name = "fake_gpt"

    def __init__(self) -> None:
        self.calls = 0

    def derive(self, packet: dict) -> dict:
        self.calls += 1
        return {
            "provider_type": "gpt",
            "provider_model": "fake-gpt",
            "metric_availability_level": HARD_EVIDENCE_LABEL,
            "selected_primary_metric": {
                "field_group": "annual_passenger_throughput",
                "field_value": "12,000,000 passengers in 2025",
                "source_urls": ["https://example.org/traffic"],
                "numeric_value": 12_000_000,
                "unit": "visits/year",
            },
            "annual_visits_est": 12_000_000,
            "capacity_estimate": 12_000_000,
            "capacity_unit": "visits/year",
            "capacity_basis": "Annual passenger throughput",
            "evidence_status": "Supported",
            "value_class": "National Flagship",
            "action_class": "Survey First",
            "recommended_solution": "pRRU",
            "reason_to_recommend": "GPT聚合客观客流证据后判断为国家级旗舰机场，推荐pRRU。",
            "next_action": "补查运营商室分公告，并核验机场2025年客流统计的第二来源。",
            "inference_basis": "12,000,000 passengers in 2025",
            "inference_chain": "official traffic evidence -> annual_visits_est=12000000",
            "inference_confidence": "Conservative",
            "review_items": [],
        }


class OverconfidentGatewayProvider(FakeGptDerivedProvider):
    def derive(self, packet: dict) -> dict:
        decision = super().derive(packet)
        decision["selected_primary_metric"] = {
            "field_group": "gateway_role",
            "field_value": "Gateway role only",
            "numeric_value": 20_000_000,
            "unit": "visits/year",
        }
        decision["annual_visits_est"] = 20_000_000
        return decision


class OfficeCapacityConfusionProvider(FakeGptDerivedProvider):
    def derive(self, packet: dict) -> dict:
        decision = super().derive(packet)
        decision["selected_primary_metric"] = {
            "field_group": "office_gfa",
            "field_value": "office_gfa: Gross leasable area: 41,000 sqm",
            "numeric_value": 41_000,
            "unit": "sqm",
        }
        decision["annual_visits_est"] = 410_000
        decision["capacity_estimate"] = 4_100
        decision["capacity_unit"] = "persons"
        return decision


class TransportAnnualConfusionProvider(FakeGptDerivedProvider):
    def derive(self, packet: dict) -> dict:
        decision = super().derive(packet)
        decision["selected_primary_metric"] = {
            "field_group": "daily_ridership",
            "field_value": "Daily passenger traffic: 180,000 passengers/day",
            "numeric_value": 180_000,
            "unit": "visits/day",
        }
        decision["annual_visits_est"] = 21_900_000_000
        decision["capacity_estimate"] = 60_000_000
        decision["capacity_unit"] = "visits/day"
        return decision


class AirportYearConfusionProvider(FakeGptDerivedProvider):
    def derive(self, packet: dict) -> dict:
        decision = super().derive(packet)
        decision["selected_primary_metric"] = {
            "field_group": "annual_passenger_throughput",
            "field_value": "2024 annual passenger traffic: 37.6 million passengers",
            "numeric_value": 2024,
            "unit": "passengers/year",
        }
        decision["annual_visits_est"] = 2024
        decision["capacity_estimate"] = 2024
        decision["capacity_unit"] = "passengers/year"
        decision["inference_chain"] = "incorrectly picked reporting year"
        return decision


class VersionedFakeGptProvider(FakeGptDerivedProvider):
    def __init__(self, annual_visits: int = 12_000_000) -> None:
        super().__init__()
        self.annual_visits = annual_visits

    def derive(self, packet: dict) -> dict:
        decision = super().derive(packet)
        decision["annual_visits_est"] = self.annual_visits
        decision["selected_primary_metric"]["numeric_value"] = self.annual_visits
        decision["selected_primary_metric"]["field_value"] = (
            f"{self.annual_visits:,} passengers in 2025"
        )
        decision["inference_chain"] = (
            f"official traffic evidence -> annual_visits_est={self.annual_visits}"
        )
        return decision


def test_gpt_derived_refresh_updates_scene_demand_and_conclusion(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=FakeGptDerivedProvider(),
        cache_dir=None,
    )
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert summary["provider"] == "fake_gpt"
    assert packet.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert packet.scene.area_metric_status == "GPT Aggregated Direct Metric"
    assert packet.scene.area_metric_name == "Annual Passenger Throughput"
    assert packet.scene.annual_visits_est == 10_000_000
    assert packet.demand is not None
    assert packet.demand.daily_visits and packet.demand.daily_visits > 27_000
    assert packet.conclusion.value_class == "National Flagship"
    assert packet.conclusion.reason_to_recommend.startswith("GPT聚合客观客流证据")
    assert "annual_passenger_throughput direct evidence" in (
        packet.inference[0].inference_chain
    )


def test_gpt_refresh_downgrades_gateway_role_to_low_evidence(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_group = 'gateway_role',
                    indicator_name = 'gateway_role',
                    field_value = 'Gateway airport role without passenger throughput'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=OverconfidentGatewayProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == LOW_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_value is None
    assert refreshed.scene.annual_visits_est is None
    assert refreshed.conclusion.evidence_status == "Insufficient"
    assert refreshed.conclusion.value_class == "Observation"
    assert any(
        review.review_type == "gpt_derived_info_refresh"
        for review in refreshed.review_queue
    )


def test_rule_refresh_preserves_survey_first_for_designated_missing_metric(
    tmp_path,
) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)
    scan_run_id = str(result.scan_run.run_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_group = 'property_identity',
                    indicator_name = 'property_identity',
                    field_value = 'Designated lead identity confirmed'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO review_queue (
                    id, property_id, scan_run_id, reason, next_action, status,
                    review_type, severity, gate_name, field_path,
                    blocking_surfaces, created_at
                ) VALUES (
                    'designated-review', :property_id, :scan_run_id,
                    '指定线索缺少主指标。', '补查量化主指标。', 'open',
                    'designated_lead_primary_metric', 'high',
                    'candidate_quality', 'scene.primary_metric', '[]', CURRENT_TIMESTAMP
                )
                """
            ),
            {"property_id": property_id, "scan_run_id": scan_run_id},
        )

    refresh_active_derived_info(
        repository.engine,
        scan_run_id=scan_run_id,
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
        force=True,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == LOW_EVIDENCE_LABEL
    assert refreshed.conclusion.evidence_status == "Insufficient"
    assert refreshed.conclusion.value_class == "Observation"
    assert refreshed.conclusion.action_class == "Survey First"


def test_gpt_capacity_estimate_cannot_override_objective_area_metric(tmp_path) -> None:
    repository = _repository_with_office(tmp_path)
    result = repository.list()[0]

    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=OfficeCapacityConfusionProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Office Gfa"
    assert refreshed.scene.area_metric_value == 41_000
    assert refreshed.scene.area_metric_unit == "sqm"


def test_office_tower_height_is_hard_primary_metric_without_visit_proxy(tmp_path) -> None:
    repository = _repository_with_office(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_group = 'tower_height',
                    indicator_name = 'tower_height',
                    field_value = 'Height: 240 meters.'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
        force=True,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert _best_metric(
        "office_government",
        [
            {
                "field_group": "tower_height",
                "indicator_name": "tower_height",
                "field_value": "Height: 240 meters.",
                "source_tier": "Tier 3",
            }
        ],
    ).numeric_unit == "meters"
    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Tower Height"
    assert refreshed.scene.area_metric_value == 240
    assert refreshed.scene.area_metric_unit == "meters"
    assert refreshed.scene.annual_visits_est is None
    assert summary["qa_after"]["data_integrity_gate"]["passed"] is True


def test_rule_refresh_derives_proxy_visits_and_busy_hour_trace_from_primary_metric(
    tmp_path,
) -> None:
    repository = _repository_with_stadium_capacity(tmp_path)
    result = repository.list()[0]

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.annual_visits_raw is None
    assert refreshed.scene.annual_visits_est == 600_000
    assert summary["counts"]["rule_analysis_requested_count"] == 1
    assert "gpt_analysis_requested_count" not in summary["counts"]
    assert refreshed.demand is not None
    assert refreshed.demand.busy_hour_traffic_gb is not None
    inference_by_field = {item.inferred_field: item for item in refreshed.inference}
    assert "annual_visits_est" in inference_by_field
    assert "busy_hour_traffic_gb" in inference_by_field
    assert "seat_count evidence" in inference_by_field["annual_visits_est"].inference_chain
    assert "40000 x 15" in inference_by_field["annual_visits_est"].inference_chain
    assert "busy_hour_users = daily_visits x attach_rate" in (
        inference_by_field["busy_hour_traffic_gb"].inference_chain
    )


def test_daily_ridership_prefers_daily_value_over_annual_projection(tmp_path) -> None:
    repository = _repository_with_transport_hub(tmp_path)
    result = repository.list()[0]

    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=TransportAnnualConfusionProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Daily Ridership"
    assert refreshed.scene.area_metric_value == 180_000
    assert refreshed.scene.area_metric_unit == "visits/day"
    assert refreshed.scene.annual_visits_est == 65_700_000


def test_annual_footfall_text_with_daily_visitation_is_not_passthrough(tmp_path) -> None:
    repository = _repository_with_mall_daily_footfall(tmp_path)
    result = repository.list()[0]

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Annual Footfall"
    assert refreshed.scene.area_metric_value == 80_000
    assert refreshed.scene.area_metric_unit == "visits/day"
    assert refreshed.scene.annual_visits_raw is None
    assert refreshed.scene.annual_visits_est == 29_200_000
    assert refreshed.demand is not None
    assert refreshed.demand.daily_visits == 80_000
    assert "daily/business-day metric x 365 days" in refreshed.inference[0].inference_chain
    assert summary["qa_after"]["data_integrity_gate"]["passed"] is True


def test_mall_embedded_visit_metric_overrides_gla_proxy(tmp_path) -> None:
    repository = _repository_with_mall_embedded_footfall(tmp_path)
    result = repository.list()[0]

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Annual Footfall"
    assert refreshed.scene.area_metric_value == 10_000_000
    assert refreshed.scene.area_metric_unit == "visits/year"
    assert refreshed.scene.annual_visits_raw == 10_000_000
    assert refreshed.scene.annual_visits_est == 10_000_000
    assert summary["qa_after"]["data_integrity_gate"]["passed"] is True


def test_transport_line_count_does_not_create_annual_visit_proxy(tmp_path) -> None:
    repository = _repository_with_transport_hub_line_count(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE scene_model_results
                SET annual_visits_est = 730,
                    area_metric_name = 'Line Count',
                    area_metric_value = 2,
                    area_metric_unit = 'facilities'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Line Count"
    assert refreshed.scene.area_metric_value == 2
    assert refreshed.scene.area_metric_unit == "lines"
    assert refreshed.scene.annual_visits_est is None
    assert (
        summary["qa_before"]["data_integrity_gate"]["counts"][
            "transport_line_count_with_annual_visits_count"
        ]
        == 1
    )
    assert summary["qa_after"]["data_integrity_gate"]["passed"] is True


def test_annual_passenger_metric_overrides_gpt_year_confusion(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_value = '2024 annual passenger traffic: 37.6 million passengers'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE scene_model_results
                SET annual_visits_est = 2024,
                    area_metric_name = 'Terminal Area',
                    area_metric_value = NULL,
                    area_metric_unit = NULL
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=AirportYearConfusionProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Annual Passenger Throughput"
    assert refreshed.scene.area_metric_value == 37_600_000
    assert refreshed.scene.annual_visits_raw == 37_600_000
    assert refreshed.scene.annual_visits_est == 37_600_000
    assert (
        summary["qa_before"]["data_integrity_gate"]["counts"][
            "year_like_annual_visits_count"
        ]
        == 1
    )
    assert summary["qa_after"]["data_integrity_gate"]["passed"] is True


def test_mislabeled_annual_metric_with_only_year_is_not_hard_evidence(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_value = :field_value
                WHERE property_id = :property_id
                """
            ),
            {
                "field_value": (
                    "international passenger airport role: declared international in 1995"
                ),
                "property_id": property_id,
            },
        )

    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == LOW_EVIDENCE_LABEL
    assert refreshed.scene.annual_visits_est is None


def test_airport_terminal_capacity_does_not_become_annual_visits(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_group = 'terminal_capacity',
                    indicator_name = 'terminal_capacity',
                    field_value = 'terminal capacity: capacity to process 2,000 passengers per hour.'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )
        connection.execute(
            text(
                """
                UPDATE scene_model_results
                SET annual_visits_est = 2000,
                    area_metric_name = 'Terminal Capacity',
                    area_metric_value = 2000,
                    area_metric_unit = 'passengers/hour'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
        force=True,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Terminal Capacity"
    assert refreshed.scene.area_metric_value == 2000
    assert refreshed.scene.annual_visits_est is None
    assert (
        summary["qa_before"]["data_integrity_gate"]["counts"][
            "year_like_annual_visits_count"
        ]
        == 1
    )
    assert summary["qa_after"]["data_integrity_gate"]["passed"] is True


def test_year_like_direct_annual_visit_value_is_blocked(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_group = 'annual_passenger_throughput',
                    indicator_name = 'annual_passenger_throughput',
                    field_value = 'Passenger throughput: 2,025 passengers/year'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )
        connection.execute(
            text(
                """
                UPDATE scene_model_results
                SET annual_visits_est = 2025,
                    area_metric_name = 'Annual Passenger Throughput',
                    area_metric_value = 2025,
                    area_metric_unit = 'visits/year'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
        force=True,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.area_metric_name == "Annual Passenger Throughput"
    assert refreshed.scene.area_metric_value == 2025
    assert refreshed.scene.annual_visits_raw is None
    assert refreshed.scene.annual_visits_est is None
    assert (
        summary["qa_before"]["data_integrity_gate"]["counts"][
            "year_like_annual_visits_count"
        ]
        == 1
    )
    assert summary["qa_after"]["data_integrity_gate"]["passed"] is True


def test_passenger_throughput_parser_handles_years_and_dot_grouping() -> None:
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "MediaWiki infobox Total Passengers (2025): 970.349",
    ) == (970_349, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "annual_passenger_throughput: 43.712 million passengers in 2024",
    ) == (43_712_000, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "2024 annual passenger traffic: 12.8 million passengers",
    ) == (12_800_000, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "Passengers: 5.0M/yr, reported 2024",
    ) == (5_000_000, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "annual passenger throughput: 5.0M passengers/year, reported 2024",
    ) == (5_000_000, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "Wikidata P3872 patronage statement: 17 passengers/year.",
    ) == (17, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "annual_passenger_throughput: 1.36667e+06 passengers/year",
    ) == (1_366_670, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "Annual traffic was 37.6m passengers in 2025.",
    ) == (37_600_000, "visits/year")
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        (
            "Passenger airport terminal with two asphalt runways of "
            "2,230 m and 3,000 m."
        ),
    ) == (None, None)


def test_annual_visit_parser_rejects_route_counts_and_traffic_shares() -> None:
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        (
            "scheduled passenger traffic: flights serve 2 destinations with "
            "1 airline"
        ),
    ) == (None, None)
    assert _numeric_value_and_unit(
        "annual_passenger_throughput",
        "passenger traffic share: airport handles 23% of national air traffic",
    ) == (None, None)


def test_daily_ridership_parser_ignores_annual_value_in_same_sentence() -> None:
    assert _numeric_value_and_unit(
        "daily_ridership",
        (
            "daily_ridership_and_line_count: Santo Domingo Metro table reports "
            "2 lines, 39 stations, daily ridership of 284,941, and annual "
            "ridership of 104,003,341 for 2022/2023."
        ),
    ) == (284_941, "visits/day")


def test_seat_count_parser_ignores_money_context() -> None:
    assert _numeric_value_and_unit(
        "seat_count",
        (
            "seat_count: Transfermarkt lists total capacity 38,000; Liberia "
            "Ministry separately confirms a US$18 million renovation agreement."
        ),
    ) == (38_000, "seats")
    assert _numeric_value_and_unit(
        "seat_count",
        (
            "seat count: Largest stadium in Cameroon by capacity; 60,000-seat "
            "Olembe stadium and sports complex on an 84-acre / 400,000 m2 site."
        ),
    ) == (60_000, "seats")


def test_hotel_keys_parser_ignores_meeting_space_numbers() -> None:
    assert _numeric_value_and_unit(
        "keys",
        (
            "total meeting space and guest rooms: Cvent lists 82,175 sq. ft. "
            "total meeting space and 1,800 guest rooms."
        ),
    ) == (1_800, "rooms")
    assert _numeric_value_and_unit(
        "keys",
        (
            "keys: Cvent results list 3 meeting rooms, 50 guest rooms, "
            "968 sq. ft. largest room and 2,045 sq. ft. total event space."
        ),
    ) == (50, "rooms")


def test_hospital_staff_parser_requires_explicit_staff_context() -> None:
    assert _numeric_value_and_unit(
        "staff_count",
        "Hospital profile reports a workforce of 4,200 staff.",
    ) == (4_200, "people")
    assert _numeric_value_and_unit(
        "staff_count",
        "Hospital opened in 2025 and serves 4 cities.",
    ) == (None, None)


def test_convention_area_parser_rejects_complex_site_area() -> None:
    assert _numeric_value_and_unit(
        "exhibition_area",
        "Complex area: 3,000,000 sq m",
    ) == (None, None)
    assert _numeric_value_and_unit(
        "exhibition_area",
        "Exhibition hall area: 42,000 sq m",
    ) == (42_000, "sqm")


def test_numeric_parser_ignores_punctuated_reporting_years() -> None:
    assert _numeric_value_and_unit(
        "beds",
        "Official hospital profile: 2,000+ bed capacity",
    ) == (2_000, "beds")
    assert _numeric_value_and_unit(
        "beds",
        "beds: Beds: 2,000",
    ) == (2_000, "beds")
    assert _numeric_value_and_unit(
        "beds",
        "Current official hospital website: 500-bed facility",
    ) == (500, "beds")
    assert _numeric_value_and_unit(
        "beds",
        (
            "beds: VFMatch facility profile states CHUD-Borgou/Alibori caters for "
            "referred cases from 14 municipalities and had about 300 beds as of 2014."
        ),
    ) == (300, "beds")
    assert _numeric_value_and_unit(
        "beds",
        (
            "beds: Finance Ministry states each modern regional hospital is slated "
            "for 75 beds; Ministry of Health says Bath was commissioned on July 30, 2025."
        ),
    ) == (75, "beds")
    assert _numeric_value_and_unit(
        "beds",
        (
            "beds: Ministry of Health says six regional hospitals add 450 inpatient "
            "beds; 75 beds per facility implied by the six-site program; the Lima "
            "facility was commissioned on August 22, 2025."
        ),
    ) == (75, "beds")
    assert _numeric_value_and_unit(
        "daily_ridership",
        (
            "passenger usage and line count: Station article states Nuevo Tocumen "
            "is on Panama Metro Line 2; Spanish article notes it was among the five "
            "most used stations by card validations in 2024."
        ),
    ) == (None, None)
    assert _numeric_value_and_unit(
        "annual_footfall",
        "Average daily visitation: 80,000 people; 120,000 on special dates.",
    ) == (80_000, "visits/day")
    assert _numeric_value_and_unit(
        "outpatient_volume",
        "outpatient_volume: official profile says more than 3,000 consultations daily.",
    ) == (3_000, "visits/day")
    assert _numeric_value_and_unit(
        "interchange_volume",
        "interchange passenger volume: 55 million passengers per year.",
    ) == (55_000_000, "visits/year")
    assert _numeric_value_and_unit(
        "outpatient_volume",
        (
            "tertiary referral centre serving about 4.3 million people, with "
            "680 beds and daily rehabilitation outpatient activity."
        ),
    ) == (None, None)
    assert _numeric_value_and_unit(
        "daily_ridership",
        "MediaWiki infobox passengers: 77,000/business day <br /> 61,000/business day",
    ) == (77_000, "visits/day")


def test_wikidata_p81_explicit_count_is_transport_hub_primary_metric() -> None:
    metrics = [
        {
            "field_group": "line_count",
            "indicator_name": "line_count",
            "field_value": "2 rail/metro lines connected per Wikidata P81 statements",
            "source_name": "Wikidata/Wikipedia",
            "source_tier": "Tier 3",
            "source_url": "https://en.wikipedia.org/wiki/Baquedano_metro_station",
            "source_date": "2026-05-12",
        },
        {
            "field_group": "line_count",
            "indicator_name": "line_count",
            "field_value": (
                "line count: 1 metro line(s) served: línea 1 del Metro de Santiago"
            ),
            "source_name": "Wikipedia station article / Wikidata structured station statement",
            "source_tier": "Tier 3",
            "source_url": "https://en.wikipedia.org/wiki/Alcántara_metro_station",
            "source_date": "2026-05-25",
        },
    ]

    metric = _best_metric("transport_hub", metrics)
    assert metric is not None
    assert metric.field_key == "line_count"
    assert metric.numeric_value == 2
    assert metric.numeric_unit == "lines"


def test_line_count_parser_ignores_p81_property_id_and_line_labels() -> None:
    assert _numeric_value_and_unit("line_count", "Wikidata statement P81") == (None, None)
    assert _numeric_value_and_unit(
        "line_count",
        "served by línea 1 del Metro de Santiago",
    ) == (None, None)
    assert _numeric_value_and_unit(
        "line_count",
        "line count: 1 metro line(s) served: línea 1 del Metro de Santiago",
    ) == (1, "lines")


def test_unchanged_evidence_package_reuses_db_decision_without_gpt_call(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    first_provider = VersionedFakeGptProvider(annual_visits=12_000_000)

    first = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=first_provider,
        cache_dir=None,
    )
    second_provider = VersionedFakeGptProvider(annual_visits=99_000_000)
    second = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=second_provider,
        cache_dir=None,
    )
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert first_provider.calls == 1
    assert second_provider.calls == 0
    assert first["counts"]["gpt_analysis_requested_count"] == 1
    assert second["counts"]["skipped_unchanged_evidence"] == 1
    assert packet.scene.annual_visits_est == 10_000_000


def test_city_normalization_does_not_trigger_gpt_without_evidence_change(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    first_provider = VersionedFakeGptProvider(annual_visits=12_000_000)
    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=first_provider,
        cache_dir=None,
    )
    with repository.engine.begin() as connection:
        connection.execute(
            text("UPDATE properties SET city = 'Canonical Cairo' WHERE country = 'Egypt'")
        )

    second_provider = VersionedFakeGptProvider(annual_visits=99_000_000)
    second = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=second_provider,
        cache_dir=None,
    )

    assert first_provider.calls == 1
    assert second_provider.calls == 0
    assert second["counts"]["skipped_unchanged_evidence"] == 1


def test_evidence_package_change_triggers_new_gpt_call(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    first_provider = VersionedFakeGptProvider(annual_visits=12_000_000)
    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=first_provider,
        cache_dir=None,
    )
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO evidence_items (
                    id, property_id, scan_run_id, field_group, indicator_name,
                    field_value, evidence_type, source_name, source_tier, source_url,
                    source_date, cross_check_status, created_at
                ) VALUES (
                    'extra-evidence', :property_id, :scan_run_id,
                    'annual_passenger_throughput', 'annual_passenger_throughput',
                    '13,000,000 passengers in 2025', 'Direct', 'Second source',
                    'Tier 1', 'https://example.org/second-source', '2026',
                    'Partial Cross-check', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "property_id": property_id,
                "scan_run_id": str(result.scan_run.run_id),
            },
        )

    second_provider = VersionedFakeGptProvider(annual_visits=13_000_000)
    second = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=second_provider,
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert first_provider.calls == 1
    assert second_provider.calls == 1
    assert second["counts"]["gpt_analysis_requested_count"] == 1
    assert refreshed.scene.annual_visits_est == 13_000_000


def _repository_with_airport(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_airport_registry(),
    )
    return repository


def _repository_with_stadium_capacity(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_stadium.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Ghana"],
            "full_scan": True,
            "scene_types": ["stadium"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_stadium_registry(),
    )
    return repository


def _repository_with_office(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_office.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Philippines"],
            "full_scan": True,
            "scene_types": ["office_government"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_office_registry(),
    )
    return repository


def _repository_with_transport_hub(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_transport.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Philippines"],
            "full_scan": True,
            "scene_types": ["transport_hub"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_transport_hub_registry(),
    )
    return repository


def _repository_with_transport_hub_line_count(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_transport_line_count.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Chile"],
            "full_scan": True,
            "scene_types": ["transport_hub"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_transport_hub_line_count_registry(),
    )
    return repository


def _repository_with_mall_daily_footfall(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_mall_daily.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Brazil"],
            "full_scan": True,
            "scene_types": ["mall_mixed_use"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_mall_daily_footfall_registry(),
    )
    return repository


def _repository_with_mall_embedded_footfall(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_mall_embedded.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Kazakhstan"],
            "full_scan": True,
            "scene_types": ["mall_mixed_use"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_mall_embedded_footfall_registry(),
    )
    return repository


def _single_airport_registry() -> dict:
    return {
        "countries": {
            "Egypt": {
                "aliases": ["Egypt"],
                "bbox": {
                    "min_latitude": 22.0,
                    "max_latitude": 32.0,
                    "min_longitude": 24.0,
                    "max_longitude": 37.0,
                },
                "candidates": [
                    {
                        "property_name": "Cairo International Airport",
                        "city": "Cairo",
                        "scene_type": "airport_terminal",
                        "annual_visits": 10_000_000,
                        "coordinate": {
                            "latitude": 30.1219,
                            "longitude": 31.4056,
                            "geocode_precision": "airport terminal centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-14",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/cairo-airport.jpg",
                            "alt_text": "Cairo International Airport terminal",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/cairo-airport-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "annual_passenger_throughput",
                                "indicator_name": "annual_passenger_throughput",
                                "field_value": "10,000,000 passengers in 2024",
                                "source_name": "Example Airport Authority",
                                "source_tier": "Tier 1",
                                "source_url": "https://example.org/cairo-airport-traffic",
                                "source_date": "2026",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }


def _single_mall_daily_footfall_registry() -> dict:
    return {
        "countries": {
            "Brazil": {
                "aliases": ["Brazil"],
                "bbox": {
                    "min_latitude": -34.0,
                    "max_latitude": 6.0,
                    "min_longitude": -74.0,
                    "max_longitude": -34.0,
                },
                "candidates": [
                    {
                        "property_name": "Shopping Center Norte",
                        "city": "São Paulo",
                        "scene_type": "mall_mixed_use",
                        "coordinate": {
                            "latitude": -23.5157,
                            "longitude": -46.6174,
                            "geocode_precision": "mall centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-06-18",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/shopping-center-norte.jpg",
                            "alt_text": "Shopping Center Norte",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/shopping-center-norte-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "annual_footfall",
                                "indicator_name": "annual_footfall",
                                "field_value": (
                                    "Average daily visitation: 80,000 people; "
                                    "120,000 on special dates."
                                ),
                                "source_name": "Shopping Center Norte public profile",
                                "source_tier": "Tier 2",
                                "source_url": "https://www.centernorte.com.br/quem-somos",
                                "source_date": "2026",
                                "evidence_type": "Direct",
                            },
                            {
                                "field_group": "retail_gfa",
                                "indicator_name": "retail_gfa",
                                "field_value": (
                                    "retail gross floor area (ABL) in m2: retail gross "
                                    "floor area (ABL): 72538 m2"
                                ),
                                "source_name": "Prefeitura de Sao Paulo - Shopping Centers 2023",
                                "source_tier": "Tier 1",
                                "source_url": "https://example.org/shopping-centers-2023",
                                "source_date": "2023",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }


def _single_mall_embedded_footfall_registry() -> dict:
    return {
        "countries": {
            "Kazakhstan": {
                "aliases": ["Kazakhstan"],
                "bbox": {
                    "min_latitude": 40.0,
                    "max_latitude": 56.0,
                    "min_longitude": 46.0,
                    "max_longitude": 88.0,
                },
                "candidates": [
                    {
                        "property_name": "Khan Shatyr Entertainment Center",
                        "city": "Astana",
                        "scene_type": "mall_mixed_use",
                        "coordinate": {
                            "latitude": 51.132,
                            "longitude": 71.403,
                            "geocode_precision": "mall centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-07-01",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/khan-shatyr.jpg",
                            "alt_text": "Khan Shatyr",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/khan-shatyr-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "retail_gfa",
                                "indicator_name": "retail_gfa",
                                "field_value": (
                                    "retail_gfa: GLA: 56,376 m2; visitors per year: "
                                    "10,000,000+; daily visitors: 25,000+."
                                ),
                                "source_name": "Mall public profile",
                                "source_tier": "Tier 2",
                                "source_url": "https://example.org/khan-shatyr-profile",
                                "source_date": "2026",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }


def _single_transport_hub_registry() -> dict:
    return {
        "countries": {
            "Philippines": {
                "aliases": ["Philippines"],
                "bbox": {
                    "min_latitude": 4.5,
                    "max_latitude": 21.5,
                    "min_longitude": 116.0,
                    "max_longitude": 127.0,
                },
                "candidates": [
                    {
                        "property_name": "Parañaque Integrated Terminal Exchange",
                        "city": "Parañaque",
                        "scene_type": "transport_hub",
                        "coordinate": {
                            "latitude": 14.5099,
                            "longitude": 120.9913,
                            "geocode_precision": "landport terminal centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-22",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/pitx.jpg",
                            "alt_text": "PITX terminal",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/pitx-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "daily_ridership",
                                "indicator_name": "daily_ridership",
                                "field_value": (
                                    "Daily passenger traffic: 180,000 passengers/day; "
                                    "projected 55 million to 60 million passengers/year"
                                ),
                                "source_name": "Example Transport Report",
                                "source_tier": "Tier 2",
                                "source_url": "https://example.org/pitx-traffic",
                                "source_date": "2026-03-12",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }


def _single_transport_hub_line_count_registry() -> dict:
    return {
        "countries": {
            "Chile": {
                "aliases": ["Chile"],
                "bbox": {
                    "min_latitude": -56.0,
                    "max_latitude": -17.0,
                    "min_longitude": -76.0,
                    "max_longitude": -66.0,
                },
                "candidates": [
                    {
                        "property_name": "Baquedano Station",
                        "city": "Santiago",
                        "scene_type": "transport_hub",
                        "coordinate": {
                            "latitude": -33.4374,
                            "longitude": -70.6348,
                            "geocode_precision": "metro station centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-25",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/baquedano.jpg",
                            "alt_text": "Baquedano metro station",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/baquedano-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "line_count",
                                "indicator_name": "line_count",
                                "field_value": (
                                    "2 rail/metro lines connected per Wikidata P81 statements"
                                ),
                                "source_name": "Wikipedia station article / Wikidata statement",
                                "source_tier": "Tier 3",
                                "source_url": (
                                    "https://en.wikipedia.org/wiki/Baquedano_metro_station"
                                ),
                                "source_date": "2026-05-25",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }


def _single_office_registry() -> dict:
    return {
        "countries": {
            "Philippines": {
                "aliases": ["Philippines"],
                "bbox": {
                    "min_latitude": 4.5,
                    "max_latitude": 21.5,
                    "min_longitude": 116.0,
                    "max_longitude": 127.0,
                },
                "candidates": [
                    {
                        "property_name": "GT Tower",
                        "city": "Makati",
                        "scene_type": "office_government",
                        "coordinate": {
                            "latitude": 14.5599,
                            "longitude": 121.0170,
                            "geocode_precision": "office tower centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-22",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/gt-tower.jpg",
                            "alt_text": "GT Tower",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/gt-tower-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "office_gfa",
                                "indicator_name": "office_gfa",
                                "field_value": "office_gfa: Gross leasable area: 41, 000 sq m",
                                "source_name": "Example Office Directory",
                                "source_tier": "Tier 2",
                                "source_url": "https://example.org/gt-tower",
                                "source_date": "2026",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }


def _single_stadium_registry() -> dict:
    return {
        "countries": {
            "Ghana": {
                "aliases": ["Ghana"],
                "bbox": {
                    "min_latitude": 4.5,
                    "max_latitude": 11.5,
                    "min_longitude": -3.5,
                    "max_longitude": 1.5,
                },
                "candidates": [
                    {
                        "property_name": "Accra Sports Stadium",
                        "city": "Accra",
                        "scene_type": "stadium",
                        "coordinate": {
                            "latitude": 5.5529,
                            "longitude": -0.1914,
                            "geocode_precision": "stadium venue centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-15",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/accra-sports-stadium.jpg",
                            "alt_text": "Accra Sports Stadium",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/accra-stadium-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "seat_count",
                                "indicator_name": "seat_count",
                                "field_value": "40,000 seats",
                                "source_name": "Example Stadium Authority",
                                "source_tier": "Tier 1",
                                "source_url": "https://example.org/accra-stadium-capacity",
                                "source_date": "2026",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }
