from isite2.rules.demand import DemandParams, calculate_demand


def test_calculate_demand_formula_chain() -> None:
    result = calculate_demand(
        3_650_000,
        DemandParams(
            attach_rate=0.8,
            indoor_capture=0.9,
            busy_hour_factor=0.1,
            gb_per_user_busy_hour=0.5,
        ),
    )
    assert result.daily_visits == 10_000
    assert result.busy_hour_users == 720
    assert result.busy_hour_traffic_gb == 360
    assert round(result.busy_hour_bandwidth_mbps, 2) == 819.20


def test_calculate_demand_missing_annual_visits() -> None:
    result = calculate_demand(None, DemandParams(0.8, 0.9, 0.1, 0.5))
    assert result.cannot_calculate_reason
    assert result.busy_hour_traffic_gb is None
