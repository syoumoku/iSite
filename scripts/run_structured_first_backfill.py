from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.growth.structured_first_backfill import build_structured_first_plan

DB_PATH = ROOT / "outputs" / "isite2_dev.db"
OUTPUT_DIR = ROOT / "outputs" / "regional_scan_loop"

DEFAULT_SCENES = [
    "airport_terminal",
    "stadium",
    "convention_center",
    "mall_mixed_use",
    "luxury_hotel_mice",
    "office_government",
    "transport_hub",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run a structured-first evidence backfill plan. "
            "This script does not call Firecrawl or any live public source."
        )
    )
    parser.add_argument("--countries", default="", help="Comma-separated country list.")
    parser.add_argument("--scenes", default=",".join(DEFAULT_SCENES))
    parser.add_argument("--max-properties", type=int, default=120)
    parser.add_argument("--max-per-country-scene", type=int, default=8)
    parser.add_argument(
        "--include-firecrawl-gap-fill",
        action="store_true",
        help="Include last-resort Firecrawl tasks in the plan only; no calls are executed.",
    )
    args = parser.parse_args()

    countries = _split(args.countries) or _active_countries()
    scenes = _split(args.scenes) or DEFAULT_SCENES
    targets = _active_targets(
        countries=countries,
        scenes=scenes,
        max_properties=args.max_properties,
        max_per_country_scene=args.max_per_country_scene,
    )
    if not countries and targets:
        countries = sorted({target["country"] for target in targets})

    plan = build_structured_first_plan(
        countries=countries,
        scenes=scenes,
        target_properties=targets,
        include_firecrawl_gap_fill=args.include_firecrawl_gap_fill,
    )
    summary = {
        "mode": "structured_first_backfill_dry_run",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "firecrawl_calls_executed": 0,
        "db_path": str(DB_PATH),
        "active_scan_run_id": _active_scan_run_id(),
        "plan": plan.as_dict(),
        "target_counts": {
            "by_country": dict(Counter(target["country"] for target in targets)),
            "by_scene": dict(Counter(target["scene_type"] for target in targets)),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = OUTPUT_DIR / f"structured_first_backfill_plan_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(_console_summary(summary), ensure_ascii=False, indent=2))


def _active_targets(
    *,
    countries: list[str],
    scenes: list[str],
    max_properties: int,
    max_per_country_scene: int,
) -> list[dict[str, Any]]:
    if not DB_PATH.exists() or max_properties <= 0:
        return []
    active_run_id = _active_scan_run_id()
    if not active_run_id:
        return []

    country_filter = ""
    scene_filter = ""
    params: list[Any] = [active_run_id]
    if countries:
        country_filter = f"and sc.country in ({','.join('?' for _ in countries)})"
        params.extend(countries)
    if scenes:
        scene_filter = f"and sc.scene_type in ({','.join('?' for _ in scenes)})"
        params.extend(scenes)
    query = f"""
        select sc.country, sc.city, sc.raw_name, sc.scene_type
        from scan_candidates sc
        where sc.scan_run_id = ?
          and coalesce(sc.candidate_quality_status, 'ready') != 'blocked'
          {country_filter}
          {scene_filter}
        order by sc.country, sc.scene_type, sc.raw_name
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(query, params).fetchall()
    targets = [
        {
            "country": country,
            "city": city or "",
            "property_name": raw_name,
            "scene_type": scene_type,
        }
        for country, city, raw_name, scene_type in rows
    ]
    return _balanced_sample(
        targets,
        max_properties=max_properties,
        max_per_country_scene=max_per_country_scene,
    )


def _balanced_sample(
    targets: list[dict[str, Any]],
    *,
    max_properties: int,
    max_per_country_scene: int,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    bucket_limit = max(1, max_per_country_scene)
    for target in targets:
        key = (target["country"], target["scene_type"])
        if len(buckets[key]) < bucket_limit:
            buckets[key].append(target)

    sampled: list[dict[str, Any]] = []
    keys = sorted(buckets)
    index = 0
    while len(sampled) < max_properties:
        added = False
        for key in keys:
            bucket = buckets[key]
            if index >= len(bucket):
                continue
            sampled.append(bucket[index])
            added = True
            if len(sampled) >= max_properties:
                break
        if not added:
            break
        index += 1
    return sampled


def _active_countries() -> list[str]:
    if not DB_PATH.exists():
        return []
    active_run_id = _active_scan_run_id()
    if not active_run_id:
        return []
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            select distinct country
            from scan_candidates
            where scan_run_id = ?
            order by country
            """,
            (active_run_id,),
        ).fetchall()
    return [country for (country,) in rows]


def _active_scan_run_id() -> str | None:
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            select scan_run_id
            from scan_candidates
            group by scan_run_id
            order by count(*) desc
            limit 1
            """
        ).fetchone()
    return str(row[0]) if row else None


def _render_report(summary: dict[str, Any]) -> str:
    plan = summary["plan"]
    target_counts = summary["target_counts"]
    lines = [
        "# Structured-first Evidence Backfill Plan",
        "",
        f"- Timestamp UTC: {summary['timestamp_utc']}",
        f"- Active scan run: {summary['active_scan_run_id']}",
        f"- Firecrawl calls executed: {summary['firecrawl_calls_executed']}",
        f"- Countries: {len(plan['countries'])}",
        f"- Scenes: {', '.join(plan['scenes'])}",
        f"- Target properties sampled: {plan['property_target_count']}",
        f"- Structured/free tasks: {len(plan['structured_tasks'])}",
        f"- Localized free queries: {len(plan['localized_queries'])}",
        f"- Planned Firecrawl gap-fill tasks: {len(plan['firecrawl_tasks'])}",
        f"- Naive Firecrawl request estimate: {plan['estimated_naive_firecrawl_requests']}",
        f"- Firecrawl requests saved estimate: {plan['estimated_firecrawl_requests_saved']}",
        "",
        "## Source Counts",
    ]
    for source_id, count in sorted(plan["source_counts"].items()):
        lines.append(f"- {source_id}: {count}")
    lines.extend(["", "## Query Phase Counts"])
    for phase, count in sorted(plan["phase_counts"].items()):
        lines.append(f"- {phase}: {count}")
    lines.extend(["", "## Target Counts By Scene"])
    for scene_type, count in sorted(target_counts["by_scene"].items()):
        lines.append(f"- {scene_type}: {count}")
    lines.extend(["", "## Target Counts By Country"])
    for country, count in sorted(target_counts["by_country"].items()):
        lines.append(f"- {country}: {count}")
    lines.append("")
    lines.append("No live requests were executed by this dry run.")
    return "\n".join(lines) + "\n"


def _console_summary(summary: dict[str, Any]) -> dict[str, Any]:
    plan = summary["plan"]
    return {
        "mode": summary["mode"],
        "firecrawl_calls_executed": summary["firecrawl_calls_executed"],
        "active_scan_run_id": summary["active_scan_run_id"],
        "target_properties": plan["property_target_count"],
        "structured_tasks": len(plan["structured_tasks"]),
        "localized_queries": len(plan["localized_queries"]),
        "firecrawl_gap_fill_tasks": len(plan["firecrawl_tasks"]),
        "estimated_firecrawl_requests_saved": plan["estimated_firecrawl_requests_saved"],
        "summary_path": summary["summary_path"],
        "report_path": summary["report_path"],
    }


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
