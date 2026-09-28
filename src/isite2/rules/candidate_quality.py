from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from isite2.domain.models import CandidateVisibility, ReviewItem, SitePacket
from isite2.rules.config_loader import load_candidate_quality_rules, scene_definitions
from isite2.rules.metric_period import annual_metric_period_issue

READY = "ready"
REVIEW_REQUIRED = "review_required"
BLOCKED_QUALITY = "blocked_quality"


@dataclass(frozen=True)
class CandidateQualityResult:
    status: str
    issues: list[str] = field(default_factory=list)
    visibility: CandidateVisibility = field(default_factory=CandidateVisibility)
    blocking_surfaces: list[str] = field(default_factory=list)


def evaluate_packet_quality(packet: SitePacket) -> CandidateQualityResult:
    evidence_field_groups = [evidence.field_group for evidence in packet.evidence]
    evidence_indicator_names = [
        evidence.indicator_name
        for evidence in packet.evidence
        if evidence.indicator_name is not None
    ]
    evidence_types = [evidence.evidence_type.value for evidence in packet.evidence]
    evidence_values = [evidence.field_value for evidence in packet.evidence]
    evidence_assumption_notes = [
        evidence.assumption_note or "" for evidence in packet.evidence
    ]
    hero_image_url = (
        str(packet.entity.hero_image.url)
        if packet.entity.hero_image is not None
        else None
    )
    return evaluate_candidate_quality(
        country=packet.entity.country,
        city=packet.entity.city,
        property_name=packet.entity.property_name,
        scene_type=packet.entity.scene_type,
        geocode_precision=packet.entity.geocode_precision,
        source_urls=[str(evidence.source_url) for evidence in packet.evidence],
        source_names=[evidence.source_name for evidence in packet.evidence],
        hero_image_url=hero_image_url,
        evidence_field_groups=evidence_field_groups,
        evidence_indicator_names=evidence_indicator_names,
        evidence_types=evidence_types,
        evidence_values=evidence_values,
        evidence_assumption_notes=evidence_assumption_notes,
        annual_visits_est=packet.scene.annual_visits_est,
        scene_assumption_note=packet.scene.assumption_note,
        city_assignment_status=(
            packet.entity.city_assignment.mapping_status
            if packet.entity.city_assignment is not None
            else None
        ),
        require_surface_assets=True,
    )


def apply_candidate_quality(packet: SitePacket) -> CandidateQualityResult:
    result = evaluate_packet_quality(packet)
    packet.candidate_quality_status = result.status
    packet.visibility = result.visibility
    packet.quality_issues = list(result.issues)
    return result


def apply_designated_lead_primary_metric_exception(
    packet: SitePacket,
    result: CandidateQualityResult,
) -> CandidateQualityResult:
    """Expose a user-designated lead when its only defect is a missing primary metric."""
    if result.status != BLOCKED_QUALITY or not result.issues:
        return result
    if not all(_is_missing_primary_metric_issue(issue) for issue in result.issues):
        return result

    exception = CandidateQualityResult(
        status=REVIEW_REQUIRED,
        issues=list(result.issues),
        visibility=CandidateVisibility(
            raw_pool=True,
            review_required=True,
            main_table_ready=True,
            map_ready=True,
            export_ready=True,
        ),
        blocking_surfaces=[],
    )
    packet.candidate_quality_status = exception.status
    packet.visibility = exception.visibility
    packet.quality_issues = list(exception.issues)
    return exception


def evaluate_candidate_quality(
    *,
    country: str,
    city: str,
    property_name: str,
    scene_type: str,
    geocode_precision: str | None = None,
    source_urls: list[str] | None = None,
    source_names: list[str] | None = None,
    hero_image_url: str | None = None,
    evidence_field_groups: list[str] | None = None,
    evidence_indicator_names: list[str] | None = None,
    evidence_types: list[str] | None = None,
    evidence_values: list[str] | None = None,
    evidence_assumption_notes: list[str] | None = None,
    annual_visits_est: float | None = None,
    scene_assumption_note: str | None = None,
    city_assignment_status: str | None = None,
    require_surface_assets: bool = False,
    config: dict | None = None,
) -> CandidateQualityResult:
    rules = config or load_candidate_quality_rules()
    issues: list[str] = []
    source_urls = source_urls or []
    source_names = source_names or []
    evidence_field_groups = evidence_field_groups or []
    evidence_indicator_names = evidence_indicator_names or []
    evidence_types = evidence_types or []
    evidence_values = evidence_values or []
    evidence_assumption_notes = evidence_assumption_notes or []

    normalized_name = normalize_text(property_name)
    normalized_city = normalize_text(city)
    normalized_country = normalize_text(country)

    if not normalized_name:
        issues.append("property_name missing")
    if is_placeholder_property_name(property_name, rules):
        issues.append("property_name contains placeholder or fixture token")
    if normalized_name and normalized_name in {normalized_city, normalized_country}:
        issues.append("property_name is only a city or country name")
    if is_generic_topic_name(property_name, rules):
        issues.append("property_name is a generic topic, not a property entity")
    if is_location_only_name(property_name, rules) and not _has_scene_token(
        property_name,
        scene_type,
        rules,
    ):
        issues.append("property_name appears to be a location-only page title")

    default_entity_issues = validate_observed_entity_fields(
        city=city,
        country=country,
        geocode_precision=geocode_precision,
        require_city=require_surface_assets or geocode_precision is not None,
        config=rules,
    )
    issues.extend(default_entity_issues)

    city_country_issue = validate_city_country_alignment(
        city=city,
        country=country,
        config=rules,
    )
    if city_country_issue is not None:
        issues.append(city_country_issue)

    source_alignment_issues = validate_evidence_entity_alignment(
        property_name=property_name,
        scene_type=scene_type,
        source_urls=source_urls,
        source_names=source_names,
        config=rules,
    )
    issues.extend(source_alignment_issues)

    if geocode_precision is not None:
        geocode_issue = validate_geocode_precision_for_scene(
            scene_type=scene_type,
            geocode_precision=geocode_precision,
            config=rules,
        )
        if geocode_issue is not None:
            issues.append(geocode_issue)

    if require_surface_assets:
        if city_assignment_status is not None and city_assignment_status != "verified":
            issues.append("city assignment is not verified")
        objective_issue = validate_scene_objective_evidence(
            scene_type=scene_type,
            evidence_field_groups=evidence_field_groups,
            evidence_indicator_names=evidence_indicator_names,
            evidence_types=evidence_types,
            evidence_values=evidence_values,
            evidence_assumption_notes=evidence_assumption_notes,
            config=rules,
        )
        if objective_issue is not None:
            issues.append(objective_issue)
        scene_default_issue = validate_scene_default_values(
            annual_visits_est=annual_visits_est,
            assumption_note=scene_assumption_note,
            config=rules,
        )
        if scene_default_issue is not None:
            issues.append(scene_default_issue)

    if issues:
        surfaces = list(rules.get("blocking_surfaces", ["main_table", "map", "export"]))
        return CandidateQualityResult(
            status=BLOCKED_QUALITY,
            issues=_dedupe(issues),
            visibility=CandidateVisibility(
                raw_pool=True,
                review_required=True,
                main_table_ready=False,
                map_ready=False,
                export_ready=False,
            ),
            blocking_surfaces=surfaces,
        )
    return CandidateQualityResult(status=READY)


def is_placeholder_property_name(property_name: str, config: dict | None = None) -> bool:
    rules = config or load_candidate_quality_rules()
    normalized = normalize_text(property_name)
    return any(
        normalize_text(token) in normalized
        for token in rules.get("placeholder_tokens", [])
    )


def is_city_or_country_only_name(property_name: str, city: str, country: str) -> bool:
    normalized = normalize_text(property_name)
    return normalized in {normalize_text(city), normalize_text(country)}


def is_generic_topic_name(property_name: str, config: dict | None = None) -> bool:
    rules = config or load_candidate_quality_rules()
    normalized = normalize_text(property_name)
    return normalized in {
        normalize_text(name)
        for name in rules.get("generic_topic_names", [])
    }


def is_location_only_name(property_name: str, config: dict | None = None) -> bool:
    rules = config or load_candidate_quality_rules()
    normalized = normalize_text(property_name)
    return normalized in {
        normalize_text(name)
        for name in rules.get("known_location_only_names", [])
    }


def validate_observed_entity_fields(
    *,
    city: str,
    country: str,
    geocode_precision: str | None,
    require_city: bool,
    config: dict | None = None,
) -> list[str]:
    rules = config or load_candidate_quality_rules()
    issues: list[str] = []
    normalized_city = normalize_text(city)
    normalized_country = normalize_text(country)
    default_tokens = [
        normalize_text(token)
        for token in rules.get("default_value_tokens", [])
    ]

    if require_city and not normalized_city:
        issues.append("city missing; scan results must not use implicit defaults")
    if normalized_city and normalized_city == normalized_country:
        issues.append("city appears defaulted to country")
    if normalized_city and normalized_city in set(default_tokens):
        issues.append("city contains a default or unknown value")
    city_level_issue = validate_city_hierarchy_level(
        city=city,
        country=country,
        config=rules,
    )
    if city_level_issue is not None:
        issues.append(city_level_issue)
    normalized_precision = normalize_text(geocode_precision or "")
    if normalized_precision and any(token in normalized_precision for token in default_tokens):
        issues.append("geocode_precision contains a default or placeholder value")
    return issues


def validate_city_hierarchy_level(
    *,
    city: str,
    country: str,
    config: dict | None = None,
) -> str | None:
    rules = config or load_candidate_quality_rules()
    normalized_city = normalize_text(city)
    if not normalized_city:
        return None

    for pattern in rules.get("blocked_city_regexes", []):
        if re.fullmatch(str(pattern), normalized_city):
            return "city contains an entity id rather than an observed city/locality"

    exact_values = {
        normalize_text(value)
        for value in _country_scoped_values(
            rules.get("blocked_city_exact_values", []),
            country=country,
        )
    }
    if normalized_city in exact_values:
        return "city is an administrative region, not an observed city/locality"

    blocked_tokens = [
        normalize_text(token)
        for token in rules.get("blocked_city_level_tokens", [])
    ]
    if any(_contains_token(normalized_city, token) for token in blocked_tokens):
        return "city contains an administrative level token, not an observed city/locality"
    return None


def validate_city_country_alignment(
    *,
    city: str,
    country: str,
    config: dict | None = None,
) -> str | None:
    rules = config or load_candidate_quality_rules()
    normalized_city = normalize_text(city)
    normalized_country = normalize_text(country)
    if not normalized_city or not normalized_country:
        return None

    for restricted_city, allowed_countries in rules.get(
        "country_restricted_city_names",
        {},
    ).items():
        if normalized_city != normalize_text(restricted_city):
            continue
        allowed = {normalize_text(item) for item in allowed_countries or []}
        if normalized_country not in allowed:
            allowed_label = ", ".join(str(item) for item in allowed_countries or [])
            return (
                "city-country mismatch: city appears to belong to "
                f"{allowed_label}, not {country}"
            )
    return None


def validate_evidence_entity_alignment(
    *,
    property_name: str,
    scene_type: str,
    source_urls: list[str],
    source_names: list[str] | None = None,
    config: dict | None = None,
) -> list[str]:
    rules = config or load_candidate_quality_rules()
    issues: list[str] = []
    normalized_name = normalize_text(property_name)
    source_names = source_names or []

    for url in source_urls:
        slug = _wiki_slug(url)
        if not slug:
            continue
        normalized_slug = normalize_text(slug)
        slug_is_same_entity = normalized_slug == normalized_name
        slug_is_location_only = (
            normalized_slug in {
                normalize_text(name)
                for name in rules.get("known_location_only_names", [])
            }
            or normalized_slug in {
                normalize_text(name)
                for name in rules.get("generic_topic_names", [])
            }
        )
        if slug_is_same_entity and slug_is_location_only and not _has_scene_token(
            property_name,
            scene_type,
            rules,
        ):
            issues.append(
                "source_url appears to cite a city/country/topic page as property evidence"
            )
        if slug_is_location_only and normalized_slug != normalized_name:
            issues.append(
                "source_url points to a location/topic page that does not identify the property"
            )

    for source_name in source_names:
        if normalize_text(source_name) in {"nominatim", "openstreetmap nominatim"}:
            issues.append("map geocode provider alone is not property value evidence")
    return _dedupe(issues)


def validate_geocode_precision_for_scene(
    *,
    scene_type: str,
    geocode_precision: str | None,
    config: dict | None = None,
) -> str | None:
    rules = config or load_candidate_quality_rules()
    normalized = normalize_text(geocode_precision or "")
    if not normalized:
        return "geocode_precision missing"
    if any(
        normalize_text(token) in normalized
        for token in rules.get("placeholder_tokens", [])
    ):
        return "geocode_precision contains placeholder or fixture token"

    blocked_tokens = [
        normalize_text(token)
        for token in rules.get("blocked_geocode_precision_tokens", [])
    ]
    if any(_contains_token(normalized, token) for token in blocked_tokens):
        return "geocode_precision is not property-level for the scene"

    allowed = [
        normalize_text(token)
        for token in rules.get("allowed_geocode_precision_tokens", {}).get(scene_type, [])
    ]
    if allowed and not any(token in normalized for token in allowed):
        return "geocode_precision is outside the allowed scene-specific property types"
    return None


def validate_scene_objective_evidence(
    *,
    scene_type: str,
    evidence_field_groups: list[str] | None = None,
    evidence_indicator_names: list[str] | None = None,
    evidence_types: list[str] | None = None,
    evidence_values: list[str] | None = None,
    evidence_assumption_notes: list[str] | None = None,
    config: dict | None = None,
) -> str | None:
    rules = config or load_candidate_quality_rules()
    scenes = scene_definitions()
    scene_rule = scenes.get(scene_type, {})
    accepted = {
        normalize_text(indicator)
        for indicator in [
            *scene_rule.get("primary_indicators", []),
            *scene_rule.get("fallback_metrics", []),
            *rules.get("objective_indicator_aliases", {}).get(scene_type, []),
        ]
        if indicator
    }
    if not accepted:
        return None
    provided = {
        normalize_text(indicator)
        for indicator in [
            *(evidence_field_groups or []),
            *(evidence_indicator_names or []),
        ]
        if indicator
    }
    if not (provided & accepted):
        return "scene objective evidence metric missing"

    objective_blocked_tokens = [
        normalize_text(token)
        for token in rules.get("objective_evidence_blocked_tokens", [])
    ]
    groups = evidence_field_groups or []
    indicators = evidence_indicator_names or []
    types = evidence_types or []
    values = evidence_values or []
    notes = evidence_assumption_notes or []
    max_len = max(len(groups), len(indicators), len(types), len(values), len(notes), 0)
    if max_len == 0:
        return None
    period_issue_message: str | None = None
    for index in range(max_len):
        group = groups[index] if index < len(groups) else ""
        indicator = indicators[index] if index < len(indicators) else ""
        if normalize_text(group) not in accepted and normalize_text(indicator) not in accepted:
            continue
        evidence_type = normalize_text(types[index] if index < len(types) else "")
        value = values[index] if index < len(values) else ""
        note = notes[index] if index < len(notes) else ""
        combined = normalize_text(f"{value} {note}")
        if evidence_type == "proxy":
            continue
        if any(token and token in combined for token in objective_blocked_tokens):
            continue
        period_issue = annual_metric_period_issue(
            group or indicator,
            value,
            context=note,
        )
        if period_issue is not None:
            period_issue_message = period_issue
            continue
        return None
    if period_issue_message is not None:
        return period_issue_message
    return "scene objective evidence metric missing or defaulted"


def validate_scene_default_values(
    *,
    annual_visits_est: float | None,
    assumption_note: str | None,
    config: dict | None = None,
) -> str | None:
    rules = config or load_candidate_quality_rules()
    normalized_note = normalize_text(assumption_note or "")
    default_tokens = [
        normalize_text(token)
        for token in rules.get("default_value_tokens", [])
    ]
    if normalized_note and any(token and token in normalized_note for token in default_tokens):
        return "scene model contains a default or fixture assumption"
    return None


def candidate_quality_review_item(
    packet: SitePacket,
    result: CandidateQualityResult | None = None,
) -> ReviewItem | None:
    quality = result or evaluate_packet_quality(packet)
    if quality.status != BLOCKED_QUALITY:
        return None
    entity = packet.entity
    source_url = str(packet.evidence[0].source_url) if packet.evidence else None
    issue_text = "; ".join(quality.issues)
    suggested_query = (
        f"{entity.property_name} {entity.city} {entity.country} "
        f"{entity.scene_type} official map annual report"
    )
    return ReviewItem(
        reason=f"候选质量门阻断：{issue_text}",
        next_action=(
            f"核验 {entity.country}/{entity.city} 的 {entity.property_name} 是否为具体物业点；"
            "补齐场景主指标证据、来源链接、日期和地图坐标；"
            "公开物业图片优先补齐，坏图用 Firecrawl 搜图换源，确实无图不阻断；"
            "若无物业级证据则移出候选池。"
        ),
        review_type="candidate_quality",
        severity="critical",
        gate_name="candidate_quality_gate",
        field_path="entity.property_name",
        blocking_surfaces=list(quality.blocking_surfaces),
        source_url=source_url,
        suggested_query=suggested_query,
    )


def packet_ready_for_surface(packet: SitePacket, surface: str) -> bool:
    if surface == "raw_pool":
        return packet.visibility.raw_pool
    if surface == "main_table":
        return packet.visibility.main_table_ready
    if surface == "map":
        return packet.visibility.map_ready
    if surface == "export":
        return packet.visibility.export_ready
    if surface == "review":
        return packet.visibility.review_required
    raise ValueError(f"unknown visibility surface: {surface}")


def filter_packets_for_surface(
    packets: list[SitePacket],
    surface: str,
    *,
    include_blocked_quality: bool = False,
) -> list[SitePacket]:
    if include_blocked_quality:
        return list(packets)
    return [packet for packet in packets if packet_ready_for_surface(packet, surface)]


def normalize_text(value: str | None) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[_/|,;:()\[\]{}]+", " ", text)
    text = re.sub(r"[^a-z0-9\s.-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_missing_primary_metric_issue(issue: str) -> bool:
    return normalize_text(issue).startswith("scene objective evidence metric missing")


def _wiki_slug(url: str) -> str | None:
    parsed = urlparse(str(url))
    netloc = parsed.netloc.casefold()
    if "wikipedia.org" not in netloc and "wikidata.org" not in netloc:
        return None
    path = unquote(parsed.path)
    if "/wiki/" not in path:
        return None
    return path.rsplit("/wiki/", 1)[-1].replace("_", " ").strip()


def _has_scene_token(text: str, scene_type: str, config: dict) -> bool:
    normalized = normalize_text(text)
    tokens = [
        normalize_text(token)
        for token in config.get("scene_title_tokens", {}).get(scene_type, [])
    ]
    return any(token and token in normalized for token in tokens)


def _contains_token(text: str, token: str) -> bool:
    if not token:
        return False
    if " " in token:
        return token in text
    return bool(re.search(rf"(^|\s){re.escape(token)}($|\s)", text))


def _country_scoped_values(config_value, *, country: str) -> list[str]:
    if not isinstance(config_value, dict):
        return list(config_value or [])

    values: list[str] = list(config_value.get("default", []) or [])
    normalized_country = normalize_text(country)
    for key, scoped_values in config_value.items():
        if key == "default":
            continue
        if normalize_text(key) == normalized_country:
            values.extend(scoped_values or [])
    return values


def _is_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(str(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _dedupe(issues: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for issue in issues:
        if issue in seen:
            continue
        seen.add(issue)
        result.append(issue)
    return result
