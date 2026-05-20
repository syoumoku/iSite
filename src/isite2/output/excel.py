from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook

from isite2.domain.models import EvidenceItem, SitePacket
from isite2.rules.candidate_quality import filter_packets_for_surface
from isite2.rules.config_loader import (
    load_candidate_quality_rules,
    load_output_template,
    scene_definitions,
)

SCENE_SHEETS = {
    "机场",
    "会展中心",
    "商场_Mixed_use",
    "体育场",
    "酒店_MICE",
    "办公_政府",
    "医院",
    "大学",
    "交通枢纽",
}


def create_excel_workbook(
    packets: list[SitePacket] | None = None,
    scope_text: str = "MVP skeleton",
    include_blocked_quality: bool = False,
) -> Workbook:
    packets = _sort_main_packets(
        filter_packets_for_surface(
            packets or [],
            "export",
            include_blocked_quality=include_blocked_quality,
        )
    )
    template = load_output_template()
    excel = template["excel"]

    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    for sheet_name in excel["required_sheets"]:
        worksheet = workbook.create_sheet(sheet_name)
        _write_sheet(worksheet, sheet_name, excel, packets, scope_text)
    return workbook


def write_excel_skeleton(
    path: Path,
    packets: list[SitePacket] | None = None,
    include_blocked_quality: bool = False,
) -> Path:
    workbook = create_excel_workbook(
        packets=packets,
        include_blocked_quality=include_blocked_quality,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _write_sheet(
    worksheet,
    sheet_name: str,
    excel: dict,
    packets: list[SitePacket],
    scope_text: str,
) -> None:
    if sheet_name == "主表" or sheet_name in SCENE_SHEETS:
        worksheet.append(excel["main_columns"])
        for packet in _packets_for_sheet(sheet_name, packets):
            worksheet.append(_main_row(packet))
    elif sheet_name == "证据表":
        worksheet.append(excel["evidence_columns"])
        for row_number, packet in enumerate(packets, start=1):
            for evidence in packet.evidence:
                worksheet.append(
                    [
                        row_number,
                        packet.entity.country,
                        packet.entity.city,
                        packet.entity.property_name,
                        packet.entity.scene_type,
                        evidence.field_group,
                        evidence.field_value,
                        evidence.evidence_type,
                        evidence.source_tier,
                        str(evidence.source_url),
                        "",
                        evidence.cross_check_status,
                    ]
                )
    elif sheet_name == "推测留痕":
        worksheet.append(excel["inference_columns"])
        for row_number, packet in enumerate(packets, start=1):
            for inference in packet.inference:
                worksheet.append(
                    [
                        row_number,
                        packet.entity.country,
                        packet.entity.property_name,
                        inference.inferred_field,
                        inference.inferred_value,
                        inference.inference_basis,
                        inference.inference_chain,
                        inference.inference_confidence,
                    ]
                )
    elif sheet_name == "Proxy模型":
        worksheet.append(excel["proxy_model_columns"])
        for scene_type, scene in scene_definitions().items():
            params = scene["demand_parameters"]
            worksheet.append(
                [
                    scene_type,
                    " / ".join(scene["primary_indicators"]),
                    scene["area_metric"],
                    " / ".join(scene["proxy_basis"]),
                    _range_text(params["attach_rate"]),
                    _range_text(params["indoor_capture"]),
                    _range_text(params["busy_hour_factor"]),
                    _range_text(params["gb_per_user_busy_hour"]),
                ]
            )
    elif sheet_name == "复核队列":
        worksheet.append(excel["review_queue_columns"])
        for row_number, packet in enumerate(packets, start=1):
            for review in packet.review_queue:
                worksheet.append(
                    [
                        row_number,
                        packet.entity.country,
                        packet.entity.city,
                        packet.entity.property_name,
                        review.reason,
                        review.next_action,
                    ]
                )
    elif sheet_name == "国家汇总":
        worksheet.append(excel["country_summary_columns"])
        _write_country_summary(worksheet, packets)
    else:
        worksheet.append(["条目", "说明"])
        for row in _method_rows(scope_text):
            worksheet.append(row)


def _packets_for_sheet(sheet_name: str, packets: list[SitePacket]) -> list[SitePacket]:
    if sheet_name == "主表":
        return packets
    scene_packets = [
        packet for packet in packets if _scene_sheet_name(packet.entity.scene_type) == sheet_name
    ]
    return _sort_scene_packets(scene_packets)


def _main_row(packet: SitePacket) -> list:
    entity = packet.entity
    demand = packet.demand
    build = packet.build_status
    main_evidence = _select_main_evidence(packet)
    main_metric = _main_metric_text(main_evidence)
    return [
        entity.country,
        entity.city,
        entity.property_name,
        entity.longitude,
        entity.latitude,
        entity.scene_type,
        main_metric,
        packet.scene.annual_visits_est,
        demand.busy_hour_traffic_gb if demand else None,
        build.indoor_system_presence,
        build.indoor_rat,
        build.operator_name or "Unknown（候选：运营商列表）",
        build.indoor_system_type,
        "MVP rule-based batch",
        packet.conclusion.evidence_status,
        packet.conclusion.reason_to_recommend,
        packet.conclusion.next_action,
        entity.google_maps_link,
    ]


def _select_main_evidence(packet: SitePacket) -> EvidenceItem | None:
    """Return the strongest scene objective evidence for the Excel main table."""
    if not packet.evidence:
        return None
    scene_type = packet.entity.scene_type
    ranked = sorted(
        packet.evidence,
        key=lambda evidence: _evidence_priority(evidence, scene_type),
    )
    return ranked[0]


def _main_metric_text(evidence: EvidenceItem | None) -> str:
    if evidence is None:
        return "Unknown（待核验）"
    metric_key = evidence.indicator_name or evidence.field_group
    return f"{metric_key}: {evidence.field_value}"


def _sort_main_packets(packets: list[SitePacket]) -> list[SitePacket]:
    return sorted(packets, key=_main_sort_key)


def _main_sort_key(packet: SitePacket) -> tuple[int, float, str, str, str]:
    annual_visits = _safe_float(packet.scene.annual_visits_est)
    return (
        1 if annual_visits is None else 0,
        -(annual_visits or 0.0),
        str(packet.entity.country or "").casefold(),
        str(packet.entity.city or "").casefold(),
        str(packet.entity.property_name or "").casefold(),
    )


def _sort_scene_packets(packets: list[SitePacket]) -> list[SitePacket]:
    return sorted(packets, key=_scene_sort_key)


def _scene_sort_key(packet: SitePacket) -> tuple[int, float, int, str, str, str]:
    evidence = _select_main_evidence(packet)
    metric_value = _metric_sort_value(evidence.field_value if evidence else "")
    metric_key = (
        _evidence_metric_key(evidence, packet.entity.scene_type) if evidence is not None else ""
    )
    metric_order = _scene_metric_order(packet.entity.scene_type)
    return (
        1 if metric_value is None else 0,
        -(metric_value or 0.0),
        metric_order.get(metric_key, 10_000),
        str(packet.entity.country or "").casefold(),
        str(packet.entity.city or "").casefold(),
        str(packet.entity.property_name or "").casefold(),
    )


def _evidence_priority(evidence: EvidenceItem, scene_type: str) -> tuple[int, int, int, int, int]:
    metric_order = _scene_metric_order(scene_type)
    metric_key = _evidence_metric_key(evidence, scene_type, metric_order)
    accepted = metric_key in metric_order
    descriptive = _is_descriptive_metric(metric_key)
    blocked = _has_blocked_objective_text(evidence)
    has_number = bool(re.search(r"\d", evidence.field_value or ""))

    if accepted and has_number and not descriptive and not blocked:
        bucket = 0
    elif accepted and not descriptive and not blocked:
        bucket = 1
    elif accepted and has_number and not blocked:
        bucket = 2
    elif accepted and not blocked:
        bucket = 3
    elif has_number:
        bucket = 4
    else:
        bucket = 5

    return (
        bucket,
        metric_order.get(metric_key, 10_000),
        _evidence_type_rank(str(evidence.evidence_type)),
        _source_tier_rank(str(evidence.source_tier)),
        _cross_check_rank(str(evidence.cross_check_status)),
    )


def _is_scene_objective_evidence(evidence: EvidenceItem, scene_type: str) -> bool:
    metric_order = _scene_metric_order(scene_type)
    metric_key = _evidence_metric_key(evidence, scene_type, metric_order)
    return (
        metric_key in metric_order
        and not _is_descriptive_metric(metric_key)
        and not _has_blocked_objective_text(evidence)
    )


def _scene_metric_order(scene_type: str) -> dict[str, int]:
    rules = load_candidate_quality_rules()
    scene_rule = scene_definitions().get(scene_type, {})
    ordered_metrics = [
        *rules.get("objective_indicator_aliases", {}).get(scene_type, []),
        *scene_rule.get("primary_indicators", []),
        *scene_rule.get("fallback_metrics", []),
    ]
    metric_order: dict[str, int] = {}
    for metric in ordered_metrics:
        normalized = _normalize_metric_key(metric)
        if normalized and normalized not in metric_order:
            metric_order[normalized] = len(metric_order)
    return metric_order


def _evidence_metric_key(
    evidence: EvidenceItem,
    scene_type: str,
    metric_order: dict[str, int] | None = None,
) -> str:
    accepted = metric_order if metric_order is not None else _scene_metric_order(scene_type)
    keys = [
        _normalize_metric_key(evidence.indicator_name or ""),
        _normalize_metric_key(evidence.field_group),
    ]
    for key in keys:
        canonical = _canonical_metric_key(key)
        if canonical in accepted:
            return canonical
    for key in keys:
        if key:
            return _canonical_metric_key(key)
    return ""


def _canonical_metric_key(metric_key: str) -> str:
    aliases = {
        "rooms": "room_count",
        "guest_rooms": "room_count",
        "hotel_keys": "keys",
        "annual_visits": "annual_footfall",
        "annual_passengers": "annual_passenger_throughput",
        "passengers": "passenger_throughput",
        "student_count": "enrollment",
        "students": "enrollment",
    }
    return aliases.get(metric_key, metric_key)


def _normalize_metric_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return re.sub(r"_+", "_", normalized)


def _safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_sort_value(text: str | None) -> float | None:
    values = [_scaled_number(match) for match in _metric_number_pattern().finditer(str(text or ""))]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values)


def _metric_number_pattern() -> re.Pattern[str]:
    return re.compile(
        r"(?P<number>\d+(?:[,.]\d{3})*(?:\.\d+)?|\d+(?:[,.]\d+)?)"
        r"\s*(?P<scale>million|millions|billion|billions|thousand|thousands|"
        r"mn|mio|milhoes|milhões|millones|k)?",
        flags=re.I,
    )


def _scaled_number(match: re.Match[str]) -> float | None:
    value = _parse_number_token(match.group("number"))
    if value is None:
        return None
    scale = str(match.group("scale") or "").casefold()
    if scale in {"billion", "billions"}:
        return value * 1_000_000_000
    if scale in {"million", "millions", "mn", "mio", "milhoes", "milhões", "millones"}:
        return value * 1_000_000
    if scale in {"thousand", "thousands", "k"}:
        return value * 1_000
    return value


def _parse_number_token(raw: str) -> float | None:
    token = raw.strip()
    if "," in token and "." in token:
        normalized = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        normalized = token.replace(",", "") if len(parts[-1]) == 3 else token.replace(",", ".")
    elif "." in token and len(token.split(".")[-1]) == 3:
        normalized = token.replace(".", "")
    else:
        normalized = token
    try:
        return float(normalized)
    except ValueError:
        return None


def _is_descriptive_metric(metric_key: str) -> bool:
    descriptive_tokens = (
        "role",
        "position",
        "brand",
        "tenant_quality",
        "hospital_grade",
        "building_grade",
        "hotel_class",
        "anchor_brands",
        "urban_catchment",
        "core_facility_intensity",
    )
    return any(token in metric_key for token in descriptive_tokens)


def _has_blocked_objective_text(evidence: EvidenceItem) -> bool:
    rules = load_candidate_quality_rules()
    combined = _normalize_metric_key(
        f"{evidence.field_value} {evidence.assumption_note or ''}"
    )
    return any(
        _normalize_metric_key(token) in combined
        for token in rules.get("objective_evidence_blocked_tokens", [])
    )


def _evidence_type_rank(value: str) -> int:
    return {"Direct": 0, "Proxy": 1, "Inferred": 2}.get(value, 3)


def _source_tier_rank(value: str) -> int:
    return {"Tier 1": 0, "Tier 2": 1, "Tier 3": 2}.get(value, 3)


def _cross_check_rank(value: str) -> int:
    return {
        "Strong Cross-check": 0,
        "Partial Cross-check": 1,
        "Single Source": 2,
        "Not Checked": 3,
        "Evidence Conflict": 4,
    }.get(value, 5)


def _write_country_summary(worksheet, packets: list[SitePacket]) -> None:
    countries = sorted({packet.entity.country for packet in packets})
    scene_columns = {
        "机场": "airport_terminal",
        "会展中心": "convention_center",
        "商场/Mixed-use": "mall_mixed_use",
        "体育场": "stadium",
        "酒店/MICE": "luxury_hotel_mice",
        "办公/政府": "office_government",
        "医院": "hospital",
        "大学": "university",
        "交通枢纽": "transport_hub",
    }
    for country in countries:
        country_packets = [packet for packet in packets if packet.entity.country == country]
        worksheet.append(
            [
                country,
                len(country_packets),
                *[
                    sum(packet.entity.scene_type == scene_type for packet in country_packets)
                    for scene_type in scene_columns.values()
                ],
            ]
        )


def _method_rows(scope_text: str) -> list[list[str]]:
    return [
        ["本轮任务范围", scope_text],
        ["主表规则", "主表只保留物业级关键结果；通用公式和定义不逐行重复。"],
        ["排序规则", "主表按年访问量估计从高到低排序；场景分表按该场景主指标数值从高到低排序，缺失或非数值排末尾。"],
        ["证据规则", "结论必须回溯到字段级证据、来源、日期、证据等级和交叉校验状态。"],
        ["推测规则", "推测只补空字段，必须写入推测留痕，不能覆盖直接证据。"],
        ["场景建模规则", "每个场景使用独立主指标、Proxy Basis 和参数区间。"],
        ["Proxy 规则", "Proxy 模型集中写入 Proxy模型 sheet。"],
        ["室分状态字段规则", "现网建设状态独立判断；Unknown 允许，空值不允许。"],
        ["输出约束", "主表固定表头；场景分表保留空表头。"],
        ["证据状态定义", "Verified / Supported / Indicative / Insufficient。"],
        ["Google地图链接规则", "主表必须保留 Google地图链接。"],
    ]


def _scene_sheet_name(scene_type: str) -> str:
    names = {
        "airport_terminal": "机场",
        "convention_center": "会展中心",
        "mall_mixed_use": "商场_Mixed_use",
        "stadium": "体育场",
        "luxury_hotel_mice": "酒店_MICE",
        "office_government": "办公_政府",
        "hospital": "医院",
        "university": "大学",
        "transport_hub": "交通枢纽",
    }
    return names.get(scene_type, scene_type)


def _range_text(values: list[float]) -> str:
    return f"{values[0]}-{values[1]}"
