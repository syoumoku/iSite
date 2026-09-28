from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from isite2.domain.enums import (
    ActionClass,
    BuildEvidenceStatus,
    CrossCheckStatus,
    EvidenceStatus,
    EvidenceType,
    IndoorRAT,
    IndoorSystemPresence,
    IndoorSystemType,
    ProxyLevel,
    RecommendedSolution,
    SceneForm,
    SourceTier,
    ValueClass,
)
from isite2.domain.models import (
    BuildStatus,
    CityAssignment,
    Conclusion,
    EvidenceItem,
    InferenceRecord,
    PropertyEntity,
    PropertyHeroImage,
    ReviewItem,
    ScanRun,
    ScanRunResult,
    ScanScope,
    SceneModelResult,
    SitePacket,
)
from isite2.growth.city_normalization import canonicalize_city
from isite2.repositories.interfaces import ScanRunRepository
from isite2.rules.candidate_quality import (
    apply_candidate_quality,
    apply_designated_lead_primary_metric_exception,
    candidate_quality_review_item,
    normalize_text,
)
from isite2.rules.config_loader import get_scene_rule, load_source_registry, scene_definitions
from isite2.rules.coordinates import CoordinateResolver
from isite2.rules.demand import calculate_demand, demand_params_from_scene_rule
from isite2.rules.gates import GateResult, run_all_gates
from isite2.rules.metric_safety import safe_annual_visit_estimate
from isite2.rules.reason import make_google_maps_link, short_reason
from isite2.rules.validation import is_concrete_review_action


@dataclass
class PipelineResult:
    packet: SitePacket
    gate_results: list[GateResult] = field(default_factory=list)
    review_items: list[ReviewItem] = field(default_factory=list)


@dataclass(frozen=True)
class FakeCandidate:
    country: str
    city: str
    property_name: str
    scene_type: str
    latitude: float
    longitude: float
    annual_visits: float | None
    main_metric_text: str
    aliases: list[str] = field(default_factory=list)
    evidence_source_name: str = "MVP Fake Public Evidence Fixture"
    evidence_source_url: str = "https://example.com/isite2-mvp-evidence"
    evidence_source_date: str = "2026"
    evidence_source_tier: SourceTier = SourceTier.TIER_1
    geocode_precision: str = "MVP fake centroid"
    map_source: str = "MVP fake adapter"
    map_source_date: str | None = None
    coordinate_status: str = "Verified"
    discovery_source: str = "mvp_fake"
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    coordinate_review_item: ReviewItem | None = None
    hero_image: dict[str, Any] | None = None
    allow_missing_primary_metric: bool = False
    designation_source: str | None = None
    city_assignment: dict[str, Any] | None = None


BUILD_STATUS_INDICATORS = {
    "indoor_coverage_deployment",
    "das_deployment",
    "indoor_5g_upgrade",
}

ANNUAL_VISITS_INDICATORS = {
    "annual visits",
    "annual passenger throughput",
    "passenger throughput",
    "annual footfall",
    "footfall",
    "daily ridership",
    "interchange volume",
}

DEFAULT_METRIC_TOKENS = {
    "proxy",
    "estimate",
    "estimated",
    "assumption",
    "assumed",
    "verify official",
    "not direct fact",
}


def run_quality_pipeline(
    packet: SitePacket,
    *,
    allow_missing_primary_metric: bool = False,
    designation_source: str | None = None,
) -> PipelineResult:
    """Run deterministic quality checks for a completed site packet."""
    candidate_quality = apply_candidate_quality(packet)
    if allow_missing_primary_metric:
        candidate_quality = apply_designated_lead_primary_metric_exception(
            packet,
            candidate_quality,
        )
    gates = run_all_gates(packet)
    review_items: list[ReviewItem] = list(packet.review_queue)
    quality_review = candidate_quality_review_item(packet, candidate_quality)
    if quality_review is not None:
        review_items.append(quality_review)
    if candidate_quality.status == "review_required" and allow_missing_primary_metric:
        review_items.append(
            ReviewItem(
                reason=(
                    "用户指定线索已确认实体，但场景一级量化主指标仍缺失；"
                    "该例外不代表高价值证据已验证。"
                ),
                next_action=(
                    f"补查 {packet.entity.property_name} 的官方/运营方资料，提取当前场景的"
                    "一级量化主指标、统计周期、来源链接和发布日期。"
                ),
                review_type="designated_lead_primary_metric",
                severity="high",
                gate_name="primary_metric_evidence_gate",
                field_path="scene.primary_value_indicators",
                source_url=designation_source,
                suggested_query=(
                    f"{packet.entity.property_name} {packet.entity.city} "
                    f"{packet.entity.scene_type} official capacity area rooms"
                ),
            )
        )
    for gate in gates:
        if not gate.passed:
            if gate.gate_name == "candidate_quality_gate":
                continue
            review_items.append(
                ReviewItem(
                    reason=f"{gate.gate_name} failed: {'; '.join(gate.issues)}",
                    next_action=(
                        "补查公开来源并补齐缺失字段；"
                        "若无法补齐，保留 Unknown 并记录具体证据缺口。"
                    ),
                )
            )
    for item in review_items:
        if not is_concrete_review_action(item.next_action):
            item.next_action = "补查官方公告、运营商室分公告或地图坐标来源，记录链接和日期。"
    return PipelineResult(packet=packet, gate_results=gates, review_items=review_items)


def run_scan_pipeline(
    scope_data: dict[str, Any],
    repository: ScanRunRepository,
    source_registry: dict[str, Any] | None = None,
    *,
    include_blocked_quality: bool = True,
) -> ScanRunResult:
    result = build_scan_result(
        scope_data,
        source_registry=source_registry,
        include_blocked_quality=include_blocked_quality,
    )
    return repository.save(result)


def build_scan_result(
    scope_data: dict[str, Any],
    source_registry: dict[str, Any] | None = None,
    *,
    include_blocked_quality: bool = True,
) -> ScanRunResult:
    scope = ScanScope.model_validate(scope_data)
    result = ScanRunResult(scan_run=ScanRun(scope=scope))
    packets = [
        _packet_from_candidate(candidate)
        for candidate in _fake_discovery(scope, source_registry=source_registry)
    ]
    if not include_blocked_quality:
        packets = [
            packet
            for packet in packets
            if packet.candidate_quality_status != "blocked_quality"
        ]
    review_items = [item for packet in packets for item in packet.review_queue]

    result.scan_run.status = "completed"
    result.scan_run.candidate_count = len(packets)
    result.scan_run.review_count = len(review_items)
    result.packets = packets
    result.review_items = review_items
    return result


def _fake_discovery(
    scope: ScanScope,
    source_registry: dict[str, Any] | None = None,
) -> list[FakeCandidate]:
    countries = scope.countries or ["Exampleland"]
    cities = scope.cities or ["Example City"]
    requested_scenes = scope.scene_types or list(scene_definitions().keys())
    scene_types = [
        scene_type for scene_type in requested_scenes if scene_type in scene_definitions()
    ]

    candidates: list[FakeCandidate] = []
    for country_index, country in enumerate(countries):
        country_candidates = _registry_candidates(
            country,
            scope.cities,
            scene_types,
            source_registry=source_registry,
        )
        if country_candidates:
            candidates.extend(country_candidates)
            continue
        for scene_index, scene_type in enumerate(scene_types):
            city = cities[scene_index % len(cities)]
            candidates.append(
                FakeCandidate(
                    country=country,
                    city=city,
                    property_name=f"{city} {get_scene_rule(scene_type)['label_zh']} MVP Candidate",
                    scene_type=scene_type,
                    latitude=20.0 + country_index + scene_index * 0.1,
                    longitude=100.0 + country_index + scene_index * 0.1,
                    annual_visits=3_650_000 + scene_index * 365_000,
                    main_metric_text=_main_metric_text(scene_type, scene_index),
                )
            )
    return candidates


def _packet_from_candidate(candidate: FakeCandidate) -> SitePacket:
    scene_rule = get_scene_rule(candidate.scene_type)
    scene_form = SceneForm(scene_rule["scene_form"])
    city_assignment = (
        CityAssignment.model_validate(candidate.city_assignment)
        if candidate.city_assignment
        else canonicalize_city(
            country=candidate.country,
            source_city=candidate.city,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
        )
    )
    canonical_city = (
        city_assignment.canonical_city
        if city_assignment.mapping_status == "verified"
        else candidate.city
    )
    property_entity = PropertyEntity(
        country=candidate.country,
        city=canonical_city,
        property_name=candidate.property_name,
        aliases=candidate.aliases,
        scene_type=candidate.scene_type,
        scene_form=scene_form,
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        geocode_precision=candidate.geocode_precision,
        map_source=candidate.map_source,
        map_source_date=candidate.map_source_date,
        google_maps_link=make_google_maps_link(
            candidate.latitude,
            candidate.longitude,
            property_name=candidate.property_name,
            city=canonical_city,
            country=candidate.country,
        ),
        coordinate_status=candidate.coordinate_status,
        hero_image=(
            PropertyHeroImage.model_validate(candidate.hero_image)
            if candidate.hero_image
            else None
        ),
        city_assignment=city_assignment,
    )

    evidence = _evidence_from_candidate(candidate, property_entity.property_id, scene_rule)
    proxy_basis = scene_rule["proxy_basis"][1]
    safe_annual_visits = safe_annual_visit_estimate(candidate.annual_visits)
    scene = SceneModelResult(
        area_metric_name=scene_rule["area_metric"],
        area_metric_status="Proxy",
        primary_value_indicators=scene_rule["primary_indicators"],
        proxy_basis=proxy_basis,
        proxy_level=ProxyLevel.P1_STRONG,
        annual_visits_est=safe_annual_visits,
        assumption_note=(
            None
            if safe_annual_visits is not None
            else "annual_visits missing; field left unset."
        ),
    )
    build_status = _build_status_from_evidence(evidence)
    demand = calculate_demand(safe_annual_visits, demand_params_from_scene_rule(scene_rule))
    solution = RecommendedSolution(scene_rule["default_solution"])
    inference = []
    if safe_annual_visits is not None and safe_annual_visits > 0:
        inference.append(
            InferenceRecord(
                inferred_field="annual_visits_est",
                inferred_value=str(int(safe_annual_visits)),
                inference_basis=proxy_basis,
                inference_chain=(
                    f"Observed: {candidate.main_metric_text} -> {proxy_basis} -> "
                    f"annual_visits_est={int(safe_annual_visits)}"
                ),
                inference_confidence="Conservative",
            )
        )
    conclusion = Conclusion(
        evidence_status=(
            EvidenceStatus.INSUFFICIENT
            if candidate.allow_missing_primary_metric
            else EvidenceStatus.SUPPORTED
        ),
        value_class=(
            ValueClass.OBSERVATION
            if candidate.allow_missing_primary_metric
            else ValueClass.CITY_CORE
        ),
        action_class=ActionClass.SURVEY_FIRST,
        recommended_solution=solution,
        reason_to_recommend=(
            "用户指定价值楼宇线索；实体和位置已核验，主指标量化证据缺失，需优先补证。"
            if candidate.allow_missing_primary_metric
            else short_reason(
                candidate.scene_type,
                candidate.main_metric_text,
                solution.value,
                inference_used=bool(inference),
            )
        ),
        risk_review_reason="室分建设状态无公开证据，现网状态链独立进入核验。",
        next_action="补查运营商室分公告、业主网络升级公告，并核验 Google Maps 坐标。",
    )
    review_queue = [
        ReviewItem(
            reason="室分建设状态缺少公开证据，不能由物业价值推断。",
            next_action="补查运营商室分公告、业主网络升级公告，并记录来源链接和日期。",
        )
    ]
    if candidate.coordinate_review_item is not None:
        review_queue.append(candidate.coordinate_review_item)
    packet = SitePacket(
        entity=property_entity,
        scene=scene,
        evidence=evidence,
        build_status=build_status,
        demand=demand,
        inference=inference,
        conclusion=conclusion,
        review_queue=review_queue,
    )
    quality = run_quality_pipeline(
        packet,
        allow_missing_primary_metric=candidate.allow_missing_primary_metric,
        designation_source=candidate.designation_source,
    )
    packet.review_queue = quality.review_items
    return packet


def _evidence_from_candidate(
    candidate: FakeCandidate,
    property_id,
    scene_rule: dict,
) -> list[EvidenceItem]:
    seeds = candidate.evidence_items or [
        {
            "field_group": scene_rule["primary_indicators"][0],
            "field_value": candidate.main_metric_text,
            "indicator_name": scene_rule["primary_indicators"][0],
            "source_name": candidate.evidence_source_name,
            "source_tier": candidate.evidence_source_tier,
            "source_url": candidate.evidence_source_url,
            "source_date": candidate.evidence_source_date,
            "evidence_type": EvidenceType.DIRECT,
        }
    ]
    cross_check_status = (
        CrossCheckStatus.PARTIAL if len({str(seed["source_url"]) for seed in seeds}) > 1
        else CrossCheckStatus.SINGLE_SOURCE
    )
    return [
        EvidenceItem(
            property_id=property_id,
            field_group=seed["field_group"],
            field_value=seed["field_value"],
            indicator_name=seed.get("indicator_name"),
            unit=seed.get("unit"),
            source_name=seed["source_name"],
            source_tier=(
                seed["source_tier"]
                if isinstance(seed["source_tier"], SourceTier)
                else SourceTier(seed["source_tier"])
            ),
            source_url=seed["source_url"],
            source_date=seed.get("source_date"),
            evidence_type=(
                seed["evidence_type"]
                if isinstance(seed["evidence_type"], EvidenceType)
                else EvidenceType(seed["evidence_type"])
            ),
            cross_check_status=cross_check_status,
            assumption_note=seed.get("assumption_note"),
        )
        for seed in seeds
    ]


def _build_status_from_evidence(evidence: list[EvidenceItem]) -> BuildStatus:
    for item in evidence:
        field_keys = {
            str(item.field_group or "").casefold(),
            str(item.indicator_name or "").casefold(),
        }
        if item.evidence_type != EvidenceType.DIRECT:
            continue
        matched = field_keys & BUILD_STATUS_INDICATORS
        if not matched:
            continue
        indicator = next(
            key
            for key in [
                "indoor_5g_upgrade",
                "das_deployment",
                "indoor_coverage_deployment",
            ]
            if key in matched
        )
        return BuildStatus(
            indoor_system_presence=IndoorSystemPresence.CONFIRMED_PRESENT,
            indoor_system_type=(
                IndoorSystemType.TRADITIONAL_DAS
                if indicator == "das_deployment"
                else IndoorSystemType.UNKNOWN
            ),
            indoor_rat=(
                IndoorRAT.FIVE_G
                if indicator == "indoor_5g_upgrade"
                or "5g" in str(item.field_value).casefold()
                else IndoorRAT.UNKNOWN
            ),
            build_evidence_status=BuildEvidenceStatus.SUPPORTED,
        )
    return BuildStatus(
        indoor_system_presence=IndoorSystemPresence.NO_PUBLIC_EVIDENCE,
        indoor_system_type=IndoorSystemType.UNKNOWN,
        indoor_rat=IndoorRAT.UNKNOWN,
        build_evidence_status=BuildEvidenceStatus.UNKNOWN,
    )


def _registry_candidates(
    country: str,
    cities: list[str],
    scene_types: list[str],
    source_registry: dict[str, Any] | None = None,
) -> list[FakeCandidate]:
    registry = _find_country_registry(country, source_registry=source_registry)
    if registry is None:
        return []

    allowed_cities = {city.strip().casefold() for city in cities if city.strip()}
    resolver = CoordinateResolver(registry)
    candidates: list[FakeCandidate] = []
    for row in registry.get("candidates", []):
        if row["scene_type"] not in scene_types:
            continue
        if allowed_cities and row["city"].casefold() not in allowed_cities:
            continue
        coordinate = resolver.resolve(row)
        evidence_items = row.get("evidence", [])
        main_metric_text = (
            evidence_items[0]["field_value"]
            if evidence_items else f"主指标：{row['property_name']} registry seed"
        )
        candidates.append(
            FakeCandidate(
                country=country,
                city=row["city"],
                property_name=row["property_name"],
                aliases=list(row.get("aliases") or []),
                scene_type=row["scene_type"],
                latitude=coordinate.latitude,
                longitude=coordinate.longitude,
                annual_visits=_observed_annual_visits(row),
                main_metric_text=main_metric_text,
                geocode_precision=coordinate.geocode_precision,
                map_source=coordinate.map_source,
                map_source_date=coordinate.map_source_date,
                coordinate_status=coordinate.coordinate_status,
                discovery_source=row.get("discovery_source", "source_registry"),
                evidence_items=evidence_items,
                coordinate_review_item=coordinate.review_item,
                hero_image=row.get("hero_image"),
                allow_missing_primary_metric=bool(
                    row.get("designated_lead")
                    and row.get("primary_metric_status") == "missing"
                ),
                designation_source=row.get("designation_source"),
                city_assignment=(
                    dict(row["city_assignment"])
                    if isinstance(row.get("city_assignment"), dict)
                    else None
                ),
            )
        )
    return [
        candidate
        for candidate in candidates
    ]


def _find_country_registry(
    country: str,
    source_registry: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized = country.strip().casefold()
    registry_data = source_registry or load_source_registry()
    for canonical, registry in registry_data["countries"].items():
        aliases = [canonical, *registry.get("aliases", [])]
        if normalized in {alias.casefold() for alias in aliases}:
            return registry
    return None


def _observed_annual_visits(row: dict[str, Any]) -> float | None:
    for evidence in row.get("evidence") or []:
        keys = {
            normalize_text(evidence.get("field_group")),
            normalize_text(evidence.get("indicator_name")),
        }
        if not (keys & ANNUAL_VISITS_INDICATORS):
            continue
        if normalize_text(evidence.get("evidence_type")) == "proxy":
            continue
        evidence_text = normalize_text(
            f"{evidence.get('field_value') or ''} {evidence.get('assumption_note') or ''}"
        )
        if any(token in evidence_text for token in DEFAULT_METRIC_TOKENS):
            continue
        observed = _numeric_metric_value(evidence.get("field_value"))
        if observed is not None and observed > 0:
            return observed
    return None


def _numeric_metric_value(value: str | None) -> float | None:
    text = str(value or "")
    numbers = []
    lower = text.casefold()
    for match in re.finditer(r"\d[\d,.]*", text):
        raw = match.group(0)
        suffix = lower[match.end() : match.end() + 24]
        parsed = _parse_metric_number(
            raw,
            scaled_suffix=("billion" in suffix or "million" in suffix),
        )
        if parsed is None:
            continue
        if safe_annual_visit_estimate(parsed) is None:
            continue
        if "billion" in suffix:
            parsed *= 1_000_000_000
        elif "million" in suffix:
            parsed *= 1_000_000
        numbers.append(parsed)
    if not numbers:
        return None
    return max(numbers)


def _parse_metric_number(value: str, *, scaled_suffix: bool = False) -> float | None:
    normalized = value.replace(" ", "")
    if (
        scaled_suffix
        and "," not in normalized
        and re.fullmatch(r"\d{1,3}\.\d{1,3}", normalized)
    ):
        try:
            return float(normalized)
        except ValueError:
            return None
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", normalized):
        normalized = normalized.replace(".", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", normalized):
        normalized = normalized.replace(",", "")
    try:
        return float(normalized.replace(",", ""))
    except ValueError:
        return None


def _main_metric_text(scene_type: str, index: int) -> str:
    values = {
        "airport_terminal": f"年客流：{10_000_000 + index * 100_000:,}人次（2026）",
        "convention_center": f"会展面积：{30_000 + index * 500:,}㎡",
        "stadium": f"座位数：{45_000 + index * 500:,}座",
        "luxury_hotel_mice": f"品牌：MVP Hotel；档次：5星；房间数：{220 + index}间",
        "mall_mixed_use": f"GLA：{80_000 + index * 1000:,}㎡",
        "office_government": f"办公面积：{60_000 + index * 1000:,}㎡",
        "hospital": f"床位数：{800 + index}床",
        "university": f"在校人数：{30_000 + index * 1000:,}人",
        "transport_hub": f"日均客流：{120_000 + index * 1000:,}人次",
        "cruise_port": f"年客流：{1_000_000 + index * 100_000:,}人次（2026）",
        "mosque": f"清真寺面积：{25_000 + index * 1000:,}㎡",
    }
    return values.get(scene_type, f"主指标：MVP fixture {index}")
