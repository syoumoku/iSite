from __future__ import annotations

import os
from uuid import UUID

from isite2.growth.derived_refresh import refresh_active_derived_info
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


def refresh_derived_after_scan(
    repository: SQLAlchemyScanRunRepository,
    scan_run_id: UUID,
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
    return refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(scan_run_id),
        provider_mode=provider_mode,
        concurrency=concurrency,
    )
