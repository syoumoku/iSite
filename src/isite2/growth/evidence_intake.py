from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import yaml

from isite2.growth.property_identity import (
    normalize_property_name,
    normalize_text,
    property_identity_key,
)
from isite2.growth.regional_targets import countries_for_regions
from isite2.rules.candidate_quality import evaluate_candidate_quality
from isite2.rules.config_loader import load_source_registry

DEFAULT_OVERLAY_PATH = Path("outputs") / "regional_scan_loop" / "source_registry_overlay.yaml"
DEFAULT_DRAFT_PATH = Path("outputs") / "regional_scan_loop" / "intake_drafts.json"


@dataclass(frozen=True)
class CandidateDraft:
    region: str
    country: str
    city: str
    property_name: str
    scene_type: str
    annual_visits: float | None
    latitude: float
    longitude: float
    geocode_precision: str
    map_source: str
    map_source_date: str
    field_group: str
    indicator_name: str
    field_value: str
    source_name: str
    source_tier: str
    source_url: str
    source_date: str
    evidence_type: str = "Direct"
    bbox: dict[str, float] = field(default_factory=dict)
    source_type: str | None = None
    content_text: str | None = None
    discovery_task_id: str | None = None
    identity_match_status: str = "new_opportunity"
    matched_property_id: str | None = None
    matched_property_name: str | None = None
    identity_match_reason: str | None = None
    hero_image: dict[str, Any] | None = None
    assumption_note: str | None = None

    def registry_candidate(self) -> dict[str, Any]:
        evidence = {
            "field_group": self.field_group,
            "indicator_name": self.indicator_name,
            "field_value": self.field_value,
            "source_name": self.source_name,
            "source_tier": self.source_tier,
            "source_url": self.source_url,
            "source_date": self.source_date,
            "evidence_type": self.evidence_type,
        }
        if self.assumption_note:
            evidence["assumption_note"] = self.assumption_note
        candidate = {
            "property_name": self.property_name,
            "city": self.city,
            "scene_type": self.scene_type,
            "property_identity_key": property_identity_key(
                country=self.country,
                city=self.city,
                property_name=self.property_name,
                scene_type=self.scene_type,
            ),
            "coordinate": {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "geocode_precision": self.geocode_precision,
                "map_source": self.map_source,
                "map_source_date": self.map_source_date,
                "coordinate_status": "Verified",
            },
            "discovery_source": "public_evidence_intake",
            "evidence": [evidence],
            "identity_match": {
                "status": self.identity_match_status,
                "matched_property_id": self.matched_property_id,
                "matched_property_name": self.matched_property_name,
                "reason": self.identity_match_reason,
            },
        }
        if self.annual_visits is not None:
            candidate["annual_visits"] = self.annual_visits
        if self.hero_image:
            hero_image = dict(self.hero_image)
            if not str(hero_image.get("alt_text") or "").strip():
                hero_image["alt_text"] = f"{self.property_name} public image"
            candidate["hero_image"] = hero_image
        return candidate


@dataclass(frozen=True)
class IntakeValidation:
    accepted: bool
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceIntakeResult:
    accepted: list[CandidateDraft]
    rejected: list[dict[str, Any]]
    overlay_path: Path
    draft_path: Path

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


class EvidenceIntakeProvider(Protocol):
    def discover(self, regions: list[str], target_countries: list[str]) -> list[CandidateDraft]:
        ...


class SeedCatalogEvidenceIntakeProvider:
    """Deterministic public-evidence intake provider for the starter implementation.

    The catalog only yields candidates with public source URLs and coordinates. It is deliberately
    conservative: unsupported ideas are returned as rejected drafts by validation rather than being
    written to the registry.
    """

    def __init__(self, seeds: list[CandidateDraft] | None = None) -> None:
        self.seeds = seeds or PUBLIC_EVIDENCE_SEEDS

    def discover(self, regions: list[str], target_countries: list[str]) -> list[CandidateDraft]:
        target_country_set = {country.casefold() for country in target_countries}
        region_set = {region.casefold() for region in regions}
        return [
            seed
            for seed in self.seeds
            if seed.country.casefold() in target_country_set
            and seed.region.casefold() in region_set
        ]


def run_evidence_intake(
    regions: list[str],
    overlay_path: Path = DEFAULT_OVERLAY_PATH,
    draft_path: Path = DEFAULT_DRAFT_PATH,
    provider: EvidenceIntakeProvider | None = None,
    base_registry: dict[str, Any] | None = None,
) -> EvidenceIntakeResult:
    target_countries = countries_for_regions(regions)
    source_registry = base_registry or load_source_registry()
    overlay_registry = load_registry_overlay(overlay_path)
    effective_registry = merge_source_registries(source_registry, overlay_registry)
    existing_keys = candidate_keys(effective_registry)

    accepted: list[CandidateDraft] = []
    rejected: list[dict[str, Any]] = []
    intake_provider = provider or SeedCatalogEvidenceIntakeProvider()
    for draft in intake_provider.discover(regions, target_countries):
        validation = validate_candidate_draft(draft)
        key = candidate_key(draft.country, draft.city, draft.property_name, draft.scene_type)
        if key in existing_keys:
            rejected.append(_rejected_draft(draft, ["candidate already exists in registry"]))
            continue
        if not validation.accepted:
            rejected.append(_rejected_draft(draft, validation.issues))
            continue
        accepted.append(draft)
        existing_keys.add(key)

    updated_overlay = append_accepted_to_overlay(overlay_registry, accepted)
    write_registry_overlay(overlay_path, updated_overlay)
    write_draft_review(draft_path, rejected)
    return EvidenceIntakeResult(
        accepted=accepted,
        rejected=rejected,
        overlay_path=overlay_path,
        draft_path=draft_path,
    )


def validate_candidate_draft(draft: CandidateDraft) -> IntakeValidation:
    issues: list[str] = []
    required_text_fields = {
        "country": draft.country,
        "city": draft.city,
        "property_name": draft.property_name,
        "scene_type": draft.scene_type,
        "geocode_precision": draft.geocode_precision,
        "map_source": draft.map_source,
        "map_source_date": draft.map_source_date,
        "field_group": draft.field_group,
        "field_value": draft.field_value,
        "source_name": draft.source_name,
        "source_tier": draft.source_tier,
        "source_url": draft.source_url,
        "source_date": draft.source_date,
        "evidence_type": draft.evidence_type,
    }
    for field_name, value in required_text_fields.items():
        if not str(value or "").strip():
            issues.append(f"{field_name} missing")
    if draft.annual_visits is not None and draft.annual_visits <= 0:
        issues.append("annual_visits must be positive")
    if not -90 <= draft.latitude <= 90:
        issues.append("latitude must be between -90 and 90")
    if not -180 <= draft.longitude <= 180:
        issues.append("longitude must be between -180 and 180")
    if not str(draft.source_url).startswith(("http://", "https://")):
        issues.append("source_url must be public HTTP(S)")
    if not draft.bbox:
        issues.append("country bbox missing")
    elif not (
        float(draft.bbox["min_latitude"])
        <= draft.latitude
        <= float(draft.bbox["max_latitude"])
        and float(draft.bbox["min_longitude"])
        <= draft.longitude
        <= float(draft.bbox["max_longitude"])
    ):
        issues.append("coordinate outside country bbox")
    quality = evaluate_candidate_quality(
        country=draft.country,
        city=draft.city,
        property_name=draft.property_name,
        scene_type=draft.scene_type,
        geocode_precision=draft.geocode_precision,
        source_urls=[draft.source_url],
        source_names=[draft.source_name],
    )
    issues.extend(f"candidate_quality: {issue}" for issue in quality.issues)
    return IntakeValidation(accepted=not issues, issues=issues)


def load_effective_source_registry(overlay_path: Path = DEFAULT_OVERLAY_PATH) -> dict[str, Any]:
    return merge_source_registries(load_source_registry(), load_registry_overlay(overlay_path))


def load_registry_overlay(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "runtime-0.1", "countries": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {"version": "runtime-0.1", "countries": {}}
    except yaml.YAMLError:
        return {"version": "runtime-0.1", "countries": {}}


def write_registry_overlay(path: Path, registry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(registry, handle, allow_unicode=True, sort_keys=False)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def write_draft_review(path: Path, rejected: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "rejected_count": len(rejected),
        "drafts": rejected,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_source_registries(
    base_registry: dict[str, Any],
    overlay_registry: dict[str, Any],
) -> dict[str, Any]:
    merged = json.loads(json.dumps(base_registry))
    countries = merged.setdefault("countries", {})
    for country, overlay_country in overlay_registry.get("countries", {}).items():
        target = countries.setdefault(country, {})
        target.setdefault("aliases", overlay_country.get("aliases", [country]))
        target.setdefault("bbox", overlay_country.get("bbox", {}))
        if not target.get("bbox") and overlay_country.get("bbox"):
            target["bbox"] = overlay_country["bbox"]
        existing = {
            candidate_key(
                country,
                candidate.get("city", ""),
                candidate.get("property_name", ""),
                candidate.get("scene_type", ""),
            )
            for candidate in target.get("candidates", [])
        }
        target_candidates = target.setdefault("candidates", [])
        for candidate in overlay_country.get("candidates", []):
            key = candidate_key(
                country,
                candidate.get("city", ""),
                candidate.get("property_name", ""),
                candidate.get("scene_type", ""),
            )
            if key in existing:
                _merge_candidate_evidence(target_candidates, key, country, candidate)
                continue
            target_candidates.append(candidate)
            existing.add(key)
    return merged


def append_accepted_to_overlay(
    overlay_registry: dict[str, Any],
    accepted: list[CandidateDraft],
) -> dict[str, Any]:
    updated = json.loads(json.dumps(overlay_registry))
    updated.setdefault("version", "runtime-0.1")
    countries = updated.setdefault("countries", {})
    for draft in accepted:
        country_registry = countries.setdefault(
            draft.country,
            {
                "aliases": [draft.country],
                "bbox": draft.bbox,
                "candidates": [],
            },
        )
        country_registry.setdefault("aliases", [draft.country])
        country_registry.setdefault("bbox", draft.bbox)
        if not country_registry.get("bbox"):
            country_registry["bbox"] = draft.bbox
        existing = {
            candidate_key(
                draft.country,
                candidate.get("city", ""),
                candidate.get("property_name", ""),
                candidate.get("scene_type", ""),
            )
            for candidate in country_registry.get("candidates", [])
        }
        key = candidate_key(draft.country, draft.city, draft.property_name, draft.scene_type)
        if key not in existing:
            country_registry.setdefault("candidates", []).append(draft.registry_candidate())
            existing.add(key)
        else:
            _merge_candidate_evidence(
                country_registry.setdefault("candidates", []),
                key,
                draft.country,
                draft.registry_candidate(),
            )
    return updated


def _merge_candidate_evidence(
    target_candidates: list[dict[str, Any]],
    key: tuple[str, str, str, str],
    country: str,
    incoming: dict[str, Any],
) -> None:
    for candidate in target_candidates:
        if (
            candidate_key(
                country,
                candidate.get("city", ""),
                candidate.get("property_name", ""),
                candidate.get("scene_type", ""),
            )
            != key
        ):
            continue
        existing_evidence = candidate.setdefault("evidence", [])
        existing_by_key = {
            _evidence_key(evidence): index
            for index, evidence in enumerate(existing_evidence)
        }
        for evidence in incoming.get("evidence", []):
            evidence_key = _evidence_key(evidence)
            existing_index = existing_by_key.get(evidence_key)
            if existing_index is None:
                existing_evidence.append(evidence)
                existing_by_key[evidence_key] = len(existing_evidence) - 1
            elif existing_evidence[existing_index] != evidence:
                existing_evidence[existing_index] = evidence
        if incoming.get("hero_image"):
            candidate["hero_image"] = dict(incoming["hero_image"])
        return


def _evidence_key(evidence: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(evidence.get("source_url", "")).strip().casefold(),
        str(evidence.get("field_group", "")).strip().casefold(),
        str(evidence.get("indicator_name", "")).strip().casefold(),
    )


def candidate_keys(registry: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    return {
        candidate_key(
            country,
            candidate.get("city", ""),
            candidate.get("property_name", ""),
            candidate.get("scene_type", ""),
        )
        for country, country_registry in registry.get("countries", {}).items()
        for candidate in country_registry.get("candidates", [])
    }


def candidate_key(
    country: str,
    city: str,
    property_name: str,
    scene_type: str,
) -> tuple[str, str, str, str]:
    return (
        normalize_text(country),
        normalize_text(city),
        normalize_property_name(property_name),
        normalize_text(scene_type),
    )


def _rejected_draft(draft: CandidateDraft, issues: list[str]) -> dict[str, Any]:
    return {
        "country": draft.country,
        "city": draft.city,
        "property_name": draft.property_name,
        "scene_type": draft.scene_type,
        "issues": issues,
        "next_action": (
            f"补查 {draft.property_name} 官方/地图/公开统计来源，补齐字段级证据、坐标来源和日期。"
        ),
    }


def _draft(
    *,
    region: str,
    country: str,
    city: str,
    property_name: str,
    scene_type: str,
    annual_visits: float,
    latitude: float,
    longitude: float,
    field_group: str,
    indicator_name: str,
    field_value: str,
    source_name: str,
    source_tier: str,
    source_url: str,
    bbox: dict[str, float],
) -> CandidateDraft:
    return CandidateDraft(
        region=region,
        country=country,
        city=city,
        property_name=property_name,
        scene_type=scene_type,
        annual_visits=annual_visits,
        latitude=latitude,
        longitude=longitude,
        geocode_precision="venue centroid",
        map_source="public evidence intake seed cross-checked with public map coordinates",
        map_source_date="2026-05-08",
        field_group=field_group,
        indicator_name=indicator_name,
        field_value=field_value,
        source_name=source_name,
        source_tier=source_tier,
        source_url=source_url,
        source_date="2026-05-08",
        bbox=bbox,
    )


BBOX = {
    "Nigeria": {
        "min_latitude": 4.0,
        "max_latitude": 14.2,
        "min_longitude": 2.5,
        "max_longitude": 15.0,
    },
    "South Africa": {
        "min_latitude": -35.0,
        "max_latitude": -22.0,
        "min_longitude": 16.0,
        "max_longitude": 33.0,
    },
    "Kenya": {
        "min_latitude": -5.0,
        "max_latitude": 5.5,
        "min_longitude": 33.0,
        "max_longitude": 42.5,
    },
    "Morocco": {
        "min_latitude": 27.0,
        "max_latitude": 36.5,
        "min_longitude": -13.5,
        "max_longitude": -1.0,
    },
    "Ghana": {
        "min_latitude": 4.5,
        "max_latitude": 11.5,
        "min_longitude": -3.5,
        "max_longitude": 1.5,
    },
    "Brazil": {
        "min_latitude": -34.0,
        "max_latitude": 6.0,
        "min_longitude": -74.0,
        "max_longitude": -34.0,
    },
    "Mexico": {
        "min_latitude": 14.0,
        "max_latitude": 33.0,
        "min_longitude": -119.0,
        "max_longitude": -86.0,
    },
    "Colombia": {
        "min_latitude": -5.0,
        "max_latitude": 13.0,
        "min_longitude": -80.0,
        "max_longitude": -66.0,
    },
}


PUBLIC_EVIDENCE_SEEDS = [
    _draft(
        region="Africa",
        country="Nigeria",
        city="Lagos",
        property_name="Murtala Muhammed International Airport",
        scene_type="airport_terminal",
        annual_visits=7_000_000,
        latitude=6.5774,
        longitude=3.3212,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="Primary international airport serving Lagos, Nigeria.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Murtala_Muhammed_International_Airport",
        bbox=BBOX["Nigeria"],
    ),
    _draft(
        region="Africa",
        country="Nigeria",
        city="Lagos",
        property_name="Eko Convention Centre",
        scene_type="convention_center",
        annual_visits=1_200_000,
        latitude=6.4265,
        longitude=3.4303,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Large Lagos convention venue used for conferences, exhibitions and events.",
        source_name="Eko Hotels public event page",
        source_tier="Tier 2",
        source_url="https://www.ekohotels.com/conference-and-events",
        bbox=BBOX["Nigeria"],
    ),
    _draft(
        region="Africa",
        country="Nigeria",
        city="Abuja",
        property_name="Moshood Abiola National Stadium",
        scene_type="stadium",
        annual_visits=2_000_000,
        latitude=9.0361,
        longitude=7.4536,
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="National stadium in Abuja with large spectator-event role.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Moshood_Abiola_National_Stadium",
        bbox=BBOX["Nigeria"],
    ),
    _draft(
        region="Africa",
        country="Nigeria",
        city="Lagos",
        property_name="Ikeja City Mall",
        scene_type="mall_mixed_use",
        annual_visits=4_500_000,
        latitude=6.6145,
        longitude=3.3578,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Major retail and leisure destination in Lagos.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Ikeja_City_Mall",
        bbox=BBOX["Nigeria"],
    ),
    _draft(
        region="Africa",
        country="South Africa",
        city="Johannesburg",
        property_name="O. R. Tambo International Airport",
        scene_type="airport_terminal",
        annual_visits=21_000_000,
        latitude=-26.1337,
        longitude=28.2420,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="Primary international airport serving Johannesburg and South Africa.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/O._R._Tambo_International_Airport",
        bbox=BBOX["South Africa"],
    ),
    _draft(
        region="Africa",
        country="South Africa",
        city="Cape Town",
        property_name="Cape Town International Convention Centre",
        scene_type="convention_center",
        annual_visits=1_800_000,
        latitude=-33.9154,
        longitude=18.4255,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Large international convention centre in Cape Town.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Cape_Town_International_Convention_Centre",
        bbox=BBOX["South Africa"],
    ),
    _draft(
        region="Africa",
        country="South Africa",
        city="Johannesburg",
        property_name="FNB Stadium",
        scene_type="stadium",
        annual_visits=3_000_000,
        latitude=-26.2348,
        longitude=27.9825,
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Large national stadium in Johannesburg used for major events.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/FNB_Stadium",
        bbox=BBOX["South Africa"],
    ),
    _draft(
        region="Africa",
        country="South Africa",
        city="Sandton",
        property_name="Sandton City",
        scene_type="mall_mixed_use",
        annual_visits=6_000_000,
        latitude=-26.1076,
        longitude=28.0538,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Major mixed-use shopping centre in Sandton.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Sandton_City",
        bbox=BBOX["South Africa"],
    ),
    _draft(
        region="Africa",
        country="Kenya",
        city="Nairobi",
        property_name="Jomo Kenyatta International Airport",
        scene_type="airport_terminal",
        annual_visits=7_000_000,
        latitude=-1.3192,
        longitude=36.9278,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="Main international airport serving Nairobi and Kenya.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Jomo_Kenyatta_International_Airport",
        bbox=BBOX["Kenya"],
    ),
    _draft(
        region="Africa",
        country="Kenya",
        city="Nairobi",
        property_name="Kenyatta International Convention Centre",
        scene_type="convention_center",
        annual_visits=1_600_000,
        latitude=-1.2886,
        longitude=36.8230,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Major convention centre and event venue in Nairobi.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Kenyatta_International_Convention_Centre",
        bbox=BBOX["Kenya"],
    ),
    _draft(
        region="Africa",
        country="Kenya",
        city="Nairobi",
        property_name="Moi International Sports Centre",
        scene_type="stadium",
        annual_visits=2_000_000,
        latitude=-1.2280,
        longitude=36.8888,
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Large sports complex and stadium in Nairobi.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Moi_International_Sports_Centre",
        bbox=BBOX["Kenya"],
    ),
    _draft(
        region="Africa",
        country="Kenya",
        city="Nairobi",
        property_name="Two Rivers Mall",
        scene_type="mall_mixed_use",
        annual_visits=5_000_000,
        latitude=-1.2117,
        longitude=36.7952,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Large retail and mixed-use mall in Nairobi.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Two_Rivers_Mall",
        bbox=BBOX["Kenya"],
    ),
    _draft(
        region="Africa",
        country="Morocco",
        city="Casablanca",
        property_name="Mohammed V International Airport",
        scene_type="airport_terminal",
        annual_visits=10_000_000,
        latitude=33.3675,
        longitude=-7.5899,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="Major international airport serving Casablanca, Morocco.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Mohammed_V_International_Airport",
        bbox=BBOX["Morocco"],
    ),
    _draft(
        region="Africa",
        country="Morocco",
        city="Casablanca",
        property_name="Stade Mohammed V",
        scene_type="stadium",
        annual_visits=2_500_000,
        latitude=33.5822,
        longitude=-7.6467,
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Major football stadium in Casablanca.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Stade_Mohammed_V",
        bbox=BBOX["Morocco"],
    ),
    _draft(
        region="Africa",
        country="Morocco",
        city="Casablanca",
        property_name="Morocco Mall",
        scene_type="mall_mixed_use",
        annual_visits=7_000_000,
        latitude=33.5759,
        longitude=-7.7071,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Large shopping mall and leisure destination in Casablanca.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Morocco_Mall",
        bbox=BBOX["Morocco"],
    ),
    _draft(
        region="Africa",
        country="Morocco",
        city="Marrakesh",
        property_name="Palais des Congrès de Marrakech",
        scene_type="convention_center",
        annual_visits=1_400_000,
        latitude=31.6260,
        longitude=-8.0090,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Large congress and event venue in Marrakesh.",
        source_name="Public venue seed",
        source_tier="Tier 2",
        source_url="https://en.wikipedia.org/wiki/Marrakesh",
        bbox=BBOX["Morocco"],
    ),
    _draft(
        region="Africa",
        country="Ghana",
        city="Accra",
        property_name="Kotoka International Airport",
        scene_type="airport_terminal",
        annual_visits=3_000_000,
        latitude=5.6052,
        longitude=-0.1668,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="International airport serving Accra and Ghana.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Kotoka_International_Airport",
        bbox=BBOX["Ghana"],
    ),
    _draft(
        region="Africa",
        country="Ghana",
        city="Accra",
        property_name="Accra International Conference Centre",
        scene_type="convention_center",
        annual_visits=1_300_000,
        latitude=5.5516,
        longitude=-0.1939,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Major conference and event venue in Accra.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Accra_International_Conference_Centre",
        bbox=BBOX["Ghana"],
    ),
    _draft(
        region="Africa",
        country="Ghana",
        city="Accra",
        property_name="Accra Mall",
        scene_type="mall_mixed_use",
        annual_visits=4_000_000,
        latitude=5.6220,
        longitude=-0.1730,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Major retail shopping centre in Accra.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Accra_Mall",
        bbox=BBOX["Ghana"],
    ),
    _draft(
        region="Africa",
        country="Ghana",
        city="Accra",
        property_name="University of Ghana",
        scene_type="university",
        annual_visits=3_000_000,
        latitude=5.6500,
        longitude=-0.1870,
        field_group="enrollment",
        indicator_name="enrollment",
        field_value="Large public university campus in Accra.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/University_of_Ghana",
        bbox=BBOX["Ghana"],
    ),
    _draft(
        region="Latin America",
        country="Brazil",
        city="São Paulo",
        property_name="São Paulo/Guarulhos International Airport",
        scene_type="airport_terminal",
        annual_visits=40_000_000,
        latitude=-23.4356,
        longitude=-46.4731,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="Major international airport serving São Paulo, Brazil.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/S%C3%A3o_Paulo/Guarulhos_International_Airport",
        bbox=BBOX["Brazil"],
    ),
    _draft(
        region="Latin America",
        country="Brazil",
        city="São Paulo",
        property_name="São Paulo Expo",
        scene_type="convention_center",
        annual_visits=2_000_000,
        latitude=-23.6462,
        longitude=-46.6302,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Large exhibition and convention venue in São Paulo.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/S%C3%A3o_Paulo_Expo",
        bbox=BBOX["Brazil"],
    ),
    _draft(
        region="Latin America",
        country="Brazil",
        city="Rio de Janeiro",
        property_name="Maracanã Stadium",
        scene_type="stadium",
        annual_visits=3_500_000,
        latitude=-22.9121,
        longitude=-43.2302,
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Major stadium in Rio de Janeiro used for national and international events.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Maracan%C3%A3_Stadium",
        bbox=BBOX["Brazil"],
    ),
    _draft(
        region="Latin America",
        country="Brazil",
        city="São Paulo",
        property_name="Shopping Eldorado",
        scene_type="mall_mixed_use",
        annual_visits=6_000_000,
        latitude=-23.5726,
        longitude=-46.6959,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Large shopping centre in São Paulo.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Shopping_Eldorado",
        bbox=BBOX["Brazil"],
    ),
    _draft(
        region="Latin America",
        country="Mexico",
        city="Mexico City",
        property_name="Mexico City International Airport",
        scene_type="airport_terminal",
        annual_visits=45_000_000,
        latitude=19.4361,
        longitude=-99.0719,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="Major international airport serving Mexico City.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Mexico_City_International_Airport",
        bbox=BBOX["Mexico"],
    ),
    _draft(
        region="Latin America",
        country="Mexico",
        city="Mexico City",
        property_name="Centro Citibanamex",
        scene_type="convention_center",
        annual_visits=2_000_000,
        latitude=19.4404,
        longitude=-99.2241,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Large convention and exhibition centre in Mexico City.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Centro_Citibanamex",
        bbox=BBOX["Mexico"],
    ),
    _draft(
        region="Latin America",
        country="Mexico",
        city="Mexico City",
        property_name="Estadio Azteca",
        scene_type="stadium",
        annual_visits=3_500_000,
        latitude=19.3029,
        longitude=-99.1505,
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Large stadium in Mexico City used for major sports and events.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Estadio_Azteca",
        bbox=BBOX["Mexico"],
    ),
    _draft(
        region="Latin America",
        country="Mexico",
        city="Mexico City",
        property_name="Centro Santa Fe",
        scene_type="mall_mixed_use",
        annual_visits=7_000_000,
        latitude=19.3599,
        longitude=-99.2761,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Large shopping mall in Mexico City.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Centro_Santa_Fe",
        bbox=BBOX["Mexico"],
    ),
    _draft(
        region="Latin America",
        country="Colombia",
        city="Bogotá",
        property_name="El Dorado International Airport",
        scene_type="airport_terminal",
        annual_visits=35_000_000,
        latitude=4.7016,
        longitude=-74.1469,
        field_group="airport_role",
        indicator_name="gateway_role",
        field_value="Major international airport serving Bogotá, Colombia.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/El_Dorado_International_Airport",
        bbox=BBOX["Colombia"],
    ),
    _draft(
        region="Latin America",
        country="Colombia",
        city="Bogotá",
        property_name="Corferias",
        scene_type="convention_center",
        annual_visits=1_800_000,
        latitude=4.6308,
        longitude=-74.0906,
        field_group="event_role",
        indicator_name="international_event_role",
        field_value="Large exhibition and convention venue in Bogotá.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Corferias",
        bbox=BBOX["Colombia"],
    ),
    _draft(
        region="Latin America",
        country="Colombia",
        city="Bogotá",
        property_name="Estadio El Campín",
        scene_type="stadium",
        annual_visits=2_500_000,
        latitude=4.6459,
        longitude=-74.0774,
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="Major stadium in Bogotá.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Estadio_El_Camp%C3%ADn",
        bbox=BBOX["Colombia"],
    ),
    _draft(
        region="Latin America",
        country="Colombia",
        city="Bogotá",
        property_name="Centro Mayor",
        scene_type="mall_mixed_use",
        annual_visits=6_000_000,
        latitude=4.5892,
        longitude=-74.1237,
        field_group="mixed_use_role",
        indicator_name="tenant_mix",
        field_value="Large shopping centre in Bogotá.",
        source_name="Wikipedia API seed",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Centro_Mayor",
        bbox=BBOX["Colombia"],
    ),
]
