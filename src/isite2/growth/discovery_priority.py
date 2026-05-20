from __future__ import annotations

from typing import Any

DEFAULT_SCENE_SCAN_PRIORITY = 50
DEFAULT_SOURCE_PRIORITY = 100
SCENE_PRIORITY_WEIGHT = 1000


def scene_scan_priority(scene_config: dict[str, Any]) -> int:
    return int(scene_config.get("scan_priority", DEFAULT_SCENE_SCAN_PRIORITY))


def discovery_task_priority(
    *,
    scene_config: dict[str, Any],
    source_priorities: dict[str, int],
    source_type: str,
    template_index: int,
) -> int:
    """Rank discovery tasks by scene first, then evidence source, then template order."""

    return (
        scene_scan_priority(scene_config) * SCENE_PRIORITY_WEIGHT
        + source_priorities.get(source_type, DEFAULT_SOURCE_PRIORITY)
        + template_index
    )
