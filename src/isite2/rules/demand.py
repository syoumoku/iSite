from __future__ import annotations

from dataclasses import dataclass

from isite2.domain.models import DemandEstimate


@dataclass(frozen=True)
class DemandParams:
    attach_rate: float
    indoor_capture: float
    busy_hour_factor: float
    gb_per_user_busy_hour: float


def midpoint(low: float, high: float) -> float:
    return (low + high) / 2


def demand_params_from_scene_rule(scene_rule: dict) -> DemandParams:
    params = scene_rule["demand_parameters"]
    return DemandParams(
        attach_rate=midpoint(*params["attach_rate"]),
        indoor_capture=midpoint(*params["indoor_capture"]),
        busy_hour_factor=midpoint(*params["busy_hour_factor"]),
        gb_per_user_busy_hour=midpoint(*params["gb_per_user_busy_hour"]),
    )


def calculate_demand(annual_visits: float | None, params: DemandParams) -> DemandEstimate:
    """Calculate the iSite demand chain.

    Formula:
    daily_visits = annual_visits / 365
    busy_hour_users = daily_visits × attach_rate × indoor_capture × busy_hour_factor
    busy_hour_traffic_gb = busy_hour_users × gb_per_user_busy_hour
    busy_hour_bandwidth_mbps = busy_hour_traffic_gb × 1024 × 8 / 3600
    """
    if annual_visits is None or annual_visits <= 0:
        return DemandEstimate(cannot_calculate_reason="annual_visits missing or non-positive")

    daily_visits = annual_visits / 365
    busy_hour_users = (
        daily_visits * params.attach_rate * params.indoor_capture * params.busy_hour_factor
    )
    busy_hour_traffic_gb = busy_hour_users * params.gb_per_user_busy_hour
    busy_hour_bandwidth_mbps = busy_hour_traffic_gb * 1024 * 8 / 3600

    return DemandEstimate(
        daily_visits=daily_visits,
        attach_rate=params.attach_rate,
        indoor_capture=params.indoor_capture,
        busy_hour_factor=params.busy_hour_factor,
        gb_per_user_busy_hour=params.gb_per_user_busy_hour,
        busy_hour_users=busy_hour_users,
        busy_hour_traffic_gb=busy_hour_traffic_gb,
        busy_hour_bandwidth_mbps=busy_hour_bandwidth_mbps,
    )
