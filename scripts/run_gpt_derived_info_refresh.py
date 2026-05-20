from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from isite2.growth.derived_refresh import refresh_active_derived_info
from isite2.repositories import get_default_repository


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Use GPT to aggregate each active property's objective evidence and refresh "
            "derived iSite2 fields."
        )
    )
    parser.add_argument("--scan-run-id")
    parser.add_argument(
        "--provider",
        choices=["gpt", "codex-oauth", "auto", "rule"],
        default="codex-oauth",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force GPT re-analysis even when the evidence package hash is unchanged.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs") / "qa")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    repository = get_default_repository()
    cache_dir = None
    if not args.no_cache and args.provider != "rule":
        cache_dir = Path("outputs") / "derived_refresh" / "gpt_cache"
    try:
        summary = refresh_active_derived_info(
            repository.engine,
            scan_run_id=args.scan_run_id,
            provider_mode=args.provider,
            limit=args.limit,
            cache_dir=cache_dir,
            force=args.force,
            concurrency=args.concurrency,
        )
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": str(exc),
                    "next_action": (
                        "For --provider gpt, set OPENAI_API_KEY or ISITE2_OPENAI_API_KEY. "
                        "For OAuth, run `codex login` and rerun with --provider codex-oauth."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2) from exc
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = args.output_dir / f"gpt_derived_info_refresh_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _render_report(summary: dict) -> str:
    scene_lines = []
    for scene, stats in summary["qa_after"]["scene_summary"].items():
        scene_lines.append(
            "- {scene}: total={total}, hard={hard}, low={low}, multi_source={multi}, "
            "evidence_items={evidence}".format(
                scene=scene,
                total=stats["total"],
                hard=stats["hard_primary_metric_properties"],
                low=stats["low_primary_metric_properties"],
                multi=stats["multi_source_properties"],
                evidence=stats["evidence_items"],
            )
        )
    errors = "\n".join(
        f"- {item['property_name']}: {item['error']}" for item in summary.get("errors", [])
    )
    if not errors:
        errors = "- none"
    return (
        "# GPT Derived Info Refresh\n\n"
        f"- Provider: {summary['provider']}\n"
        f"- Target count: {summary['target_count']}\n"
        f"- Refreshed: {summary['counts'].get('properties_refreshed', 0)}\n"
        f"- Errors: {summary['error_count']}\n\n"
        "## Scene QA\n"
        + "\n".join(scene_lines)
        + "\n\n## Errors\n"
        + errors
        + "\n"
    )


if __name__ == "__main__":
    main()
