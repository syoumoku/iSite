from __future__ import annotations

from typing import Any


YEAR_LIKE_ANNUAL_VISIT_MIN = 1900
YEAR_LIKE_ANNUAL_VISIT_MAX = 2100


def float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_year_like_annual_visit(value: Any) -> bool:
    numeric_value = float_or_none(value)
    if numeric_value is None:
        return False
    if not numeric_value.is_integer():
        return False
    return YEAR_LIKE_ANNUAL_VISIT_MIN <= numeric_value <= YEAR_LIKE_ANNUAL_VISIT_MAX


def safe_annual_visit_estimate(value: Any) -> float | None:
    numeric_value = float_or_none(value)
    if numeric_value is None:
        return None
    if is_year_like_annual_visit(numeric_value):
        return None
    return numeric_value


def scrub_year_like_annual_visit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove year-like annual visit values from API-facing JSON payloads.

    This is a final display/publication guard. Source evidence and localized primary
    metric text are left intact for audit; only derived annual-visit and demand values
    that would otherwise expose a reporting year as traffic are blanked.
    """
    scene = payload.get("scene")
    if not isinstance(scene, dict):
        return payload
    original = scene.get("annual_visits_est")
    if not is_year_like_annual_visit(original):
        safe_value = safe_annual_visit_estimate(original)
        scene["annual_visits_est"] = safe_value
        return payload

    scene["annual_visits_est"] = None
    if is_year_like_annual_visit(scene.get("annual_visits_raw")):
        scene["annual_visits_raw"] = None
    demand = payload.get("demand")
    if isinstance(demand, dict):
        for key in [
            "daily_visits",
            "busy_hour_users",
            "busy_hour_traffic_gb",
            "busy_hour_bandwidth_mbps",
        ]:
            if key in demand:
                demand[key] = None
        demand["cannot_calculate_reason"] = (
            "annual_visits_est blocked by year-like QA guard"
        )
    return payload


def scrub_year_like_annual_visit_feature(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return feature
    original = properties.get("annual_visits_est")
    if not is_year_like_annual_visit(original):
        properties["annual_visits_est"] = safe_annual_visit_estimate(original)
        return feature
    properties["annual_visits_est"] = None
    if "busy_hour_traffic_gb" in properties:
        properties["busy_hour_traffic_gb"] = None
    return feature
