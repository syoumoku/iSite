from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from isite2.rules.config_loader import get_scene_rule, load_traffic_model

ANNUAL_VISIT_KEYS = {
    "annual_passenger_throughput",
    "passenger_throughput",
    "annual_footfall",
    "footfall",
    "annual_visitors",
    "annual_visits",
    "annual_station_entries_exits",
    "outpatient_volume",
    "interchange_volume",
}
DAILY_VISIT_KEYS = {
    "daily_ridership",
    "ridership",
    "daily_visitors",
    "outpatient_volume",
    "interchange_volume",
}
DIRECT_VISIT_KEYS = ANNUAL_VISIT_KEYS | DAILY_VISIT_KEYS


@dataclass(frozen=True)
class TrafficMetricInput:
    key: str
    value: float
    unit: str
    evidence_id: str | None = None
    field_value: str | None = None


@dataclass(frozen=True)
class _TrafficComponent:
    name: str
    metric: TrafficMetricInput
    config: dict[str, Any]


@dataclass(frozen=True)
class TrafficEstimateResult:
    model_version: str
    estimate_method: str
    input_hash: str
    selected_metric_key: str | None
    selected_metric_value: float | None
    selected_metric_unit: str | None
    selected_evidence_ids: list[str]
    annual_visits_p10: float | None
    annual_visits_p50: float | None
    annual_visits_p90: float | None
    typical_day_visits_p10: float | None
    typical_day_visits_p50: float | None
    typical_day_visits_p90: float | None
    peak_day_visits_p10: float | None
    peak_day_visits_p50: float | None
    peak_day_visits_p90: float | None
    busy_hour_users_p10: float | None
    busy_hour_users_p50: float | None
    busy_hour_users_p90: float | None
    busy_hour_traffic_gb_p10: float | None
    busy_hour_traffic_gb_p50: float | None
    busy_hour_traffic_gb_p90: float | None
    busy_hour_bandwidth_mbps_p10: float | None
    busy_hour_bandwidth_mbps_p50: float | None
    busy_hour_bandwidth_mbps_p90: float | None
    confidence: str
    activation_status: str = "not_evaluated"
    activation_reason: str | None = None
    parameter_snapshot: dict[str, Any] = field(default_factory=dict)
    qa_flags: list[str] = field(default_factory=list)
    cannot_calculate_reason: str | None = None

    @property
    def legacy_annual_visits_est(self) -> float | None:
        return self.annual_visits_p50

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_traffic_v2(
    scene_type: str,
    metrics: list[TrafficMetricInput],
    *,
    model_config: dict[str, Any] | None = None,
) -> TrafficEstimateResult:
    config = model_config or load_traffic_model()
    version = str(config.get("version") or "traffic.v2")
    scene_config = dict(config.get("scenes", {}).get(scene_type) or {})
    input_hash = traffic_input_hash(scene_type, metrics, version)
    if not scene_config:
        return _empty_result(
            version,
            input_hash,
            f"traffic model missing for scene_type={scene_type}",
        )

    positive_non_year_metrics = [
        metric
        for metric in metrics
        if _positive(metric.value) and metric.unit and not _looks_like_year(metric.value)
    ]
    valid_metrics = [
        metric
        for metric in positive_non_year_metrics
        if _metric_within_bounds(metric, config)
    ]
    filtered_metric_keys = sorted(
        {
            metric.key
            for metric in positive_non_year_metrics
            if metric not in valid_metrics
        }
    )
    annual_metric = _first_metric(
        valid_metrics,
        lambda item: item.unit == "visits/year" and item.key in DIRECT_VISIT_KEYS,
    )
    daily_metric = _first_metric(
        valid_metrics,
        lambda item: item.unit == "visits/day" and item.key in DIRECT_VISIT_KEYS,
    )
    components = _traffic_components(valid_metrics, scene_config)
    proxy_metric, proxy_config = _proxy_metric(valid_metrics, scene_config)
    direct_visit_covers_total = bool(
        scene_config.get("direct_visit_covers_total", True)
    )

    method = "insufficient"
    metric: TrafficMetricInput | None = None
    annual_range: tuple[float | None, float | None, float | None] = (None, None, None)
    qa_flags: list[str] = (
        ["implausible_metric_filtered:" + ",".join(filtered_metric_keys)]
        if filtered_metric_keys
        else []
    )
    selected_components: list[_TrafficComponent] = []
    if annual_metric is not None and direct_visit_covers_total:
        method = "direct_annual"
        metric = annual_metric
        annual_range = (None, annual_metric.value, None)
    elif daily_metric is not None and direct_visit_covers_total:
        method = "direct_daily"
        metric = daily_metric
        active_days = _triple(scene_config.get("active_days"), default=(365, 365, 365))
        annual_range = tuple(daily_metric.value * days for days in active_days)
    elif components:
        method = "scene_components"
        selected_components = components
        metric = components[0].metric
        active_days = _triple(
            scene_config.get("active_days"),
            default=(365, 365, 365),
        )
        annual_range = _sum_triples(
            [
                _component_annual_range(component, active_days)
                for component in components
            ]
        )
        if not bool(scene_config.get("calibrated")):
            qa_flags.append("uncalibrated_scene")
    elif proxy_metric is not None and proxy_config is not None:
        method = "scene_proxy"
        metric = proxy_metric
        multipliers = _triple(proxy_config.get("annual_multiplier"), default=(1, 1, 1))
        annual_range = tuple(proxy_metric.value * multiplier for multiplier in multipliers)
        if not bool(scene_config.get("calibrated")):
            qa_flags.append("uncalibrated_scene")

    if metric is None or annual_range[1] is None:
        return _empty_result(
            version,
            input_hash,
            "no direct visit metric or supported scene proxy",
            qa_flags=qa_flags,
        )

    active_days = _triple(scene_config.get("active_days"), default=(365, 365, 365))
    typical_day_denominators = (
        (365.0, 365.0, 365.0)
        if bool(scene_config.get("event_driven"))
        else active_days
    )
    if method == "direct_daily":
        typical_range = (metric.value, metric.value, metric.value)
    else:
        annual_low = annual_range[0] if annual_range[0] is not None else annual_range[1]
        annual_high = annual_range[2] if annual_range[2] is not None else annual_range[1]
        typical_range = (
            float(annual_low) / typical_day_denominators[2],
            float(annual_range[1]) / typical_day_denominators[1],
            float(annual_high) / typical_day_denominators[0],
        )

    peak_multiplier = (
        _triple(proxy_config.get("peak_multiplier"))
        if proxy_metric is not None
        and proxy_config is not None
        and proxy_config.get("peak_multiplier") is not None
        else None
    )
    if selected_components:
        peak_range = _sum_triples(
            [
                _component_peak_range(
                    component,
                    active_days=active_days,
                    event_driven=bool(scene_config.get("event_driven")),
                    scene_peak_factors=_triple(
                        scene_config.get("peak_day_factor"),
                        default=(1.0, 1.0, 1.0),
                    ),
                )
                for component in selected_components
            ]
        )
    elif peak_multiplier is not None:
        peak_range = tuple(
            proxy_metric.value * multiplier for multiplier in peak_multiplier
        )
    else:
        peak_factors = _triple(
            scene_config.get("peak_day_factor"),
            default=(1.0, 1.0, 1.0),
        )
        peak_range = tuple(
            typical_range[index] * peak_factors[index] for index in range(3)
        )

    demand = get_scene_rule(scene_type).get("demand_parameters", {})
    attach = _triple(demand.get("attach_rate"), midpoint=True, default=(0.7, 0.7, 0.7))
    indoor = _triple(
        demand.get("indoor_capture"),
        midpoint=True,
        default=(0.8, 0.8, 0.8),
    )
    busy = _triple(
        demand.get("busy_hour_factor"),
        midpoint=True,
        default=(0.1, 0.1, 0.1),
    )
    gb_per_user = _triple(
        demand.get("gb_per_user_busy_hour"),
        midpoint=True,
        default=(0.4, 0.4, 0.4),
    )
    busy_users = tuple(
        peak_range[index] * attach[index] * indoor[index] * busy[index]
        for index in range(3)
    )
    busy_traffic = tuple(
        busy_users[index] * gb_per_user[index] for index in range(3)
    )
    busy_seconds = max(1, int(config.get("busy_hour_seconds") or 3600))
    bandwidth = tuple(value * 8192 / busy_seconds for value in busy_traffic)

    parameter_snapshot = {
        "active_days": list(active_days),
        "typical_day_basis": (
            "calendar_day" if bool(scene_config.get("event_driven")) else "active_day"
        ),
        "peak_day_factor": list(
            _triple(scene_config.get("peak_day_factor"), default=(1.0, 1.0, 1.0))
        ),
        "attach_rate": list(attach),
        "indoor_capture": list(indoor),
        "busy_hour_factor": list(busy),
        "gb_per_user_busy_hour": list(gb_per_user),
        "proxy": proxy_config if proxy_config is not None else None,
        "components": [
            {
                "name": component.name,
                "metric_key": component.metric.key,
                "metric_value": component.metric.value,
                "metric_unit": component.metric.unit,
                "config": component.config,
            }
            for component in selected_components
        ],
        "peak_metric_key": proxy_metric.key if peak_multiplier is not None else None,
    }
    confidence = (
        "high"
        if method == "direct_annual"
        else "medium"
        if method == "direct_daily" or bool(scene_config.get("calibrated"))
        else "low"
    )
    selected_metric_key = (
        "+".join(component.metric.key for component in selected_components)
        if selected_components
        else metric.key
    )
    selected_evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for evidence_id in (
                *(
                    component.metric.evidence_id
                    for component in selected_components
                ),
                metric.evidence_id if not selected_components else None,
                proxy_metric.evidence_id
                if peak_multiplier is not None and not selected_components
                else None,
            )
            if evidence_id
        )
    )
    return TrafficEstimateResult(
        model_version=version,
        estimate_method=method,
        input_hash=input_hash,
        selected_metric_key=selected_metric_key,
        selected_metric_value=(
            float(metric.value) if len(selected_components) <= 1 else None
        ),
        selected_metric_unit=metric.unit if len(selected_components) <= 1 else None,
        selected_evidence_ids=selected_evidence_ids,
        annual_visits_p10=annual_range[0],
        annual_visits_p50=annual_range[1],
        annual_visits_p90=annual_range[2],
        typical_day_visits_p10=typical_range[0],
        typical_day_visits_p50=typical_range[1],
        typical_day_visits_p90=typical_range[2],
        peak_day_visits_p10=peak_range[0],
        peak_day_visits_p50=peak_range[1],
        peak_day_visits_p90=peak_range[2],
        busy_hour_users_p10=busy_users[0],
        busy_hour_users_p50=busy_users[1],
        busy_hour_users_p90=busy_users[2],
        busy_hour_traffic_gb_p10=busy_traffic[0],
        busy_hour_traffic_gb_p50=busy_traffic[1],
        busy_hour_traffic_gb_p90=busy_traffic[2],
        busy_hour_bandwidth_mbps_p10=bandwidth[0],
        busy_hour_bandwidth_mbps_p50=bandwidth[1],
        busy_hour_bandwidth_mbps_p90=bandwidth[2],
        confidence=confidence,
        parameter_snapshot=parameter_snapshot,
        qa_flags=qa_flags,
    )


def traffic_input_hash(
    scene_type: str,
    metrics: list[TrafficMetricInput],
    model_version: str,
) -> str:
    payload = {
        "scene_type": scene_type,
        "model_version": model_version,
        "metrics": sorted(
            [
                {
                    "key": metric.key,
                    "value": metric.value,
                    "unit": metric.unit,
                    "evidence_id": metric.evidence_id,
                    "field_value": metric.field_value,
                }
                for metric in metrics
            ],
            key=lambda item: (
                str(item["key"]),
                str(item["unit"]),
                float(item["value"]),
                str(item["evidence_id"] or ""),
            ),
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _empty_result(
    version: str,
    input_hash: str,
    reason: str,
    *,
    qa_flags: list[str] | None = None,
) -> TrafficEstimateResult:
    return TrafficEstimateResult(
        model_version=version,
        estimate_method="insufficient",
        input_hash=input_hash,
        selected_metric_key=None,
        selected_metric_value=None,
        selected_metric_unit=None,
        selected_evidence_ids=[],
        annual_visits_p10=None,
        annual_visits_p50=None,
        annual_visits_p90=None,
        typical_day_visits_p10=None,
        typical_day_visits_p50=None,
        typical_day_visits_p90=None,
        peak_day_visits_p10=None,
        peak_day_visits_p50=None,
        peak_day_visits_p90=None,
        busy_hour_users_p10=None,
        busy_hour_users_p50=None,
        busy_hour_users_p90=None,
        busy_hour_traffic_gb_p10=None,
        busy_hour_traffic_gb_p50=None,
        busy_hour_traffic_gb_p90=None,
        busy_hour_bandwidth_mbps_p10=None,
        busy_hour_bandwidth_mbps_p50=None,
        busy_hour_bandwidth_mbps_p90=None,
        confidence="insufficient",
        qa_flags=list(qa_flags or []),
        cannot_calculate_reason=reason,
    )


def _first_metric(
    metrics: list[TrafficMetricInput],
    predicate,
) -> TrafficMetricInput | None:
    candidates = [metric for metric in metrics if predicate(metric)]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (-item.value, item.key))[0]


def _proxy_metric(
    metrics: list[TrafficMetricInput],
    scene_config: dict[str, Any],
) -> tuple[TrafficMetricInput | None, dict[str, Any] | None]:
    proxy_metrics = dict(scene_config.get("proxy_metrics") or {})
    for key, proxy_config in proxy_metrics.items():
        candidate = _first_metric(metrics, lambda item, expected=key: item.key == expected)
        if candidate is not None:
            return candidate, dict(proxy_config or {})
    return None, None


def _metric_within_bounds(
    metric: TrafficMetricInput,
    model_config: dict[str, Any],
) -> bool:
    bounds = (model_config.get("metric_bounds") or {}).get(metric.key)
    if not isinstance(bounds, (list, tuple)) or len(bounds) < 2:
        return True
    minimum, maximum = float(bounds[0]), float(bounds[1])
    return minimum <= float(metric.value) <= maximum


def _traffic_components(
    metrics: list[TrafficMetricInput],
    scene_config: dict[str, Any],
) -> list[_TrafficComponent]:
    groups = dict(scene_config.get("proxy_component_groups") or {})
    proxy_metrics = dict(scene_config.get("proxy_metrics") or {})
    components: list[_TrafficComponent] = []
    for name, keys in groups.items():
        if not isinstance(keys, list):
            continue
        for key in keys:
            candidate = _first_metric(
                metrics,
                lambda item, expected=str(key): item.key == expected,
            )
            if candidate is None:
                continue
            components.append(
                _TrafficComponent(
                    name=str(name),
                    metric=candidate,
                    config=dict(proxy_metrics.get(str(key)) or {}),
                )
            )
            break
    return components


def _component_annual_range(
    component: _TrafficComponent,
    active_days: tuple[float, float, float],
) -> tuple[float, float, float]:
    metric = component.metric
    if metric.unit == "visits/year":
        return metric.value, metric.value, metric.value
    if metric.unit == "visits/day":
        return tuple(metric.value * days for days in active_days)
    multipliers = _triple(
        component.config.get("annual_multiplier"),
        default=(1.0, 1.0, 1.0),
    )
    return tuple(metric.value * multiplier for multiplier in multipliers)


def _component_peak_range(
    component: _TrafficComponent,
    *,
    active_days: tuple[float, float, float],
    event_driven: bool,
    scene_peak_factors: tuple[float, float, float],
) -> tuple[float, float, float]:
    metric = component.metric
    peak_multiplier = component.config.get("peak_multiplier")
    if peak_multiplier is not None:
        multipliers = _triple(peak_multiplier, default=(1.0, 1.0, 1.0))
        return tuple(metric.value * multiplier for multiplier in multipliers)
    if metric.unit == "visits/day":
        typical = (metric.value, metric.value, metric.value)
    else:
        annual = _component_annual_range(component, active_days)
        denominators = (365.0, 365.0, 365.0) if event_driven else active_days
        typical = (
            annual[0] / denominators[2],
            annual[1] / denominators[1],
            annual[2] / denominators[0],
        )
    return tuple(
        typical[index] * scene_peak_factors[index] for index in range(3)
    )


def _sum_triples(
    values: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    return tuple(sum(value[index] for value in values) for index in range(3))


def _triple(
    values: Any,
    *,
    midpoint: bool = False,
    default: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    fallback = default or (1.0, 1.0, 1.0)
    if not isinstance(values, (list, tuple)) or not values:
        return fallback
    numbers = [float(value) for value in values]
    if len(numbers) >= 3:
        return numbers[0], numbers[1], numbers[2]
    if len(numbers) == 2:
        center = (numbers[0] + numbers[1]) / 2
        return (numbers[0], center, numbers[1]) if midpoint else (numbers[0], center, numbers[1])
    return numbers[0], numbers[0], numbers[0]


def _positive(value: float | int | None) -> bool:
    return value is not None and float(value) > 0


def _looks_like_year(value: float | int) -> bool:
    numeric = float(value)
    return numeric.is_integer() and 1900 <= numeric <= 2100
