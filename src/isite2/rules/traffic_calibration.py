from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any


@dataclass(frozen=True)
class TrafficBenchmarkSample:
    scene_type: str
    actual_annual_visits: float
    v1_annual_visits: float
    v2_annual_visits_p50: float
    property_id: str | None = None


@dataclass(frozen=True)
class SceneCalibrationGate:
    scene_type: str
    sample_count: int
    v1_mape: float | None
    v2_mape: float | None
    relative_improvement: float | None
    median_absolute_percentage_error: float | None
    status: str
    rollout_allowed: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TrafficActivationDecision:
    activation_status: str
    activation_reason: str
    activate_legacy: bool


def decide_traffic_activation(
    *,
    country: str,
    scene_type: str,
    estimate_method: str,
    model_config: dict[str, Any],
    activation_mode: str | None = None,
) -> TrafficActivationDecision:
    """Decide whether a V2 result may replace the legacy P50 fields."""
    rollout = dict(model_config.get("rollout") or {})
    mode = str(
        activation_mode
        or rollout.get("default_activation_mode")
        or "direct_only"
    ).strip().lower()
    if mode not in {"direct_only", "shadow", "all"}:
        raise ValueError(
            "traffic V2 activation mode must be one of: direct_only, shadow, all"
        )
    if estimate_method == "insufficient":
        return TrafficActivationDecision(
            activation_status="shadow_insufficient",
            activation_reason="V2 could not calculate a supported traffic estimate",
            activate_legacy=False,
        )
    if mode == "shadow":
        return TrafficActivationDecision(
            activation_status="shadow_forced",
            activation_reason="V2 activation mode is shadow",
            activate_legacy=False,
        )
    if estimate_method in {"direct_annual", "direct_daily"}:
        return TrafficActivationDecision(
            activation_status="activated_direct",
            activation_reason="direct visit evidence may replace the legacy estimate",
            activate_legacy=True,
        )
    if mode == "all":
        return TrafficActivationDecision(
            activation_status="activated_override_all",
            activation_reason="V2 activation mode explicitly enables all supported estimates",
            activate_legacy=True,
        )

    approved_scenes = _approved_proxy_scenes(rollout, country)
    if scene_type in approved_scenes:
        return TrafficActivationDecision(
            activation_status="activated_approved_proxy",
            activation_reason=(
                f"{scene_type} proxy rollout is explicitly approved for {country}"
            ),
            activate_legacy=True,
        )
    return TrafficActivationDecision(
        activation_status="shadow_unapproved_proxy",
        activation_reason=(
            f"{scene_type} proxy rollout has not passed the activation gate for {country}"
        ),
        activate_legacy=False,
    )


def _approved_proxy_scenes(
    rollout: dict[str, Any],
    country: str,
) -> set[str]:
    configured = dict(rollout.get("approved_proxy_scenes") or {})
    normalized_country = country.strip().casefold()
    approved: set[str] = set()
    for key, values in configured.items():
        if str(key).strip().casefold() not in {"*", normalized_country}:
            continue
        if isinstance(values, list):
            approved.update(str(value).strip() for value in values if str(value).strip())
    return approved


def evaluate_traffic_rollout(
    samples: list[TrafficBenchmarkSample],
    *,
    minimum_sample_count: int = 30,
    required_relative_improvement: float = 0.20,
) -> list[SceneCalibrationGate]:
    by_scene: dict[str, list[TrafficBenchmarkSample]] = defaultdict(list)
    for sample in samples:
        if (
            sample.actual_annual_visits > 0
            and sample.v1_annual_visits > 0
            and sample.v2_annual_visits_p50 > 0
        ):
            by_scene[sample.scene_type].append(sample)

    output = []
    for scene_type, scene_samples in sorted(by_scene.items()):
        v1_errors = [
            abs(sample.v1_annual_visits - sample.actual_annual_visits)
            / sample.actual_annual_visits
            for sample in scene_samples
        ]
        v2_errors = [
            abs(sample.v2_annual_visits_p50 - sample.actual_annual_visits)
            / sample.actual_annual_visits
            for sample in scene_samples
        ]
        v1_mape = sum(v1_errors) / len(v1_errors)
        v2_mape = sum(v2_errors) / len(v2_errors)
        improvement = (v1_mape - v2_mape) / v1_mape if v1_mape > 0 else None
        if len(scene_samples) < minimum_sample_count:
            status = "uncalibrated_scene"
            allowed = False
        elif improvement is not None and improvement >= required_relative_improvement:
            status = "passed"
            allowed = True
        else:
            status = "failed_improvement_gate"
            allowed = False
        output.append(
            SceneCalibrationGate(
                scene_type=scene_type,
                sample_count=len(scene_samples),
                v1_mape=v1_mape,
                v2_mape=v2_mape,
                relative_improvement=improvement,
                median_absolute_percentage_error=median(v2_errors),
                status=status,
                rollout_allowed=allowed,
            )
        )
    return output
