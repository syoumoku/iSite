from __future__ import annotations

from isite2.rules.traffic import TrafficMetricInput, estimate_traffic_v2
from isite2.rules.traffic_calibration import (
    TrafficBenchmarkSample,
    decide_traffic_activation,
    evaluate_traffic_rollout,
)
from isite2.rules.config_loader import load_traffic_model


def test_direct_annual_visits_remain_direct_without_synthetic_range() -> None:
    result = estimate_traffic_v2(
        "airport_terminal",
        [
            TrafficMetricInput(
                key="annual_passenger_throughput",
                value=37_600_000,
                unit="visits/year",
                evidence_id="airport-throughput",
            )
        ],
    )

    assert result.estimate_method == "direct_annual"
    assert result.annual_visits_p10 is None
    assert result.annual_visits_p50 == 37_600_000
    assert result.annual_visits_p90 is None
    assert result.selected_evidence_ids == ["airport-throughput"]
    assert "uncalibrated_scene" not in result.qa_flags


def test_stadium_uses_event_peak_instead_of_annual_average_for_busy_hour() -> None:
    result = estimate_traffic_v2(
        "stadium",
        [
            TrafficMetricInput(
                key="seat_count",
                value=60_000,
                unit="seats",
                evidence_id="stadium-seats",
            )
        ],
    )

    assert result.estimate_method == "scene_proxy"
    assert result.annual_visits_p10 < result.annual_visits_p50 < result.annual_visits_p90
    assert result.peak_day_visits_p50 > result.typical_day_visits_p50
    assert result.busy_hour_users_p50 > 0
    assert result.legacy_annual_visits_est == result.annual_visits_p50
    assert "uncalibrated_scene" in result.qa_flags


def test_daily_ridership_uses_scene_active_days_and_preserves_order() -> None:
    result = estimate_traffic_v2(
        "transport_hub",
        [
            TrafficMetricInput(
                key="daily_ridership",
                value=284_941,
                unit="visits/day",
                evidence_id="station-ridership",
            )
        ],
    )

    assert result.estimate_method == "direct_daily"
    assert result.annual_visits_p10 <= result.annual_visits_p50 <= result.annual_visits_p90
    assert result.typical_day_visits_p50 == 284_941
    assert result.legacy_annual_visits_est == result.annual_visits_p50


def test_daily_footfall_period_overrides_annual_field_name() -> None:
    result = estimate_traffic_v2(
        "mall_mixed_use",
        [
            TrafficMetricInput(
                key="annual_footfall",
                value=80_000,
                unit="visits/day",
                evidence_id="mall-daily-footfall",
            ),
            TrafficMetricInput(
                key="gla",
                value=65_284.2,
                unit="sqm",
                evidence_id="mall-gla",
            ),
        ],
    )

    assert result.estimate_method == "direct_daily"
    assert result.typical_day_visits_p50 == 80_000
    assert result.annual_visits_p50 == 28_800_000
    assert result.selected_evidence_ids == ["mall-daily-footfall"]


def test_non_visit_line_count_does_not_create_annual_visits() -> None:
    result = estimate_traffic_v2(
        "transport_hub",
        [
            TrafficMetricInput(
                key="line_count",
                value=2,
                unit="lines",
                evidence_id="connected-lines",
            )
        ],
    )

    assert result.estimate_method == "insufficient"
    assert result.annual_visits_p50 is None
    assert result.cannot_calculate_reason


def test_hotel_combines_accommodation_and_mice_components() -> None:
    result = estimate_traffic_v2(
        "luxury_hotel_mice",
        [
            TrafficMetricInput("keys", 300, "rooms", evidence_id="hotel-keys"),
            TrafficMetricInput(
                "ballroom_capacity",
                1_000,
                "people",
                evidence_id="hotel-ballroom",
            ),
        ],
    )

    assert result.estimate_method == "scene_components"
    assert result.annual_visits_p50 == 390_000
    assert result.peak_day_visits_p50 == 1_390
    assert result.selected_evidence_ids == ["hotel-keys", "hotel-ballroom"]
    assert result.parameter_snapshot["components"][0]["name"] == "accommodation"


def test_implausibly_small_hotel_metric_is_filtered() -> None:
    result = estimate_traffic_v2(
        "luxury_hotel_mice",
        [
            TrafficMetricInput(
                "ballroom_capacity",
                2,
                "people",
                evidence_id="polluted-ballroom",
            )
        ],
    )

    assert result.estimate_method == "insufficient"
    assert result.annual_visits_p50 is None
    assert result.qa_flags == ["implausible_metric_filtered:ballroom_capacity"]


def test_hospital_combines_outpatient_inpatient_and_staff_components() -> None:
    result = estimate_traffic_v2(
        "hospital",
        [
            TrafficMetricInput(
                "outpatient_volume",
                100_000,
                "visits/year",
                evidence_id="hospital-outpatient",
            ),
            TrafficMetricInput("beds", 500, "beds", evidence_id="hospital-beds"),
            TrafficMetricInput(
                "staff_count",
                1_000,
                "people",
                evidence_id="hospital-staff",
            ),
        ],
    )

    assert result.estimate_method == "scene_components"
    assert result.annual_visits_p10 == 455_000
    assert result.annual_visits_p50 == 550_000
    assert result.annual_visits_p90 == 695_000
    assert result.selected_evidence_ids == [
        "hospital-outpatient",
        "hospital-beds",
        "hospital-staff",
    ]


def test_input_hash_is_order_independent() -> None:
    metrics = [
        TrafficMetricInput("gla", 72_538, "sqm", evidence_id="gla"),
        TrafficMetricInput("annual_footfall", 29_200_000, "visits/year", evidence_id="footfall"),
    ]

    first = estimate_traffic_v2("mall_mixed_use", metrics)
    second = estimate_traffic_v2("mall_mixed_use", list(reversed(metrics)))

    assert first.input_hash == second.input_hash
    assert first.estimate_method == "direct_annual"
    assert first.annual_visits_p50 == 29_200_000


def test_scene_rollout_gate_requires_twenty_percent_mape_improvement() -> None:
    samples = [
        TrafficBenchmarkSample(
            scene_type="mall_mixed_use",
            actual_annual_visits=1_000_000,
            v1_annual_visits=1_500_000,
            v2_annual_visits_p50=1_200_000,
            property_id=str(index),
        )
        for index in range(30)
    ]

    gate = evaluate_traffic_rollout(samples)[0]

    assert gate.sample_count == 30
    assert gate.relative_improvement == 0.6
    assert gate.rollout_allowed is True


def test_small_scene_sample_is_marked_uncalibrated() -> None:
    samples = [
        TrafficBenchmarkSample(
            scene_type="stadium",
            actual_annual_visits=1_000_000,
            v1_annual_visits=1_500_000,
            v2_annual_visits_p50=1_200_000,
        )
    ]

    gate = evaluate_traffic_rollout(samples)[0]

    assert gate.status == "uncalibrated_scene"
    assert gate.rollout_allowed is False


def test_direct_evidence_activates_without_proxy_calibration() -> None:
    decision = decide_traffic_activation(
        country="Brazil",
        scene_type="airport_terminal",
        estimate_method="direct_annual",
        model_config=load_traffic_model(),
    )

    assert decision.activate_legacy is True
    assert decision.activation_status == "activated_direct"


def test_unapproved_proxy_remains_shadow_only() -> None:
    decision = decide_traffic_activation(
        country="Brazil",
        scene_type="stadium",
        estimate_method="scene_proxy",
        model_config=load_traffic_model(),
    )

    assert decision.activate_legacy is False
    assert decision.activation_status == "shadow_unapproved_proxy"


def test_explicit_all_mode_can_activate_proxy_for_controlled_rollout() -> None:
    decision = decide_traffic_activation(
        country="Brazil",
        scene_type="stadium",
        estimate_method="scene_proxy",
        model_config=load_traffic_model(),
        activation_mode="all",
    )

    assert decision.activate_legacy is True
    assert decision.activation_status == "activated_override_all"
