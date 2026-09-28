from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import SQLAlchemyError

from isite2.localization import (
    DatabaseLocalizationCache,
    enum_label,
    generic_free_text_fallback,
    localization_schema_version,
    localize_packet,
    localize_text,
    resolve_locale,
    scene_label,
    supported_locales,
)
from isite2.property_search import property_search_document
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository
from isite2.rules.coordinates import is_map_ready_coordinate
from isite2.rules.metric_safety import (
    is_year_like_annual_visit,
    safe_annual_visit_estimate,
    scrub_year_like_annual_visit_payload,
)

PUBLIC_API_TABLES = [
    "public_api_property_index",
    "public_api_property_packets",
    "public_api_map_features",
    "public_api_review_queue_rows",
]

PUBLIC_API_SCHEMA_VERSION = "public_api_preaggregation.v3"

REQUIRED_SOURCE_COLUMNS = {
    "properties": {
        "id",
        "canonical_name",
        "country",
        "city",
        "scene_type",
        "latitude",
        "longitude",
        "geocode_precision",
        "coordinate_status",
    },
    "scan_candidates": {
        "scan_run_id",
        "property_id",
        "country",
        "city",
        "scene_type",
        "candidate_quality_status",
        "visibility",
        "quality_issues",
        "created_at",
    },
    "evidence_items": {"property_id", "scan_run_id", "field_value", "source_url"},
    "scene_model_results": {"property_id", "scan_run_id", "annual_visits_est", "proxy_level"},
    "build_statuses": {"property_id", "scan_run_id", "indoor_system_presence", "indoor_rat"},
    "demand_estimates": {"property_id", "scan_run_id", "busy_hour_traffic_gb"},
    "conclusions": {
        "property_id",
        "scan_run_id",
        "evidence_status",
        "value_class",
        "action_class",
        "recommended_solution",
    },
    "review_queue": {"id", "property_id", "scan_run_id", "reason", "next_action", "status"},
    "localized_text_cache": {
        "source_text_hash",
        "text_kind",
        "source_locale",
        "target_locale",
        "schema_version",
        "translated_text",
    },
}


@dataclass
class PublicApiPreaggregation:
    rows: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {table: [] for table in PUBLIC_API_TABLES}
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def table_counts(self) -> dict[str, int]:
        return {table: len(rows) for table, rows in self.rows.items()}


def build_public_api_preaggregation(
    source_url: str,
    *,
    locales: list[str] | None = None,
) -> PublicApiPreaggregation:
    repository = SQLAlchemyScanRunRepository.from_url(
        source_url,
        storage_mode="postgis" if source_url.startswith("postgresql") else "sqlite",
        create_schema=False,
    )
    missing_columns = missing_preaggregation_source_columns(repository)
    if missing_columns:
        return PublicApiPreaggregation(
            metadata={
                "schema_version": PUBLIC_API_SCHEMA_VERSION,
                "status": "skipped_missing_source_columns",
                "missing_source_columns": missing_columns,
                "locales": [],
                "localization_schema_version": localization_schema_version(),
                "localization_cache_miss_count": 0,
                "localization_pending_text_count": 0,
                "complaint_coverage": {
                    "status": "unavailable_missing_source_columns",
                    "property_signal_count": 0,
                    "valid_observation_count": 0,
                },
            }
        )

    resolved_locales = [resolve_locale(locale) for locale in (locales or supported_locales())]
    resolved_locales = list(dict.fromkeys(resolved_locales))
    cache = DatabaseLocalizationCache(repository.engine, ensure_schema=False)
    result = PublicApiPreaggregation()
    result.metadata.update(
        {
            "schema_version": PUBLIC_API_SCHEMA_VERSION,
            "status": "generated",
            "locales": resolved_locales,
            "localization_schema_version": localization_schema_version(),
            "localization_cache_miss_count": 0,
            "localization_pending_text_count": 0,
            "feature_flags": {
                "traffic_v2": False,
                "complaints": False,
                "ookla_public": _public_ookla_enabled(),
            },
            "complaint_coverage": {
                "status": "no_qualified_property_level_data",
                "property_signal_count": 0,
                "valid_observation_count": 0,
            },
        }
    )

    latest_scan_at = _latest_scan_at(repository)
    summary_rows = repository.latest_candidate_summary_rows(include_blocked_quality=True)
    index_by_property = {str(row["property_id"]): row for row in summary_rows}
    sort_by_property = {str(row["property_id"]): index for index, row in enumerate(summary_rows)}
    packets_by_property = {}
    for row in summary_rows:
        property_id = str(row["property_id"])
        packet = repository.get_property_packet_for_run(property_id, str(row["scan_run_id"]))
        if packet is not None:
            packets_by_property[property_id] = packet

    for sort_order, row in enumerate(summary_rows):
        result.rows["public_api_property_index"].append(
            _property_index_row(
                row,
                sort_order,
                packets_by_property.get(str(row["property_id"])),
            )
        )
    result.metadata["feature_flags"]["traffic_v2"] = any(
        packet.traffic_estimate is not None for packet in packets_by_property.values()
    )
    result.metadata["feature_flags"]["complaints"] = any(
        packet.network_signals is not None
        and packet.network_signals.complaints is not None
        for packet in packets_by_property.values()
    )
    complaint_signals = [
        packet.network_signals.complaints
        for packet in packets_by_property.values()
        if packet.network_signals is not None
        and packet.network_signals.complaints is not None
    ]
    result.metadata["complaint_coverage"] = {
        "status": (
            "qualified_property_level_data_available"
            if complaint_signals
            else "no_qualified_property_level_data"
        ),
        "property_signal_count": len(complaint_signals),
        "valid_observation_count": sum(
            signal.valid_complaint_count for signal in complaint_signals
        ),
    }

    for row in summary_rows:
        property_id = str(row["property_id"])
        packet = packets_by_property.get(property_id)
        if packet is None:
            continue
        sort_order = sort_by_property[property_id]
        for locale in resolved_locales:
            packet_payload = jsonable_encoder(packet)
            scrub_year_like_annual_visit_payload(packet_payload)
            _apply_public_network_feature_flags(packet_payload)
            packet_payload["localized"] = localize_packet(
                packet,
                locale=locale,
                cache=cache,
                allow_provider=False,
            )
            result.metadata["localization_cache_miss_count"] += _cache_miss_count(packet_payload)
            result.metadata["localization_pending_text_count"] += _pending_text_count(
                packet_payload
            )
            result.rows["public_api_property_packets"].append(
                _property_packet_row(row, locale, sort_order, packet_payload)
            )

    for row in summary_rows:
        if row.get("longitude") is None or row.get("latitude") is None:
            continue
        if not is_map_ready_coordinate(str(row.get("coordinate_status") or "")):
            continue
        sort_order = sort_by_property[str(row["property_id"])]
        for locale in resolved_locales:
            feature = _map_feature(
                row,
                locale,
                latest_scan_at,
                packets_by_property.get(str(row["property_id"])),
            )
            result.rows["public_api_map_features"].append(
                _map_feature_row(row, locale, sort_order, feature)
            )

    review_rows = repository.list_review_queue_rows({}, status=None)
    for sort_order, review in enumerate(review_rows):
        index_row = index_by_property.get(str(review["property_id"]))
        if index_row is None:
            continue
        for locale in resolved_locales:
            row_json, miss_count = _review_queue_row_json(
                review,
                locale,
                cache,
                city_id=index_row.get("city_id"),
            )
            result.metadata["localization_cache_miss_count"] += miss_count
            result.metadata["localization_pending_text_count"] += _pending_text_count(row_json)
            result.rows["public_api_review_queue_rows"].append(
                _review_queue_table_row(index_row, review, locale, sort_order, row_json)
            )

    return result


def missing_preaggregation_source_columns(
    repository: SQLAlchemyScanRunRepository,
) -> dict[str, list[str]]:
    inspector = sa_inspect(repository.engine)
    missing: dict[str, list[str]] = {}
    try:
        table_names = set(inspector.get_table_names())
        for table, required_columns in REQUIRED_SOURCE_COLUMNS.items():
            if table not in table_names:
                missing[table] = sorted(required_columns)
                continue
            available_columns = {column["name"] for column in inspector.get_columns(table)}
            missing_columns = sorted(required_columns - available_columns)
            if missing_columns:
                missing[table] = missing_columns
    except SQLAlchemyError as exc:
        missing["_introspection"] = [str(exc)]
    return missing


def _property_index_row(
    row: dict[str, Any],
    sort_order: int,
    packet=None,
) -> dict[str, Any]:
    visibility = dict(row.get("visibility") or {})
    annual_visits_est = safe_annual_visit_estimate(row.get("annual_visits_est"))
    busy_hour_traffic_gb = (
        None
        if is_year_like_annual_visit(row.get("annual_visits_est"))
        else row.get("busy_hour_traffic_gb")
    )
    traffic = packet.traffic_estimate if packet is not None else None
    active_traffic = _active_traffic(traffic)
    network = packet.network_signals if packet is not None else None
    complaints = network.complaints if network is not None else None
    ookla_public = _public_ookla_enabled()
    return {
        "property_id": str(row["property_id"]),
        "scan_run_id": str(row["scan_run_id"]),
        "property_name": str(row["property_name"]),
        "aliases": list(row.get("aliases") or []),
        "search_text_normalized": property_search_document(
            str(row["property_name"]),
            row.get("aliases") or [],
        ),
        "country": str(row["country"]),
        "city": str(row["city"]),
        "city_id": row.get("city_id"),
        "city_assignment": row.get("city_assignment"),
        "scene_type": str(row["scene_type"]),
        "longitude": row.get("longitude"),
        "latitude": row.get("latitude"),
        "google_maps_link": row.get("google_maps_link"),
        "geocode_precision": row.get("geocode_precision"),
        "map_source": row.get("map_source"),
        "coordinate_status": row.get("coordinate_status"),
        "evidence_status": str(row["evidence_status"]),
        "value_class": str(row["value_class"]),
        "action_class": str(row["action_class"]),
        "recommended_solution": str(row["recommended_solution"]),
        "annual_visits_est": annual_visits_est,
        "annual_visits_p10": (
            active_traffic.annual_visits_p10 if active_traffic is not None else None
        ),
        "annual_visits_p50": (
            active_traffic.annual_visits_p50
            if active_traffic is not None
            else annual_visits_est
        ),
        "annual_visits_p90": (
            active_traffic.annual_visits_p90 if active_traffic is not None else None
        ),
        "traffic_model_version": (
            active_traffic.model_version if active_traffic is not None else None
        ),
        "proxy_level": str(row["proxy_level"]),
        "busy_hour_traffic_gb": busy_hour_traffic_gb,
        "complaint_pressure": (
            complaints.pressure_level if complaints is not None else None
        ),
        "network_validation_priority": (
            _public_network_priority(network, packet.conclusion.value_class)
            if network is not None and packet is not None
            else None
        ),
        "network_data_freshness": (
            complaints.data_freshness if complaints is not None else None
        ),
        "feature_flags": {
            "traffic_v2": traffic is not None,
            "traffic_v2_activated": active_traffic is not None,
            "complaints": complaints is not None,
            "ookla_public": ookla_public,
        },
        "indoor_system_presence": str(row["indoor_system_presence"]),
        "indoor_rat": str(row["indoor_rat"]),
        "candidate_quality_status": str(row["candidate_quality_status"]),
        "main_table_ready": bool(visibility.get("main_table_ready")),
        "map_ready": bool(visibility.get("map_ready")),
        "export_ready": bool(visibility.get("export_ready")),
        "map_coordinate_ready": (
            row.get("longitude") is not None
            and row.get("latitude") is not None
            and is_map_ready_coordinate(str(row.get("coordinate_status") or ""))
        ),
        "has_review_issue": int(row.get("review_count") or 0) > 0,
        "review_count": int(row.get("review_count") or 0),
        "source_count": int(row.get("source_count") or 0),
        "source_urls": list(row.get("source_urls") or []),
        "main_metric_text": str(row.get("main_metric_text") or ""),
        "visibility": visibility,
        "quality_issues": list(row.get("quality_issues") or []),
        "sort_order": sort_order,
    }


def _filter_columns(row: dict[str, Any], locale: str, sort_order: int) -> dict[str, Any]:
    visibility = dict(row.get("visibility") or {})
    return {
        "locale": locale,
        "property_id": str(row["property_id"]),
        "scan_run_id": str(row["scan_run_id"]),
        "country": str(row["country"]),
        "city": str(row["city"]),
        "city_id": row.get("city_id"),
        "scene_type": str(row["scene_type"]),
        "evidence_status": str(row["evidence_status"]),
        "value_class": str(row["value_class"]),
        "action_class": str(row["action_class"]),
        "recommended_solution": str(row["recommended_solution"]),
        "indoor_system_presence": str(row["indoor_system_presence"]),
        "indoor_rat": str(row["indoor_rat"]),
        "proxy_level": str(row["proxy_level"]),
        "candidate_quality_status": str(row["candidate_quality_status"]),
        "main_table_ready": bool(visibility.get("main_table_ready")),
        "map_ready": bool(visibility.get("map_ready")),
        "export_ready": bool(visibility.get("export_ready")),
        "has_review_issue": int(row.get("review_count") or 0) > 0,
        "sort_order": sort_order,
    }


def _property_packet_row(
    row: dict[str, Any],
    locale: str,
    sort_order: int,
    packet_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = _filter_columns(row, locale, sort_order)
    payload["id"] = f"{locale}:{row['property_id']}"
    payload["packet_json"] = packet_payload
    return payload


def _map_feature_row(
    row: dict[str, Any],
    locale: str,
    sort_order: int,
    feature: dict[str, Any],
) -> dict[str, Any]:
    payload = _filter_columns(row, locale, sort_order)
    payload["id"] = f"{locale}:{row['property_id']}"
    payload["map_coordinate_ready"] = True
    payload["feature_json"] = feature
    return payload


def _review_queue_table_row(
    index_row: dict[str, Any],
    review: dict[str, Any],
    locale: str,
    sort_order: int,
    row_json: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": f"{locale}:{review['review_id']}",
        "review_id": str(review["review_id"]),
        "locale": locale,
        "property_id": str(review["property_id"]),
        "scan_run_id": str(review["scan_run_id"]),
        "country": str(review["country"]),
        "city": str(review["city"]),
        "city_id": index_row.get("city_id"),
        "scene_type": str(review["scene_type"]),
        "evidence_status": str(index_row["evidence_status"]),
        "value_class": str(index_row["value_class"]),
        "action_class": str(index_row["action_class"]),
        "indoor_system_presence": str(index_row["indoor_system_presence"]),
        "candidate_quality_status": str(review["candidate_quality_status"]),
        "status": str(review["status"]),
        "sort_order": sort_order,
        "row_json": row_json,
    }


def _map_feature(
    row: dict[str, Any],
    locale: str,
    latest_scan_at: str | None,
    packet=None,
) -> dict[str, Any]:
    hero_image = row.get("hero_image") or {}
    annual_visits_est = safe_annual_visit_estimate(row.get("annual_visits_est"))
    busy_hour_traffic_gb = (
        None
        if is_year_like_annual_visit(row.get("annual_visits_est"))
        else row["busy_hour_traffic_gb"]
    )
    traffic = packet.traffic_estimate if packet is not None else None
    active_traffic = _active_traffic(traffic)
    network = packet.network_signals if packet is not None else None
    complaints = network.complaints if network is not None else None
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [row["longitude"], row["latitude"]],
        },
        "properties": {
            "property_id": str(row["property_id"]),
            "property_name": row["property_name"],
            "country": row["country"],
            "city": row["city"],
            "city_id": row.get("city_id"),
            "city_assignment": row.get("city_assignment"),
            "scene_type": row["scene_type"],
            "evidence_status": row["evidence_status"],
            "value_class": row["value_class"],
            "action_class": row["action_class"],
            "recommended_solution": row["recommended_solution"],
            "main_metric_text": row["main_metric_text"],
            "annual_visits_est": annual_visits_est,
            "annual_visits_p10": (
                active_traffic.annual_visits_p10 if active_traffic is not None else None
            ),
            "annual_visits_p50": (
                active_traffic.annual_visits_p50
                if active_traffic is not None
                else annual_visits_est
            ),
            "annual_visits_p90": (
                active_traffic.annual_visits_p90 if active_traffic is not None else None
            ),
            "traffic_model_version": (
                active_traffic.model_version if active_traffic is not None else None
            ),
            "busy_hour_traffic_gb": busy_hour_traffic_gb,
            "complaint_pressure": (
                complaints.pressure_level if complaints is not None else None
            ),
            "network_validation_priority": (
                _public_network_priority(network, packet.conclusion.value_class)
                if network is not None and packet is not None
                else None
            ),
            "feature_flags": {
                "traffic_v2": traffic is not None,
                "traffic_v2_activated": active_traffic is not None,
                "complaints": complaints is not None,
                "ookla_public": _public_ookla_enabled(),
            },
            "review_count": row["review_count"],
            "source_count": row["source_count"],
            "indoor_system_presence": row["indoor_system_presence"],
            "indoor_rat": row["indoor_rat"],
            "proxy_level": row["proxy_level"],
            "last_scan_at": latest_scan_at,
            "google_maps_link": row["google_maps_link"],
            "geocode_precision": row["geocode_precision"],
            "map_source": row["map_source"],
            "coordinate_status": row["coordinate_status"],
            "candidate_quality_status": row["candidate_quality_status"],
            "visibility": row["visibility"],
            "quality_issues": row["quality_issues"],
            "hero_image_url": hero_image.get("url"),
            "hero_image_alt": hero_image.get("alt_text"),
            "hero_image_source_name": hero_image.get("source_name"),
            "localized": _map_feature_localized_labels(row, locale),
        },
    }


def _apply_public_network_feature_flags(packet_payload: dict[str, Any]) -> None:
    traffic_payload = packet_payload.get("traffic_estimate")
    traffic_status = (
        str(traffic_payload.get("activation_status") or "")
        if isinstance(traffic_payload, dict)
        else ""
    )
    flags = {
        "traffic_v2": bool(traffic_payload),
        "traffic_v2_activated": traffic_status.startswith("activated"),
        "complaints": bool((packet_payload.get("network_signals") or {}).get("complaints")),
        "ookla_public": _public_ookla_enabled(),
    }
    packet_payload["feature_flags"] = flags
    if flags["ookla_public"]:
        return
    network = packet_payload.get("network_signals")
    if not isinstance(network, dict):
        return
    network["ookla"] = {"mobile": None, "fixed": None}
    reasons = list(network.get("validation_reasons") or [])
    network["validation_reasons"] = [
        reason for reason in reasons if "Ookla" not in str(reason)
    ]
    if not network["validation_reasons"]:
        network["network_validation_priority"] = "Normal"
        network["next_action"] = (
            "Continue normal evidence refresh; no network-pain escalation is justified."
        )


def _public_network_priority(network, value_class) -> str:
    if _public_ookla_enabled():
        return network.network_validation_priority
    complaints = network.complaints
    if complaints is None or complaints.pressure_level != "high":
        return "Normal"
    canonical_value = getattr(value_class, "value", value_class)
    return "High" if canonical_value in {"National Flagship", "City Core"} else "Medium"


def _public_ookla_enabled() -> bool:
    return os.getenv("ISITE2_PUBLIC_OOKLA_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _active_traffic(traffic):
    if traffic is None:
        return None
    if str(traffic.activation_status or "").startswith("activated"):
        return traffic
    return None


def _map_feature_localized_labels(row: dict[str, Any], locale: str) -> dict[str, Any]:
    return {
        "locale": locale,
        "scene_label": scene_label(row["scene_type"], locale),
        "evidence_status_label": enum_label(row["evidence_status"], locale),
        "value_class_label": enum_label(row["value_class"], locale),
        "action_class_label": enum_label(row["action_class"], locale),
        "recommended_solution_label": enum_label(row["recommended_solution"], locale),
        "indoor_system_presence_label": enum_label(row["indoor_system_presence"], locale),
        "indoor_rat_label": enum_label(row["indoor_rat"], locale),
        "proxy_level_label": enum_label(row["proxy_level"], locale),
    }


def _review_queue_row_json(
    review: dict[str, Any],
    locale: str,
    cache: DatabaseLocalizationCache,
    *,
    city_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    reason = localize_text(
        review["reason"],
        text_kind="review.reason",
        target_locale=locale,
        cache=cache,
        allow_provider=False,
        fallback_text=generic_free_text_fallback("review.reason", locale),
    )
    next_action = localize_text(
        review["next_action"],
        text_kind="review.next_action",
        target_locale=locale,
        cache=cache,
        allow_provider=False,
        fallback_text=generic_free_text_fallback("review.next_action", locale),
    )
    miss_count = int(reason.status == "cache_miss") + int(next_action.status == "cache_miss")
    return (
        {
            "property_id": review["property_id"],
            "property_name": review["property_name"],
            "country": review["country"],
            "city": review["city"],
            "city_id": city_id,
            "scene_type": review["scene_type"],
            "candidate_quality_status": review["candidate_quality_status"],
            "visibility": review["visibility"],
            "quality_issues": review["quality_issues"],
            "reason": review["reason"],
            "next_action": review["next_action"],
            "status": review["status"],
            "review_type": review["review_type"],
            "severity": review["severity"],
            "gate_name": review["gate_name"],
            "field_path": review["field_path"],
            "blocking_surfaces": review["blocking_surfaces"],
            "source_url": review["source_url"],
            "suggested_query": review["suggested_query"],
            "localized": {
                "locale": locale,
                "scene_label": scene_label(review["scene_type"], locale),
                "reason": reason.translated_text,
                "reason_original": review["reason"],
                "next_action": next_action.translated_text,
                "next_action_original": review["next_action"],
                "status_label": enum_label(review["status"], locale),
            },
        },
        miss_count,
    )


def _latest_scan_at(repository: SQLAlchemyScanRunRepository) -> str | None:
    runs = repository.list()
    if not runs:
        return None
    return runs[-1].scan_run.created_at.isoformat()


def _cache_miss_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_cache_miss_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_cache_miss_count(item) for item in value)
    return 1 if value == "cache_miss" else 0


def _pending_text_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_pending_text_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_pending_text_count(item) for item in value)
    if not isinstance(value, str):
        return 0
    return int("Localization pending" in value) + int("本地化待刷新" in value)
