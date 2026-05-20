from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=None)
def load_yaml(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clear_config_cache() -> None:
    load_yaml.cache_clear()


def load_scene_rules() -> dict[str, Any]:
    return load_yaml("config/scenes.yaml")


def load_output_template() -> dict[str, Any]:
    return load_yaml("config/output_templates.yaml")


def load_status_enums() -> dict[str, Any]:
    return load_yaml("config/status_enums.yaml")


def load_gate_rules() -> dict[str, Any]:
    return load_yaml("config/gates.yaml")


def load_source_registry() -> dict[str, Any]:
    return load_yaml("config/source_registry.yaml")


def load_discovery_sources() -> dict[str, Any]:
    return load_yaml("config/discovery_sources.yaml")


def load_firecrawl_search_strategy() -> dict[str, Any]:
    return load_yaml("config/firecrawl_search_strategy.yaml")


def load_localized_search_strategy() -> dict[str, Any]:
    return load_yaml("config/localized_search_strategy.yaml")


def load_free_structured_sources() -> dict[str, Any]:
    return load_yaml("config/free_structured_sources.yaml")


def load_candidate_quality_rules() -> dict[str, Any]:
    return load_yaml("config/candidate_quality.yaml")


def scene_definitions() -> dict[str, Any]:
    rules = load_scene_rules()
    return rules["scenes"]


def get_scene_rule(scene_type: str) -> dict[str, Any]:
    scenes = scene_definitions()
    if scene_type not in scenes:
        raise KeyError(f"unknown scene_type: {scene_type}")
    return scenes[scene_type]


def validate_output_template_contract(template: dict[str, Any] | None = None) -> list[str]:
    output_template = template or load_output_template()
    excel = output_template.get("excel", {})
    issues: list[str] = []

    required_sheets = excel.get("required_sheets", [])
    main_columns = excel.get("main_columns", [])
    if "主表" not in required_sheets:
        issues.append("required sheet 主表 missing")
    if "Google地图链接" not in main_columns:
        issues.append("main_columns must include Google地图链接")
    if len(main_columns) != len(set(main_columns)):
        issues.append("main_columns contains duplicates")

    required_column_groups = [
        "evidence_columns",
        "inference_columns",
        "proxy_model_columns",
        "review_queue_columns",
        "country_summary_columns",
    ]
    for group in required_column_groups:
        if not excel.get(group):
            issues.append(f"{group} missing")

    return issues
