from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


SEED_SCAN = "seed_scan"
DEEP_EXPANSION = "deep_expansion"
NEAR_SATURATION = "near_saturation"

_MIN_SATURATION_SCENES = 6
_MIN_SATURATION_SOURCE_TYPES = 2
_MIN_SATURATION_GROUPS = 12


def evaluate_scan_maturity(
    *,
    candidate_count: int,
    scene_count: int,
    source_count: int,
    progress_rows: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Classify country coverage without treating candidate volume as market size."""
    progress = list(progress_rows)
    progress_scenes = {str(row.get("scene_type") or "") for row in progress}
    progress_scenes.discard("")
    source_types = {str(row.get("source_type") or "") for row in progress}
    source_types.discard("")
    exhausted_count = sum(str(row.get("status") or "") == "exhausted" for row in progress)
    tracked_rounds = max((int(row.get("cycle_number") or 0) for row in progress), default=0)
    density = round(source_count / candidate_count, 2) if candidate_count else 0.0

    saturation_ready = (
        len(progress) >= _MIN_SATURATION_GROUPS
        and len(progress_scenes) >= _MIN_SATURATION_SCENES
        and len(source_types) >= _MIN_SATURATION_SOURCE_TYPES
        and exhausted_count == len(progress)
    )
    if saturation_ready:
        level = NEAR_SATURATION
        basis = "tracked_exhaustion"
    else:
        tracked_deep_expansion = (
            tracked_rounds >= 2
            and len(progress_scenes) >= 4
            and len(source_types) >= 2
        )
        inferred_deep_expansion = (
            scene_count >= 6
            and (
                (candidate_count >= 100 and density >= 1.2)
                or (candidate_count >= 50 and density >= 1.5)
            )
        )
        if tracked_deep_expansion or inferred_deep_expansion:
            level = DEEP_EXPANSION
            basis = "tracked_rounds" if tracked_deep_expansion else "active_coverage"
        else:
            level = SEED_SCAN
            basis = "active_coverage"

    return {
        "level": level,
        "basis": basis,
        "scene_count": scene_count,
        "evidence_per_candidate": density,
        "progress_group_count": len(progress),
        "exhausted_group_count": exhausted_count,
        "tracked_rounds": tracked_rounds,
    }


def add_scan_maturity_to_country_summaries(
    summaries: Iterable[dict[str, Any]],
    progress_rows: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    progress_by_country: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in progress_rows:
        country = str(row.get("country") or "")
        if country:
            progress_by_country[country].append(row)

    enriched = []
    for raw_summary in summaries:
        summary = dict(raw_summary)
        country = str(summary.get("country") or "")
        scenes = summary.get("scenes") or {}
        summary["scan_maturity"] = evaluate_scan_maturity(
            candidate_count=int(summary.get("candidate_count") or 0),
            scene_count=sum(int(count or 0) > 0 for count in scenes.values()),
            source_count=int(summary.get("source_count") or 0),
            progress_rows=progress_by_country.get(country, ()),
        )
        enriched.append(summary)
    return enriched
