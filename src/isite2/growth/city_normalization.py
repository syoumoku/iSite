from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Any

from isite2.domain.models import CityAssignment
from isite2.growth.property_identity import normalize_text, property_identity_key
from isite2.rules.config_loader import load_city_admin_sources


def canonicalize_city(
    *,
    country: str,
    source_city: str,
    property_id: str | None = None,
    property_name: str | None = None,
    scene_type: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    config: dict[str, Any] | None = None,
) -> CityAssignment:
    payload = config or load_city_admin_sources()
    country_config = (payload.get("countries") or {}).get(country)
    if not isinstance(country_config, dict):
        return _unmapped(source_city, payload)

    property_override = _resolve_property_override(
        country_config,
        property_id=property_id,
        property_name=property_name,
        scene_type=scene_type,
    )
    if property_override is not None:
        if property_override.get("status") != "matched":
            return _assignment(
                source_city=source_city,
                payload=payload,
                country_config=country_config,
                mapping_status="review_required",
                mapping_method=str(property_override["reason"]),
            )
        city = next(
            (
                row
                for row in country_config.get("cities") or []
                if str(row.get("city_id") or "")
                == str(property_override["city_id"])
            ),
            None,
        )
        if city is None:
            return _assignment(
                source_city=source_city,
                payload=payload,
                country_config=country_config,
                mapping_status="review_required",
                mapping_method="property_override_city_missing",
            )
        if not _matches_override_reference(
            city,
            latitude=latitude,
            longitude=longitude,
            max_distance_km=float(property_override.get("max_distance_km") or 0),
        ):
            return _assignment(
                source_city=source_city,
                payload=payload,
                country_config=country_config,
                mapping_status="review_required",
                mapping_method="property_override_coordinate_conflict",
            )
        return _assignment(
            source_city=source_city,
            payload=payload,
            country_config=country_config,
            city=city,
            mapping_status="verified",
            mapping_method=str(property_override.get("mapping_method") or "property_override"),
        )

    normalized_source = normalize_text(source_city)
    name_matches: list[dict[str, Any]] = []
    reference_matches: list[dict[str, Any]] = []
    coordinate_matches: list[dict[str, Any]] = []
    for city in country_config.get("cities") or []:
        canonical_aliases = [city.get("canonical_name"), *(city.get("aliases") or [])]
        locality_aliases = list(city.get("localities") or [])
        normalized_canonical = {
            normalize_text(value) for value in canonical_aliases if str(value or "").strip()
        }
        normalized_localities = {
            normalize_text(value) for value in locality_aliases if str(value or "").strip()
        }
        name_match = (
            normalized_source in normalized_canonical
            or normalized_source in normalized_localities
        )
        if name_match:
            name_matches.append(city)
            if _matches_reference_point(city, latitude, longitude):
                reference_matches.append(city)
        if _contains(city.get("bounds"), latitude, longitude):
            coordinate_matches.append(city)

    required_reference_matches = [
        city for city in name_matches if city.get("reference_match_required")
    ]
    if required_reference_matches:
        eligible_name_matches = [
            city
            for city in name_matches
            if not city.get("reference_match_required") or city in reference_matches
        ]
        if not eligible_name_matches:
            return _assignment(
                source_city=source_city,
                payload=payload,
                country_config=country_config,
                mapping_status="review_required",
                mapping_method="official_name_coordinate_conflict",
            )
    else:
        eligible_name_matches = name_matches

    name_match = _only(eligible_name_matches)
    coordinate_match = _only(coordinate_matches)
    if (
        name_match
        and coordinate_match
        and name_match.get("city_id") != coordinate_match.get("city_id")
    ):
        return _assignment(
            source_city=source_city,
            payload=payload,
            country_config=country_config,
            mapping_status="review_required",
            mapping_method="coordinate_name_conflict",
        )
    if len(eligible_name_matches) > 1 or len(coordinate_matches) > 1:
        return _assignment(
            source_city=source_city,
            payload=payload,
            country_config=country_config,
            mapping_status="review_required",
            mapping_method="ambiguous_official_mapping",
        )

    city = coordinate_match or name_match
    if city is None:
        unresolved_reason = str(
            (country_config.get("unresolved_localities") or {}).get(normalized_source)
            or "no_official_match"
        )
        review_reasons = {
            "official_name_coordinate_conflict",
            "ambiguous_official_location_without_reference",
            "ambiguous_official_location",
            "coordinate_required",
        }
        return _assignment(
            source_city=source_city,
            payload=payload,
            country_config=country_config,
            mapping_status=(
                "review_required" if unresolved_reason in review_reasons else "unmapped"
            ),
            mapping_method=unresolved_reason,
        )
    canonical_name = str(city.get("canonical_name") or "").strip()
    normalized_canonical_aliases = {
        normalize_text(value)
        for value in [canonical_name, *(city.get("aliases") or [])]
        if str(value or "").strip()
    }
    normalized_official_exact_names = {
        normalize_text(value)
        for value in city.get("official_exact_names") or []
        if str(value or "").strip()
    }
    if not normalized_official_exact_names:
        normalized_official_exact_names = normalized_canonical_aliases
    if city.get("reference_match_required"):
        method = (
            "reference_exact_coordinate"
            if normalized_source in normalized_official_exact_names
            else "reference_alias_coordinate"
        )
    elif city.get("official_match_method") in {
        "official_name_unique_no_reference",
        "official_alias_unique_no_reference",
    }:
        method = (
            "official_name_unique_no_reference"
            if normalized_source in normalized_official_exact_names
            else "official_alias_unique_no_reference"
        )
    elif coordinate_match is not None and name_match is None:
        method = "coordinate_boundary"
    elif normalized_source in normalized_canonical_aliases:
        method = "canonical_exact"
    else:
        method = "official_crosswalk"
    return _assignment(
        source_city=source_city,
        payload=payload,
        country_config=country_config,
        city=city,
        mapping_status="verified",
        mapping_method=method,
    )


def build_city_normalization_plan(
    properties: list[dict[str, Any]],
    *,
    country: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assignments: list[dict[str, Any]] = []
    before = Counter()
    after = Counter()
    statuses = Counter()
    identity_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in properties:
        source_city = str(row.get("source_city") or row.get("city") or "").strip()
        assignment = canonicalize_city(
            country=country,
            source_city=source_city,
            property_id=str(row.get("property_id") or row.get("id") or ""),
            property_name=str(
                row.get("property_name") or row.get("canonical_name") or ""
            ),
            scene_type=str(row.get("scene_type") or ""),
            latitude=_optional_float(row.get("latitude")),
            longitude=_optional_float(row.get("longitude")),
            config=config,
        )
        canonical_city = (
            assignment.canonical_city
            if assignment.mapping_status == "verified"
            else str(row.get("city") or source_city)
        )
        identity_key = property_identity_key(
            country=country,
            city=canonical_city,
            city_id=assignment.city_id,
            property_name=str(row.get("property_name") or row.get("canonical_name") or ""),
            scene_type=str(row.get("scene_type") or ""),
        )
        planned = {
            "property_id": str(row.get("property_id") or row.get("id") or ""),
            "property_name": str(
                row.get("property_name") or row.get("canonical_name") or ""
            ),
            "scene_type": str(row.get("scene_type") or ""),
            "before_city": str(row.get("city") or ""),
            "after_city": canonical_city,
            "property_identity_key": identity_key,
            "city_assignment": assignment.model_dump(mode="json"),
        }
        assignments.append(planned)
        before[planned["before_city"]] += 1
        after[canonical_city] += 1
        statuses[assignment.mapping_status] += 1
        identity_groups[identity_key].append(planned)

    collisions = [
        {
            "property_identity_key": key,
            "properties": rows,
        }
        for key, rows in identity_groups.items()
        if len(rows) > 1
    ]
    unresolved = [
        row
        for row in assignments
        if row["city_assignment"]["mapping_status"] != "verified"
    ]
    return {
        "country": country,
        "property_count": len(assignments),
        "before_city_count": len(before),
        "after_city_count": len(after),
        "before_city_distribution": dict(sorted(before.items())),
        "after_city_distribution": dict(sorted(after.items())),
        "mapping_status_counts": dict(sorted(statuses.items())),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "identity_collision_count": len(collisions),
        "identity_collisions": collisions,
        "assignments": assignments,
    }


def apply_verified_city_assignments_to_overlay(
    overlay: dict[str, Any],
    *,
    country: str,
    config: dict[str, Any],
    verified_only: bool,
) -> dict[str, int]:
    country_registry = (overlay.get("countries") or {}).get(country) or {}
    updates = 0
    audited = 0
    skipped = 0
    for candidate in country_registry.get("candidates") or []:
        existing_assignment = candidate.get("city_assignment") or {}
        source_city = str(
            existing_assignment.get("source_city")
            or candidate.get("source_city")
            or candidate.get("city")
            or ""
        )
        coordinate = candidate.get("coordinate") or {}
        one = build_city_normalization_plan(
            [
                {
                    "id": candidate.get("property_id") or candidate.get("property_name"),
                    "canonical_name": candidate.get("property_name"),
                    "city": candidate.get("city"),
                    "source_city": source_city,
                    "scene_type": candidate.get("scene_type"),
                    "latitude": coordinate.get("latitude"),
                    "longitude": coordinate.get("longitude"),
                }
            ],
            country=country,
            config=config,
        )["assignments"][0]
        assignment = one["city_assignment"]
        if assignment["mapping_status"] != "verified":
            if not verified_only:
                raise ValueError(
                    f"Overlay candidate has unresolved city: {candidate.get('property_name')}"
                )
            candidate["source_city"] = source_city
            candidate["city"] = source_city
            candidate.pop("city_id", None)
            candidate.pop("property_identity_key", None)
            candidate["city_assignment"] = assignment
            audited += 1
            skipped += 1
            continue
        candidate["source_city"] = source_city
        candidate["city"] = one["after_city"]
        candidate["city_id"] = assignment["city_id"]
        candidate["city_assignment"] = assignment
        candidate["property_identity_key"] = property_identity_key(
            country=country,
            city=one["after_city"],
            city_id=assignment["city_id"],
            property_name=str(candidate.get("property_name") or ""),
            scene_type=str(candidate.get("scene_type") or ""),
        )
        updates += 1
        audited += 1
    return {"updated": updates, "skipped": skipped, "audited": audited}


def _assignment(
    *,
    source_city: str,
    payload: dict[str, Any],
    country_config: dict[str, Any],
    mapping_status: str,
    mapping_method: str,
    city: dict[str, Any] | None = None,
) -> CityAssignment:
    sources = country_config.get("sources") or []
    source = (
        city.get("source")
        if city and isinstance(city.get("source"), dict)
        else sources[0]
        if sources
        else country_config.get("source") or {}
    )
    return CityAssignment(
        city_id=str(city.get("city_id")) if city and city.get("city_id") else None,
        canonical_city=str(city.get("canonical_name") or "") if city else "",
        source_city=source_city,
        locality=source_city or None,
        admin_area_1=str(city.get("admin_area_1")) if city and city.get("admin_area_1") else None,
        admin_area_2=str(city.get("admin_area_2")) if city and city.get("admin_area_2") else None,
        mapping_status=mapping_status,
        mapping_method=mapping_method,
        grouping_basis=(
            str(city.get("grouping_basis"))
            if city and city.get("grouping_basis")
            else None
        ),
        source_authority=str(source.get("authority")) if source.get("authority") else None,
        source_url=str(source.get("url")) if source.get("url") else None,
        source_date=str(source.get("date")) if source.get("date") else None,
        source_hash=_source_hash(country_config),
        mapping_version=str(
            country_config.get("mapping_version") or payload.get("version") or ""
        )
        or None,
    )


def _unmapped(source_city: str, payload: dict[str, Any]) -> CityAssignment:
    return CityAssignment(
        source_city=source_city,
        locality=source_city or None,
        mapping_status="unmapped",
        mapping_method="country_not_configured",
        mapping_version=str(payload.get("version") or "") or None,
    )


def _only(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[0] if len(rows) == 1 else None


def _resolve_property_override(
    country_config: dict[str, Any],
    *,
    property_id: str | None,
    property_name: str | None,
    scene_type: str | None,
) -> dict[str, Any] | None:
    if not property_id:
        return None
    matches = [
        row
        for row in country_config.get("property_overrides") or []
        if str(row.get("property_id") or "") == str(property_id)
    ]
    if not matches:
        return None
    if len(matches) != 1:
        return {"status": "invalid", "reason": "ambiguous_property_override"}
    override = matches[0]
    if (
        normalize_text(override.get("property_name")) != normalize_text(property_name)
        or str(override.get("scene_type") or "") != str(scene_type or "")
    ):
        return {"status": "invalid", "reason": "property_override_input_mismatch"}
    return {"status": "matched", **override}


def _matches_override_reference(
    city: dict[str, Any],
    *,
    latitude: float | None,
    longitude: float | None,
    max_distance_km: float,
) -> bool:
    if latitude is None or longitude is None or max_distance_km <= 0:
        return False
    points = city.get("reference_points") or []
    return any(
        _haversine_km(
            latitude,
            longitude,
            _optional_float(point.get("latitude")),
            _optional_float(point.get("longitude")),
        )
        <= max_distance_km
        for point in points
        if isinstance(point, dict)
        and _optional_float(point.get("latitude")) is not None
        and _optional_float(point.get("longitude")) is not None
    )


def _contains(
    bounds: Any,
    latitude: float | None,
    longitude: float | None,
) -> bool:
    if not isinstance(bounds, dict) or latitude is None or longitude is None:
        return False
    try:
        return (
            float(bounds["min_latitude"]) <= latitude <= float(bounds["max_latitude"])
            and float(bounds["min_longitude"]) <= longitude <= float(bounds["max_longitude"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _matches_reference_point(
    city: dict[str, Any],
    latitude: float | None,
    longitude: float | None,
) -> bool:
    if not city.get("reference_match_required"):
        return True
    if latitude is None or longitude is None:
        return False
    max_distance_km = float(city.get("max_distance_km") or 75.0)
    for point in city.get("reference_points") or []:
        try:
            point_latitude = float(point["latitude"])
            point_longitude = float(point["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            _haversine_km(
                latitude,
                longitude,
                point_latitude,
                point_longitude,
            )
            <= max_distance_km
        ):
            return True
    return False


def _haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    radius_km = 6371.0088
    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    latitude_a_rad = math.radians(latitude_a)
    latitude_b_rad = math.radians(latitude_b)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_a_rad)
        * math.cos(latitude_b_rad)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))


def _source_hash(country_config: dict[str, Any]) -> str:
    serialized = json.dumps(country_config, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
