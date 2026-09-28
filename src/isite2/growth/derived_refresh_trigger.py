from __future__ import annotations

import os
from uuid import UUID

from isite2.growth.derived_refresh import refresh_active_derived_info
from isite2.growth.localization_refresh import refresh_localization_cache_for_packets
from isite2.growth.traffic_refresh import (
    refresh_traffic_estimates_v2,
    rollback_traffic_estimates_v2,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


def refresh_derived_after_scan(
    repository: SQLAlchemyScanRunRepository,
    scan_run_id: UUID,
    *,
    property_ids: list[str] | None = None,
) -> dict:
    """Standard post-scan derived refresh hook.

    Normal local runs default to Codex OAuth. Tests skip the external GPT hook unless
    explicitly enabled.
    """
    enabled = os.getenv("ISITE2_ENABLE_DERIVED_REFRESH_AFTER_SCAN")
    if enabled is None and os.getenv("PYTEST_CURRENT_TEST"):
        enabled = "0"
    if str(enabled or "1").strip().lower() in {"0", "false", "no", "off"}:
        return {
            "mode": "gpt_derived_info_refresh",
            "scan_run_id": str(scan_run_id),
            "skipped_reason": "post-scan derived refresh disabled",
        }

    provider_mode = os.getenv("ISITE2_DERIVED_REFRESH_PROVIDER", "codex-oauth")
    concurrency = int(os.getenv("ISITE2_DERIVED_REFRESH_CONCURRENCY", "1"))
    if (
        provider_mode == "gpt"
        and not os.getenv("OPENAI_API_KEY")
        and not os.getenv("ISITE2_OPENAI_API_KEY")
    ):
        return {
            "mode": "gpt_derived_info_refresh",
            "provider": "openai_gpt",
            "scan_run_id": str(scan_run_id),
            "skipped_reason": (
                "OPENAI_API_KEY or ISITE2_OPENAI_API_KEY is required for standard "
                "GPT derived refresh"
            ),
        }
    result = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(scan_run_id),
        property_ids=property_ids,
        provider_mode=provider_mode,
        concurrency=concurrency,
    )
    result["traffic_refresh"] = _refresh_traffic_after_derived(
        repository,
        scan_run_id,
        property_ids=property_ids,
    )
    result["localization_refresh"] = _refresh_localization_after_derived(
        repository,
        scan_run_id,
        property_ids=property_ids,
    )
    return result


def _refresh_traffic_after_derived(
    repository: SQLAlchemyScanRunRepository,
    scan_run_id: UUID,
    *,
    property_ids: list[str] | None = None,
) -> dict:
    model_version = os.getenv("ISITE2_TRAFFIC_MODEL_VERSION", "v2").strip().lower()
    if model_version == "v1":
        return rollback_traffic_estimates_v2(
            repository.engine,
            scan_run_id=str(scan_run_id),
            property_ids=property_ids,
        )
    try:
        return refresh_traffic_estimates_v2(
            repository.engine,
            scan_run_id=str(scan_run_id),
            property_ids=property_ids,
            activation_mode=os.getenv("ISITE2_TRAFFIC_V2_ACTIVATION_MODE"),
        )
    except Exception as exc:  # noqa: BLE001 - preserve completed derived refresh
        return {
            "mode": "traffic_estimates_v2_refresh",
            "scan_run_id": str(scan_run_id),
            "error": str(exc),
        }


def _refresh_localization_after_derived(
    repository: SQLAlchemyScanRunRepository,
    scan_run_id: UUID,
    *,
    property_ids: list[str] | None = None,
) -> dict:
    enabled = os.getenv("ISITE2_ENABLE_LOCALIZATION_REFRESH_AFTER_DERIVED")
    if enabled is None and os.getenv("PYTEST_CURRENT_TEST"):
        enabled = "0"
    if str(enabled or "1").strip().lower() in {"0", "false", "no", "off"}:
        return {
            "mode": "localization_refresh",
            "scan_run_id": str(scan_run_id),
            "skipped_reason": "post-derived localization refresh disabled",
        }
    locale_env = os.getenv("ISITE2_LOCALIZATION_REFRESH_LOCALES")
    locales = (
        [item.strip() for item in locale_env.split(",") if item.strip()] if locale_env else None
    )
    text_kind_env = os.getenv("ISITE2_LOCALIZATION_REFRESH_TEXT_KINDS")
    text_kinds = (
        [item.strip() for item in text_kind_env.split(",") if item.strip()]
        if text_kind_env
        else None
    )
    provider_mode = os.getenv("ISITE2_LOCALIZATION_PROVIDER", "codex-oauth")
    limit_env = os.getenv("ISITE2_LOCALIZATION_REFRESH_LIMIT")
    limit = int(limit_env) if limit_env else 0
    try:
        packets = repository.list_properties({"scan_run_id": str(scan_run_id)})
        summary = refresh_localization_cache_for_packets(
            packets,
            repository.engine,
            locales=locales,
            text_kinds=text_kinds,
            provider_mode=provider_mode,
            limit=limit,
        )
        summary["scan_run_id"] = str(scan_run_id)
        summary["refresh_scope"] = "scan_run_referenced_text_hashes"
        summary["requested_changed_property_count"] = (
            len(set(property_ids)) if property_ids is not None else None
        )
        return summary
    except Exception as exc:  # noqa: BLE001 - derived refresh already succeeded
        return {
            "mode": "localization_refresh",
            "scan_run_id": str(scan_run_id),
            "error": str(exc),
        }
