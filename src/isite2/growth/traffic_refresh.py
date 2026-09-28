from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from isite2.db.models import (
    DemandEstimateDB,
    SceneModelResultDB,
    TrafficEstimateV2DB,
)
from isite2.db.session import create_session_factory
from isite2.growth.derived_refresh import (
    EvidenceMetric,
    _best_visit_metric,
    _evidence_pool,
    _latest_targets,
    _metric_candidates,
)
from isite2.rules.traffic import (
    TrafficEstimateResult,
    TrafficMetricInput,
    estimate_traffic_v2,
)
from isite2.rules.config_loader import load_traffic_model
from isite2.rules.traffic_calibration import decide_traffic_activation


def refresh_traffic_estimates_v2(
    engine: Engine,
    *,
    scan_run_id: str | None = None,
    country: str | None = None,
    property_ids: list[str] | None = None,
    force: bool = False,
    activation_mode: str | None = None,
) -> dict[str, Any]:
    """Refresh V2 estimates and activate only results allowed by the rollout gate."""
    with engine.begin() as connection:
        targets = _latest_targets(connection, scan_run_id=scan_run_id)
        if country is not None:
            normalized_country = country.strip().casefold()
            targets = [
                target
                for target in targets
                if str(target["country"]).strip().casefold() == normalized_country
            ]
        if property_ids is not None:
            allowed = set(property_ids)
            targets = [target for target in targets if target["property_id"] in allowed]
        evidence = _evidence_pool(
            connection,
            [target["property_id"] for target in targets],
        )

    session_factory = create_session_factory(engine)
    counts: Counter[str] = Counter()
    qa_errors: list[dict[str, str]] = []
    method_counts: Counter[str] = Counter()
    activation_counts: Counter[str] = Counter()
    model_config = load_traffic_model()
    for target in targets:
        evidence_rows = evidence.get(target["property_id"], [])
        metrics = _traffic_metrics(target["scene_type"], evidence_rows)
        result = estimate_traffic_v2(
            target["scene_type"],
            metrics,
            model_config=model_config,
        )
        decision = decide_traffic_activation(
            country=str(target["country"]),
            scene_type=str(target["scene_type"]),
            estimate_method=result.estimate_method,
            model_config=model_config,
            activation_mode=activation_mode,
        )
        result = replace(
            result,
            activation_status=decision.activation_status,
            activation_reason=decision.activation_reason,
        )
        try:
            _validate_result(result)
        except ValueError as exc:
            qa_errors.append(
                {
                    "property_id": target["property_id"],
                    "property_name": target["canonical_name"],
                    "error": str(exc),
                }
            )
            counts["qa_rejected"] += 1
            continue

        with session_factory.begin() as session:
            prior = session.scalar(
                select(TrafficEstimateV2DB)
                .where(
                    TrafficEstimateV2DB.property_id == target["property_id"],
                    TrafficEstimateV2DB.scan_run_id == target["scan_run_id"],
                    TrafficEstimateV2DB.model_version == result.model_version,
                )
                .order_by(TrafficEstimateV2DB.calculated_at.desc())
            )
            existing = session.scalar(
                select(TrafficEstimateV2DB)
                .where(
                    TrafficEstimateV2DB.property_id == target["property_id"],
                    TrafficEstimateV2DB.scan_run_id == target["scan_run_id"],
                    TrafficEstimateV2DB.model_version == result.model_version,
                    TrafficEstimateV2DB.input_hash == result.input_hash,
                )
                .order_by(TrafficEstimateV2DB.calculated_at.desc())
            )
            if existing is not None and not force:
                was_activated = _is_activated(existing.activation_status)
                existing.activation_status = result.activation_status
                existing.activation_reason = result.activation_reason
                counts["skipped_unchanged"] += 1
                if decision.activate_legacy:
                    _sync_legacy_rows(session, target, result)
                    counts["activated_legacy"] += 1
                else:
                    if was_activated:
                        _restore_v1_rows(session, target, existing)
                        counts["restored_v1"] += 1
                    counts["shadow_only"] += 1
                method_counts[existing.estimate_method] += 1
                activation_counts[result.activation_status] += 1
                continue
            baseline = _baseline_snapshot(session, target, prior)
            if force:
                session.execute(
                    delete(TrafficEstimateV2DB).where(
                        TrafficEstimateV2DB.property_id == target["property_id"],
                        TrafficEstimateV2DB.scan_run_id == target["scan_run_id"],
                        TrafficEstimateV2DB.model_version == result.model_version,
                    )
                )
            session.add(_traffic_row(target, result, baseline))
            if decision.activate_legacy:
                _sync_legacy_rows(session, target, result)
                counts["activated_legacy"] += 1
            else:
                if prior is not None and _is_activated(prior.activation_status):
                    _restore_v1_rows(session, target, prior)
                    counts["restored_v1"] += 1
                counts["shadow_only"] += 1
            counts["refreshed"] += 1
            method_counts[result.estimate_method] += 1
            activation_counts[result.activation_status] += 1

    return {
        "mode": "traffic_estimates_v2_refresh",
        "model_version": "traffic.v2.0",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "scan_run_id": scan_run_id,
        "country": country,
        "activation_mode": (
            activation_mode
            or dict(model_config.get("rollout") or {}).get("default_activation_mode")
            or "direct_only"
        ),
        "target_count": len(targets),
        "counts": dict(counts),
        "method_counts": dict(method_counts),
        "activation_counts": dict(activation_counts),
        "qa_error_count": len(qa_errors),
        "qa_errors": qa_errors[:25],
    }


def rollback_traffic_estimates_v2(
    engine: Engine,
    *,
    scan_run_id: str | None = None,
    country: str | None = None,
    property_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Restore the V1 scene and demand snapshots captured before V2 activation."""
    with engine.begin() as connection:
        targets = _latest_targets(connection, scan_run_id=scan_run_id)
    if country is not None:
        normalized_country = country.strip().casefold()
        targets = [
            target
            for target in targets
            if str(target["country"]).strip().casefold() == normalized_country
        ]
    if property_ids is not None:
        allowed = set(property_ids)
        targets = [target for target in targets if target["property_id"] in allowed]

    session_factory = create_session_factory(engine)
    counts: Counter[str] = Counter()
    for target in targets:
        with session_factory.begin() as session:
            row = session.scalar(
                select(TrafficEstimateV2DB)
                .where(
                    TrafficEstimateV2DB.property_id == target["property_id"],
                    TrafficEstimateV2DB.scan_run_id == target["scan_run_id"],
                )
                .order_by(TrafficEstimateV2DB.calculated_at.desc())
            )
            if row is None:
                counts["no_v2_snapshot"] += 1
                continue
            if not _has_baseline(row):
                counts["missing_v1_snapshot"] += 1
                continue
            _restore_v1_rows(session, target, row)
            rows = session.scalars(
                select(TrafficEstimateV2DB).where(
                    TrafficEstimateV2DB.property_id == target["property_id"],
                    TrafficEstimateV2DB.scan_run_id == target["scan_run_id"],
                )
            ).all()
            for estimate in rows:
                estimate.activation_status = "rolled_back_v1"
                estimate.activation_reason = (
                    "legacy scene and demand fields restored from the pre-V2 snapshot"
                )
            counts["restored_v1"] += 1
    return {
        "mode": "traffic_estimates_v2_rollback",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "scan_run_id": scan_run_id,
        "country": country,
        "target_count": len(targets),
        "counts": dict(counts),
    }


def _baseline_snapshot(
    session,
    target: dict[str, Any],
    prior: TrafficEstimateV2DB | None,
) -> dict[str, Any]:
    if prior is not None and _has_baseline(prior):
        return {
            "annual_visits_est": prior.v1_annual_visits_est,
            "annual_visits_raw": prior.v1_annual_visits_raw,
            "state": _json(prior.v1_demand_snapshot, {}),
        }
    scene = session.scalar(
        select(SceneModelResultDB).where(
            SceneModelResultDB.property_id == target["property_id"],
            SceneModelResultDB.scan_run_id == target["scan_run_id"],
        )
    )
    demand = session.scalar(
        select(DemandEstimateDB).where(
            DemandEstimateDB.property_id == target["property_id"],
            DemandEstimateDB.scan_run_id == target["scan_run_id"],
        )
    )
    demand_payload = None
    if demand is not None:
        demand_payload = {
            "daily_visits": demand.daily_visits,
            "attach_rate": demand.attach_rate,
            "indoor_capture": demand.indoor_capture,
            "busy_hour_factor": demand.busy_hour_factor,
            "gb_per_user_busy_hour": demand.gb_per_user_busy_hour,
            "busy_hour_users": demand.busy_hour_users,
            "busy_hour_traffic_gb": demand.busy_hour_traffic_gb,
            "busy_hour_bandwidth_mbps": demand.busy_hour_bandwidth_mbps,
            "cannot_calculate_reason": demand.cannot_calculate_reason,
        }
    return {
        "annual_visits_est": scene.annual_visits_est if scene is not None else None,
        "annual_visits_raw": scene.annual_visits_raw if scene is not None else None,
        "state": {
            "captured": True,
            "scene_exists": scene is not None,
            "scene_assumption_note": scene.assumption_note if scene is not None else None,
            "demand_exists": demand is not None,
            "demand": demand_payload,
        },
    }


def _has_baseline(row: TrafficEstimateV2DB) -> bool:
    snapshot = _json(row.v1_demand_snapshot, {})
    return bool(isinstance(snapshot, dict) and snapshot.get("captured") is True)


def _is_activated(status: str | None) -> bool:
    return str(status or "").startswith("activated")


def _traffic_metrics(
    scene_type: str,
    evidence_rows: list[dict[str, Any]],
) -> list[TrafficMetricInput]:
    candidates = list(_metric_candidates(scene_type, evidence_rows))
    visit_metric = _best_visit_metric(scene_type, evidence_rows)
    if visit_metric is not None:
        candidates.insert(0, visit_metric)
    output: list[TrafficMetricInput] = []
    seen: set[tuple[str, float, str, str]] = set()
    for metric in candidates:
        if metric.numeric_value is None or metric.numeric_unit is None:
            continue
        evidence_id = _evidence_id(metric, evidence_rows)
        key = (
            metric.field_key,
            float(metric.numeric_value),
            metric.numeric_unit,
            evidence_id or "",
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(
            TrafficMetricInput(
                key=metric.field_key,
                value=float(metric.numeric_value),
                unit=metric.numeric_unit,
                evidence_id=evidence_id,
                field_value=metric.field_value,
            )
        )
    return output


def _evidence_id(
    metric: EvidenceMetric,
    evidence_rows: list[dict[str, Any]],
) -> str | None:
    for row in evidence_rows:
        if (
            str(row.get("source_url") or "") == metric.source_url
            and str(row.get("field_value") or "").strip() == metric.field_value.strip()
        ):
            value = row.get("evidence_id")
            return str(value) if value else None
    return None


def _traffic_row(
    target: dict[str, Any],
    result: TrafficEstimateResult,
    baseline: dict[str, Any],
) -> TrafficEstimateV2DB:
    payload = result.as_dict()
    return TrafficEstimateV2DB(
        id=str(uuid4()),
        property_id=target["property_id"],
        scan_run_id=target["scan_run_id"],
        v1_annual_visits_est=baseline.get("annual_visits_est"),
        v1_annual_visits_raw=baseline.get("annual_visits_raw"),
        v1_demand_snapshot=baseline.get("state") or {"captured": True},
        **payload,
        calculated_at=datetime.now(UTC),
    )


def _sync_legacy_rows(
    session,
    target: dict[str, Any],
    result: TrafficEstimateResult,
) -> None:
    scene = session.scalar(
        select(SceneModelResultDB).where(
            SceneModelResultDB.property_id == target["property_id"],
            SceneModelResultDB.scan_run_id == target["scan_run_id"],
        )
    )
    if scene is not None:
        scene.annual_visits_est = result.annual_visits_p50
        scene.annual_visits_raw = (
            result.annual_visits_p50
            if result.estimate_method == "direct_annual"
            else None
        )
        method_label = f"traffic_v2:{result.estimate_method}"
        existing_note = str(scene.assumption_note or "")
        notes = [
            note
            for note in existing_note.split("; ")
            if note and not note.startswith("traffic_v2:")
        ]
        scene.assumption_note = "; ".join([*notes, method_label])

    demand = session.scalar(
        select(DemandEstimateDB).where(
            DemandEstimateDB.property_id == target["property_id"],
            DemandEstimateDB.scan_run_id == target["scan_run_id"],
        )
    )
    if demand is None:
        demand = DemandEstimateDB(
            id=str(uuid4()),
            property_id=target["property_id"],
            scan_run_id=target["scan_run_id"],
        )
        session.add(demand)
    demand.daily_visits = result.typical_day_visits_p50
    demand.busy_hour_users = result.busy_hour_users_p50
    demand.busy_hour_traffic_gb = result.busy_hour_traffic_gb_p50
    demand.busy_hour_bandwidth_mbps = result.busy_hour_bandwidth_mbps_p50
    demand.cannot_calculate_reason = result.cannot_calculate_reason
    parameters = result.parameter_snapshot
    demand.attach_rate = _middle(parameters.get("attach_rate"))
    demand.indoor_capture = _middle(parameters.get("indoor_capture"))
    demand.busy_hour_factor = _middle(parameters.get("busy_hour_factor"))
    demand.gb_per_user_busy_hour = _middle(parameters.get("gb_per_user_busy_hour"))


def _restore_v1_rows(
    session,
    target: dict[str, Any],
    row: TrafficEstimateV2DB,
) -> None:
    snapshot = _json(row.v1_demand_snapshot, {})
    scene = session.scalar(
        select(SceneModelResultDB).where(
            SceneModelResultDB.property_id == target["property_id"],
            SceneModelResultDB.scan_run_id == target["scan_run_id"],
        )
    )
    if scene is not None:
        scene.annual_visits_est = row.v1_annual_visits_est
        scene.annual_visits_raw = row.v1_annual_visits_raw
        scene.assumption_note = snapshot.get("scene_assumption_note")

    demand = session.scalar(
        select(DemandEstimateDB).where(
            DemandEstimateDB.property_id == target["property_id"],
            DemandEstimateDB.scan_run_id == target["scan_run_id"],
        )
    )
    if not snapshot.get("demand_exists"):
        if demand is not None:
            session.delete(demand)
        return
    demand_payload = snapshot.get("demand")
    if not isinstance(demand_payload, dict):
        return
    if demand is None:
        demand = DemandEstimateDB(
            id=str(uuid4()),
            property_id=target["property_id"],
            scan_run_id=target["scan_run_id"],
        )
        session.add(demand)
    for field_name in (
        "daily_visits",
        "attach_rate",
        "indoor_capture",
        "busy_hour_factor",
        "gb_per_user_busy_hour",
        "busy_hour_users",
        "busy_hour_traffic_gb",
        "busy_hour_bandwidth_mbps",
        "cannot_calculate_reason",
    ):
        setattr(demand, field_name, demand_payload.get(field_name))


def _middle(value: Any) -> float | None:
    if not isinstance(value, list) or not value:
        return None
    return float(value[min(1, len(value) - 1)])


def _validate_result(result: TrafficEstimateResult) -> None:
    for label in (
        "annual_visits",
        "typical_day_visits",
        "peak_day_visits",
        "busy_hour_users",
        "busy_hour_traffic_gb",
        "busy_hour_bandwidth_mbps",
    ):
        values = [
            getattr(result, f"{label}_p10"),
            getattr(result, f"{label}_p50"),
            getattr(result, f"{label}_p90"),
        ]
        present = [float(value) for value in values if value is not None]
        if any(value <= 0 for value in present):
            raise ValueError(f"{label} contains a non-positive value")
        if len(present) == 3 and present != sorted(present):
            raise ValueError(f"{label} does not satisfy P10 <= P50 <= P90")
    annual = result.annual_visits_p50
    if annual is not None and float(annual).is_integer() and 1900 <= annual <= 2100:
        raise ValueError("annual visits contains a year-like value")


def _result_from_row(row: TrafficEstimateV2DB) -> TrafficEstimateResult:
    return TrafficEstimateResult(
        model_version=row.model_version,
        estimate_method=row.estimate_method,
        input_hash=row.input_hash,
        selected_metric_key=row.selected_metric_key,
        selected_metric_value=row.selected_metric_value,
        selected_metric_unit=row.selected_metric_unit,
        selected_evidence_ids=_json(row.selected_evidence_ids, []),
        annual_visits_p10=row.annual_visits_p10,
        annual_visits_p50=row.annual_visits_p50,
        annual_visits_p90=row.annual_visits_p90,
        typical_day_visits_p10=row.typical_day_visits_p10,
        typical_day_visits_p50=row.typical_day_visits_p50,
        typical_day_visits_p90=row.typical_day_visits_p90,
        peak_day_visits_p10=row.peak_day_visits_p10,
        peak_day_visits_p50=row.peak_day_visits_p50,
        peak_day_visits_p90=row.peak_day_visits_p90,
        busy_hour_users_p10=row.busy_hour_users_p10,
        busy_hour_users_p50=row.busy_hour_users_p50,
        busy_hour_users_p90=row.busy_hour_users_p90,
        busy_hour_traffic_gb_p10=row.busy_hour_traffic_gb_p10,
        busy_hour_traffic_gb_p50=row.busy_hour_traffic_gb_p50,
        busy_hour_traffic_gb_p90=row.busy_hour_traffic_gb_p90,
        busy_hour_bandwidth_mbps_p10=row.busy_hour_bandwidth_mbps_p10,
        busy_hour_bandwidth_mbps_p50=row.busy_hour_bandwidth_mbps_p50,
        busy_hour_bandwidth_mbps_p90=row.busy_hour_bandwidth_mbps_p90,
        confidence=row.confidence,
        activation_status=row.activation_status,
        activation_reason=row.activation_reason,
        parameter_snapshot=_json(row.parameter_snapshot, {}),
        qa_flags=_json(row.qa_flags, []),
        cannot_calculate_reason=row.cannot_calculate_reason,
    )


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value
