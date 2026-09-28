from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


@cache
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


def load_localization_config() -> dict[str, Any]:
    return load_yaml("config/localization.yaml")


def load_free_structured_sources() -> dict[str, Any]:
    return load_yaml("config/free_structured_sources.yaml")


def load_candidate_quality_rules() -> dict[str, Any]:
    return load_yaml("config/candidate_quality.yaml")


def load_traffic_model() -> dict[str, Any]:
    return load_yaml("config/traffic_model.yaml")


def load_qa_controls() -> dict[str, Any]:
    return load_yaml("config/qa_controls.yaml")


def load_city_admin_sources() -> dict[str, Any]:
    curated = load_yaml("config/city_admin_sources.yaml")
    generated_path = ROOT / "config/city_admin_sources.global.yaml"
    if not generated_path.exists():
        return curated
    generated = load_yaml("config/city_admin_sources.global.yaml")
    return {
        **generated,
        **curated,
        "source_policy": {
            **(generated.get("source_policy") or {}),
            **(curated.get("source_policy") or {}),
        },
        "countries": {
            **(generated.get("countries") or {}),
            **(curated.get("countries") or {}),
        },
    }


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
    if "物业点重要证据" in main_columns and "主指标量化值" in main_columns:
        evidence_index = main_columns.index("物业点重要证据")
        if main_columns.index("主指标量化值") != evidence_index + 1:
            issues.append("main_columns must place 主指标量化值 after 物业点重要证据")
    else:
        issues.append("main_columns must include 主指标量化值")
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


def validate_qa_controls_contract(controls: dict[str, Any] | None = None) -> list[str]:
    payload = controls or load_qa_controls()
    issues: list[str] = []
    history_archive = str(payload.get("history_archive") or "")
    active_guide = str(payload.get("active_guide") or "")
    for label, relative_path in (
        ("history_archive", history_archive),
        ("active_guide", active_guide),
    ):
        if not relative_path or not (ROOT / relative_path).is_file():
            issues.append(f"{label} must reference an existing file")

    rows = payload.get("controls")
    if not isinstance(rows, list) or not rows:
        return issues + ["controls must be a non-empty list"]

    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"controls[{index}] must be an object")
            continue
        control_id = str(row.get("id") or "")
        if not control_id.startswith("QA-"):
            issues.append(f"controls[{index}].id must start with QA-")
        elif control_id in seen_ids:
            issues.append(f"duplicate control id: {control_id}")
        seen_ids.add(control_id)
        if row.get("severity") not in {"critical", "high", "medium", "low"}:
            issues.append(f"{control_id}: unsupported severity")
        enforcement = row.get("enforcement")
        if enforcement == "automated":
            for field in ("rule_refs", "test_refs"):
                refs = row.get(field)
                if not isinstance(refs, list) or not refs:
                    issues.append(f"{control_id}: automated control requires {field}")
                    continue
                for relative_path in refs:
                    if not (ROOT / str(relative_path)).is_file():
                        issues.append(
                            f"{control_id}: missing {field[:-1]} {relative_path}"
                        )
        elif enforcement == "manual":
            runbook_path = str(row.get("runbook_ref") or "").split("#", 1)[0]
            if not runbook_path or not (ROOT / runbook_path).is_file():
                issues.append(f"{control_id}: manual control requires a valid runbook_ref")
            if row.get("artifact_required") is not True:
                issues.append(f"{control_id}: manual control must require an artifact")
        else:
            issues.append(f"{control_id}: enforcement must be automated or manual")
    return issues
