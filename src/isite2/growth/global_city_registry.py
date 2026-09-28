from __future__ import annotations

import csv
import difflib
import io
import json
import math
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from isite2.growth.property_identity import normalize_text

UNLOCODE_RELEASE = "2025-1"
UNLOCODE_AUTHORITY = "United Nations Economic Commission for Europe (UNECE)"
UNLOCODE_PUBLICATION_URL = "https://unlocode.unece.org/publications/"
GNS_RELEASE = "2013-12-23"
GNS_AUTHORITY = (
    "National Geospatial-Intelligence Agency / U.S. Board on Geographic Names"
)
GNS_PUBLICATION_URL = "https://geonames.nga.mil/geonames/GNSHome/welcome.html"

_PROPERTY_SCENE_HINT_TOKENS = {
    "airport_terminal": {
        "aeroclub",
        "aerodrome",
        "airfield",
        "airport",
        "airstrip",
        "terminal",
    },
    "convention_center": {
        "center",
        "centre",
        "conference",
        "convention",
        "expo",
        "exhibition",
    },
    "hospital": {"clinic", "health", "hospital", "medical"},
    "large_retail_supermarket": {
        "hypermarket",
        "market",
        "retail",
        "store",
        "supermarket",
    },
    "luxury_hotel_mice": {
        "conference",
        "convention",
        "hotel",
        "lodge",
        "resort",
    },
    "mall_mixed_use": {"center", "centre", "mall", "plaza", "shopping"},
    "office_government": {
        "building",
        "center",
        "centre",
        "complex",
        "headquarters",
        "office",
        "tower",
    },
    "stadium": {"arena", "complex", "sports", "stadium"},
    "transport_hub": {
        "interchange",
        "metro",
        "railway",
        "station",
        "terminal",
    },
    "university": {"campus", "college", "institute", "university"},
}
_FACILITY_NAME_TOKENS = frozenset(
    token for values in _PROPERTY_SCENE_HINT_TOKENS.values() for token in values
)
_GNS_ADMIN_PLACE_DESIGNATIONS = {
    "PPLA",
    "PPLA2",
    "PPLA3",
    "PPLA4",
    "PPLC",
}
_GNS_NON_ADMIN_PROPERTY_HINT_MAX_DISTANCE_KM = 25.0
_GNS_NEAREST_ADMIN_MAX_DISTANCE_KM = {
    "airport_terminal": 25.0,
    "transport_hub": 25.0,
}
_GNS_NEAREST_NON_ADMIN_MAX_DISTANCE_KM = {
    "airport_terminal": 15.0,
    "transport_hub": 10.0,
}
_GNS_NEAREST_DEFAULT_ADMIN_MAX_DISTANCE_KM = 15.0
_GNS_NEAREST_DEFAULT_NON_ADMIN_MAX_DISTANCE_KM = 7.5
_GNS_NEAREST_UNSCOPED_AIRPORT_ADMIN_MAX_DISTANCE_KM = 15.0
_GNS_NEAREST_MIN_DISTANCE_GAP_KM = 5.0
_GNS_NEAREST_MIN_DISTANCE_RATIO = 1.5
_PROPERTY_CITY_LEADING_DESCRIPTORS = (
    ("bahia", "de"),
    ("bahias", "de"),
)
_ADMIN_AREA_WORDS = {
    "admin",
    "administrative",
    "area",
    "county",
    "department",
    "departamento",
    "district",
    "governorate",
    "khaet",
    "khet",
    "khett",
    "municipality",
    "oblast",
    "prefecture",
    "province",
    "provincia",
    "region",
    "regional",
    "state",
    "wilaya",
    "wilayah",
}

COUNTRY_CODE_ALIASES = {
    "bolivia": "BO",
    "brunei": "BN",
    "cape verde": "CV",
    "congo": "CG",
    "cote divoire": "CI",
    "czech republic": "CZ",
    "democratic republic of the congo": "CD",
    "iran": "IR",
    "laos": "LA",
    "moldova": "MD",
    "north macedonia": "MK",
    "reunion": "RE",
    "russia": "RU",
    "sao tome and principe": "ST",
    "south korea": "KR",
    "tanzania": "TZ",
    "turkey": "TR",
    "venezuela": "VE",
    "vietnam": "VN",
}


def build_unlocode_registry(
    properties: Iterable[dict[str, Any]],
    *,
    locodes_dir: Path,
    country_codes_path: Path,
    subdivisions_path: Path | None = None,
    excluded_countries: set[str] | None = None,
    max_distance_km: float = 75.0,
    gns_root: Path | None = None,
    gns_country_files: dict[str, str] | None = None,
    gns_source_manifest: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a compact registry for only the locations present in iSite.

    UN/LOCODE is the primary authoritative global location-name baseline and
    GNS populated places are an optional fallback. Neither source defines iSite
    metropolitan rollups, so generated rows require an official name plus a
    nearby reference coordinate. A property-name hint is accepted only when it
    starts with an official place name and also passes scene and coordinate
    gates.
    """

    excluded = excluded_countries or set()
    country_codes = load_country_codes(country_codes_path)
    subdivision_names = (
        load_unlocode_subdivision_names(subdivisions_path)
        if subdivisions_path and subdivisions_path.exists()
        else {}
    )
    rows_by_country: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in properties:
        country = str(row.get("country") or "").strip()
        if country and country not in excluded:
            rows_by_country[country].append(row)

    generated_countries: dict[str, Any] = {}
    audit_countries: dict[str, Any] = {}
    for country, rows in sorted(rows_by_country.items()):
        country_code = country_codes.get(normalize_country_name(country))
        if not country_code:
            audit_countries[country] = _unavailable_country_audit(
                rows, "country_code_not_found"
            )
            continue
        locode_path = locodes_dir / f"{country_code}.csv"
        if not locode_path.exists():
            audit_countries[country] = _unavailable_country_audit(
                rows, "country_file_not_found"
            )
            continue

        locations = load_unlocode_locations(locode_path)
        preferred_variants = _preferred_name_variants_by_source_city(
            rows=rows,
            locations=locations,
            subdivision_names=subdivision_names.get(country_code, set()),
            max_distance_km=max_distance_km,
        )
        gns_filename = (gns_country_files or {}).get(country)
        gns_path = gns_root / gns_filename if gns_root and gns_filename else None
        gns_locations, gns_admin1_aliases = (
            load_gns_country_data(gns_path)
            if gns_path is not None and gns_path.exists()
            else ({}, {})
        )
        gns_unique_locations = unique_official_locations(gns_locations)
        primary_results: list[dict[str, Any]] = []
        verified_unlocode_groups: set[str] = set()
        for row in rows:
            source_city = str(row.get("source_city") or row.get("city") or "").strip()
            result = match_unlocode_location(
                source_city=source_city,
                latitude=_optional_float(row.get("latitude")),
                longitude=_optional_float(row.get("longitude")),
                locations=locations,
                max_distance_km=max_distance_km,
                subdivision_names=subdivision_names.get(country_code, set()),
                preferred_name_variant=preferred_variants.get(normalize_text(source_city)),
            )
            if (
                result["status"] == "verified"
                and result["reason"]
                in {
                    "official_name_unique_no_reference",
                    "official_alias_unique_no_reference",
                }
                and gns_locations
            ):
                gns_validation = match_gns_location(
                    source_city=source_city,
                    latitude=_optional_float(row.get("latitude")),
                    longitude=_optional_float(row.get("longitude")),
                    locations=gns_locations,
                    max_distance_km=max_distance_km,
                )
                if gns_validation["status"] == "verified":
                    result["supplemental_reference"] = gns_validation["location"]
                elif (
                    gns_validation.get("reason")
                    == "gns_official_name_coordinate_conflict"
                ):
                    result = {
                        "status": "review_required",
                        "reason": "official_name_coordinate_conflict",
                    }
            primary_results.append(result)
            if result["status"] == "verified":
                verified_unlocode_groups.add(normalize_text(source_city))

        gns_preferred = _preferred_exact_locations_by_source_city(
            rows=rows,
            locations=gns_locations,
            max_distance_km=max_distance_km,
        )
        cities: dict[str, dict[str, Any]] = {}
        property_overrides: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        matched_count = 0
        match_methods: Counter[str] = Counter()
        unresolved_reasons: Counter[str] = Counter()
        source_roles: set[str] = set()
        for row, primary_result in zip(rows, primary_results, strict=True):
            source_city = str(row.get("source_city") or row.get("city") or "").strip()
            result = primary_result
            source_role = "unlocode"
            normalized_source = normalize_text(source_city)
            if (
                result["status"] != "verified"
                and result["reason"] != "high_level_admin_area_not_city"
                and normalized_source not in verified_unlocode_groups
                and gns_locations
            ):
                fallback = match_gns_location(
                    source_city=source_city,
                    latitude=_optional_float(row.get("latitude")),
                    longitude=_optional_float(row.get("longitude")),
                    locations=gns_locations,
                    max_distance_km=max_distance_km,
                    preferred_location=gns_preferred.get(normalized_source),
                )
                if fallback["status"] == "verified":
                    result = fallback
                    source_role = "gns"
            if result["status"] != "verified":
                property_hint = _match_property_name_sources(
                    property_name=str(
                        row.get("property_name") or row.get("canonical_name") or ""
                    ),
                    scene_type=str(row.get("scene_type") or ""),
                    country=country,
                    latitude=_optional_float(row.get("latitude")),
                    longitude=_optional_float(row.get("longitude")),
                    unlocode_locations=locations,
                    gns_locations=gns_locations,
                    max_distance_km=max_distance_km,
                )
                if property_hint["status"] == "verified":
                    result = property_hint
                    source_role = str(property_hint["source_role"])
                elif property_hint["status"] == "review_required":
                    result = property_hint
            if result["status"] == "unmapped" and gns_locations:
                nearest_city = match_nearest_gns_city(
                    scene_type=str(row.get("scene_type") or ""),
                    latitude=_optional_float(row.get("latitude")),
                    longitude=_optional_float(row.get("longitude")),
                    locations=gns_locations,
                    unique_locations=gns_unique_locations,
                    source_city=source_city,
                    admin1_aliases=gns_admin1_aliases,
                )
                if nearest_city["status"] != "unmapped":
                    result = nearest_city
                    source_role = "gns"
            if result["status"] != "verified":
                unresolved_reasons[result["reason"]] += 1
                unresolved.append(
                    {
                        "property_id": str(row.get("property_id") or row.get("id") or ""),
                        "property_name": str(
                            row.get("property_name") or row.get("canonical_name") or ""
                        ),
                        "source_city": source_city,
                        "reason": result["reason"],
                    }
                )
                continue
            matched_count += 1
            match_methods[result["reason"]] += 1
            source_roles.add(source_role)
            supplemental_reference = result.get("supplemental_reference")
            if supplemental_reference is not None:
                source_roles.add("gns")
            location = result["location"]
            city_id = f"{country_code}:{source_role}:{str(location['code']).lower()}"
            source = (
                _gns_source_payload(
                    country=country,
                    filename=gns_filename,
                    manifest=gns_source_manifest,
                )
                if source_role == "gns"
                else _unlocode_source_payload()
            )
            property_scoped_match = any(
                marker in str(result["reason"])
                for marker in ("property_name_city", "nearest_")
            )
            city = cities.setdefault(
                city_id,
                {
                    "city_id": city_id,
                    "canonical_name": location["preferred_name"],
                    "official_name": location["name"],
                    "admin_area_1": location.get("subdivision") or None,
                    "grouping_basis": (
                        "official_populated_place"
                        if source_role == "gns"
                        else "official_trade_location"
                    ),
                    "aliases": [],
                    "official_exact_names": [
                        location["name"],
                        location.get("name_ascii"),
                    ],
                    "official_derived_aliases": [
                        alias
                        for alias in location.get("match_aliases") or []
                        if normalize_text(alias)
                        not in set(location.get("exact_names_normalized") or [])
                    ],
                    "localities": [],
                    "reference_points": [],
                    "reference_match_required": (
                        location["latitude"] is not None
                        or supplemental_reference is not None
                    ),
                    "official_match_method": result["reason"],
                    "max_distance_km": max_distance_km,
                    "source": source,
                },
            )
            for alias in (
                location["name"],
                location.get("name_ascii"),
                *(location.get("match_aliases") or []),
                *(() if property_scoped_match else (source_city,)),
            ):
                if alias and alias not in city["aliases"]:
                    city["aliases"].append(alias)
            reference_locations = [
                (location, source_role),
                *(([(supplemental_reference, "gns")]) if supplemental_reference else []),
            ]
            for reference_location, reference_role in reference_locations:
                if (
                    reference_location.get("latitude") is None
                    or reference_location.get("longitude") is None
                ):
                    continue
                point = {
                    "latitude": reference_location["latitude"],
                    "longitude": reference_location["longitude"],
                    "source_role": reference_role,
                }
                if point not in city["reference_points"]:
                    city["reference_points"].append(point)
            if property_scoped_match:
                property_overrides.append(
                    {
                        "property_id": str(
                            row.get("property_id") or row.get("id") or ""
                        ),
                        "property_name": str(
                            row.get("property_name")
                            or row.get("canonical_name")
                            or ""
                        ),
                        "scene_type": str(row.get("scene_type") or ""),
                        "source_city": source_city,
                        "city_id": city_id,
                        "mapping_method": str(result["reason"]),
                        "matched_phrase": str(result.get("matched_phrase") or ""),
                        "max_distance_km": float(
                            result.get("max_distance_km")
                            or _property_hint_distance_limit(
                                location,
                                source_role=source_role,
                                max_distance_km=max_distance_km,
                            )
                        ),
                    }
                )

        _consolidate_cross_source_cities(
            cities,
            property_overrides=property_overrides,
            max_distance_km=max_distance_km,
        )
        source_roles = {
            "gns" if ":gns:" in city_id else "unlocode"
            for city_id in cities
        }
        if cities:
            unresolved_localities = _unresolved_locality_reasons(unresolved)
            mapping_sources = [_unlocode_source_payload()]
            if "gns" in source_roles:
                mapping_sources.append(
                    _gns_source_payload(
                        country=country,
                        filename=gns_filename,
                        manifest=gns_source_manifest,
                    )
                )
            generated_countries[country] = {
                "country_code": country_code,
                "mapping_version": _mapping_version(source_roles),
                "grouping_policy": (
                    "official_location_name_or_unique_nearby_coordinate"
                ),
                "rollup_status": "country_crosswalk_required_for_metro_rollups",
                "sources": mapping_sources,
                "unresolved_localities": unresolved_localities,
                "property_overrides": sorted(
                    property_overrides,
                    key=lambda item: item["property_id"],
                ),
                "cities": sorted(cities.values(), key=lambda item: item["city_id"]),
            }
        audit_countries[country] = {
            "property_count": len(rows),
            "verified_count": matched_count,
            "unresolved_count": len(unresolved),
            "ready_to_apply": bool(rows) and not unresolved,
            "match_method_counts": dict(sorted(match_methods.items())),
            "unresolved_reason_counts": dict(sorted(unresolved_reasons.items())),
            "unresolved": unresolved,
        }

    registry = {
        "version": f"unlocode-{UNLOCODE_RELEASE}+gns-{GNS_RELEASE}",
        "generated": True,
        "source_policy": {
            "scope": "official_name_or_unique_nearby_coordinate",
            "metro_rollups": "require_country_official_crosswalk",
        },
        "countries": generated_countries,
    }
    audit = {
        "source_release": UNLOCODE_RELEASE,
        "gns_source_release": GNS_RELEASE if gns_root else None,
        "country_count": len(rows_by_country),
        "generated_country_count": len(generated_countries),
        "ready_country_count": sum(
            1 for item in audit_countries.values() if item["ready_to_apply"]
        ),
        "verified_property_count": sum(
            item["verified_count"] for item in audit_countries.values()
        ),
        "unresolved_property_count": sum(
            item["unresolved_count"] for item in audit_countries.values()
        ),
        "match_method_counts": _sum_named_counts(
            audit_countries.values(), "match_method_counts"
        ),
        "unresolved_reason_counts": _sum_named_counts(
            audit_countries.values(), "unresolved_reason_counts"
        ),
        "countries": audit_countries,
    }
    return registry, audit


def load_country_codes(path: Path) -> dict[str, str]:
    result = dict(COUNTRY_CODE_ALIASES)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("CountryCode") or "").strip().upper()
            name = re.sub(
                r"\s*\(the\)$", "", str(row.get("CountryName") or ""), flags=re.I
            )
            if code and name:
                result.setdefault(normalize_country_name(name), code)
    return result


def load_gns_populated_places(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load official GNS populated-place names, grouped by stable UFI."""

    return load_gns_country_data(path)[0]


def load_gns_country_data(
    path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, set[str]]]:
    """Load populated places and official first-level administrative aliases."""

    by_ufi: dict[str, list[dict[str, str]]] = defaultdict(list)
    admin1_aliases: dict[str, set[str]] = defaultdict(set)
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".txt") and "disclaimer" not in name.lower()
        ]
        if len(members) != 1:
            raise ValueError(f"Expected one GNS data file in {path}, found {members}")
        with archive.open(members[0]) as raw:
            reader = csv.DictReader(
                io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace"),
                delimiter="\t",
            )
            for row in reader:
                if (
                    str(row.get("FC") or "").strip() == "A"
                    and str(row.get("DSG") or "").strip() == "ADM1"
                ):
                    admin1_code = str(row.get("ADM1") or "").strip()
                    if admin1_code:
                        for alias in _admin_area_name_aliases(
                            str(
                                row.get("FULL_NAME_RO")
                                or row.get("FULL_NAME_ND_RO")
                                or row.get("FULL_NAME_RG")
                                or ""
                            )
                        ):
                            admin1_aliases[alias].add(admin1_code)
                ufi = str(row.get("UFI") or "").strip()
                if str(row.get("FC") or "").strip() != "P" or not ufi:
                    continue
                name = str(
                    row.get("FULL_NAME_RO")
                    or row.get("FULL_NAME_ND_RO")
                    or row.get("FULL_NAME_RG")
                    or ""
                ).strip()
                if not name:
                    continue
                by_ufi[ufi].append({str(key): str(value or "") for key, value in row.items()})

    locations: list[dict[str, Any]] = []
    for ufi, rows in by_ufi.items():
        approved = next((row for row in rows if row.get("NT") == "N"), rows[0])
        names = _gns_names(rows)
        approved_names = _gns_names([row for row in rows if row.get("NT") == "N"])
        latitude = _optional_float(approved.get("LAT"))
        longitude = _optional_float(approved.get("LONG"))
        if latitude is None or longitude is None:
            continue
        preferred_name = str(
            approved.get("FULL_NAME_RO") or approved.get("FULL_NAME_ND_RO") or names[0]
        ).strip()
        locations.append(
            {
                "code": ufi,
                "name": preferred_name,
                "name_ascii": str(approved.get("FULL_NAME_ND_RO") or "").strip(),
                "preferred_name": preferred_name,
                "match_aliases": names,
                "exact_names_normalized": sorted(
                    {normalize_text(name) for name in approved_names if name}
                ),
                "subdivision": str(approved.get("ADM1") or "").strip(),
                "feature_designation": str(approved.get("DSG") or "").strip(),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    return _index_unlocode_locations(locations), dict(admin1_aliases)


def _gns_names(rows: list[dict[str, str]]) -> list[str]:
    result: list[str] = []
    for row in rows:
        for field in (
            "FULL_NAME_RO",
            "FULL_NAME_ND_RO",
            "FULL_NAME_RG",
            "FULL_NAME_ND_RG",
        ):
            value = str(row.get(field) or "").strip()
            if value and value not in result:
                result.append(value)
    return result


def _admin_area_name_aliases(value: str) -> set[str]:
    aliases: set[str] = set()
    for label in official_label_aliases(value):
        normalized = normalize_text(label)
        if not normalized:
            continue
        aliases.add(normalized)
        compact = " ".join(
            token for token in normalized.split() if token not in _ADMIN_AREA_WORDS
        )
        if compact:
            aliases.add(compact)
    return aliases


def load_unlocode_locations(path: Path) -> dict[str, list[dict[str, Any]]]:
    location_rows: list[dict[str, Any]] = []
    alias_records: list[tuple[str, str]] = []
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 5:
                continue
            if str(row[0]).strip() == "=":
                alias = _parse_unlocode_alias_record(str(row[3] or row[4]))
                if alias:
                    alias_records.append(alias)
                continue
            if len(row) < 11 or not row[2] or str(row[3]).startswith("."):
                continue
            coordinates = parse_unlocode_coordinates(row[10])
            if str(row[0]).strip() == "X":
                continue
            name = str(row[3]).strip()
            name_ascii = str(row[4]).strip()
            aliases = unlocode_location_aliases(name, name_ascii)
            location = {
                "code": str(row[2]).strip(),
                "name": name,
                "name_ascii": name_ascii,
                "preferred_name": preferred_unlocode_name(name, name_ascii),
                "match_aliases": aliases,
                "exact_names_normalized": sorted(
                    {
                        normalize_text(value)
                        for value in (name, name_ascii)
                        if value
                    }
                ),
                "subdivision": str(row[5]).strip(),
                "functions": str(row[6]).strip(),
                "latitude": coordinates[0] if coordinates else None,
                "longitude": coordinates[1] if coordinates else None,
            }
            location_rows.append(location)

    locations = _index_unlocode_locations(location_rows)
    for alias, target in alias_records:
        targets = locations.get(normalize_text(target), [])
        if len(targets) != 1:
            continue
        location = targets[0]
        if alias not in location["match_aliases"]:
            location["match_aliases"].append(alias)
    locations = _index_unlocode_locations(location_rows)
    return {
        alias: collapse_near_duplicate_locations(rows)
        for alias, rows in locations.items()
    }


def _index_unlocode_locations(
    location_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    locations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for location in location_rows:
        for alias in location.get("match_aliases") or []:
            normalized = normalize_text(alias)
            if normalized and location not in locations[normalized]:
                locations[normalized].append(location)
    return locations


def _parse_unlocode_alias_record(value: str) -> tuple[str, str] | None:
    if "=" not in value:
        return None
    alias, target = (part.strip() for part in value.split("=", 1))
    if not alias or not target:
        return None
    return alias, target


def collapse_near_duplicate_locations(
    locations: list[dict[str, Any]],
    *,
    max_duplicate_distance_km: float = 5.0,
) -> list[dict[str, Any]]:
    """Collapse duplicate official codes that represent the same named place."""

    result: list[dict[str, Any]] = []
    for location in sorted(locations, key=lambda item: str(item.get("code") or "")):
        duplicate = next(
            (
                existing
                for existing in result
                if normalize_text(existing.get("preferred_name"))
                == normalize_text(location.get("preferred_name"))
                and _locations_are_near(
                    existing,
                    location,
                    max_distance_km=max_duplicate_distance_km,
                )
            ),
            None,
        )
        if duplicate is None:
            result.append(location)
            continue
        for alias in location.get("match_aliases") or []:
            if alias not in duplicate["match_aliases"]:
                duplicate["match_aliases"].append(alias)
        duplicate["duplicate_codes"] = sorted(
            {
                duplicate["code"],
                *(duplicate.get("duplicate_codes") or []),
                location["code"],
                *(location.get("duplicate_codes") or []),
            }
        )
    return result


def _locations_are_near(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    max_distance_km: float,
) -> bool:
    coordinates = (
        first.get("latitude"),
        first.get("longitude"),
        second.get("latitude"),
        second.get("longitude"),
    )
    if any(value is None for value in coordinates):
        return False
    return haversine_km(*coordinates) <= max_distance_km


def load_unlocode_subdivision_names(path: Path) -> dict[str, set[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names: dict[str, set[str]] = defaultdict(set)
    for row in payload.get("@graph") or []:
        country_ref = ((row.get("unlcdv:countryCode") or {}).get("@id") or "")
        country_code = str(country_ref).rsplit(":", 1)[-1].upper()
        label = str(row.get("rdfs:label") or "").strip()
        if country_code and label:
            for alias in official_label_aliases(label):
                normalized = normalize_text(alias)
                if normalized:
                    names[country_code].add(normalized)
    return dict(names)


def unlocode_location_aliases(name: str, name_ascii: str = "") -> list[str]:
    aliases: list[str] = []
    for value in (name, name_ascii):
        for alias in official_label_aliases(value):
            if alias and alias not in aliases:
                aliases.append(alias)
    return aliases


def official_label_aliases(value: str) -> list[str]:
    """Return conservative aliases explicitly carried by an official label."""

    label = str(value or "").strip()
    if not label:
        return []
    aliases = [label]
    parenthetical = re.fullmatch(r".+\(([^()]+)\)\s*", label)
    if parenthetical:
        head = label[: parenthetical.start(1) - 1].strip()
        detail = parenthetical.group(1).strip()
        if head:
            aliases.append(head)
        former_name = re.sub(r"^(?:ex|formerly|former)\s+", "", detail, flags=re.I)
        aliases.append(former_name or detail)
    bracketed = re.fullmatch(r".+\[([^\[\]]+)\]\s*", label)
    if bracketed:
        aliases.append(bracketed.group(1).strip())
    if "," in label:
        head, tail = (part.strip() for part in label.split(",", 1))
        if head and tail and len(head) >= 3:
            aliases.append(head)
    return aliases


def preferred_unlocode_name(name: str, name_ascii: str = "") -> str:
    label = str(name or name_ascii).strip()
    former = re.fullmatch(r"(.+?)\s*\((?:ex|formerly|former)\s+[^()]+\)\s*", label, re.I)
    if former:
        return former.group(1).strip()
    aliases = official_label_aliases(name)
    if len(aliases) > 1:
        return aliases[-1]
    return str(name or name_ascii).strip()


def match_property_name_official_location(
    *,
    property_name: str,
    scene_type: str,
    country: str,
    latitude: float | None,
    longitude: float | None,
    locations: dict[str, list[dict[str, Any]]],
    source_role: str,
    max_distance_km: float,
) -> dict[str, Any]:
    """Match a leading official place name carried by a property name.

    This is intentionally narrower than entity extraction. The official name
    must start the property name, be followed by a scene-specific facility
    token, and have a unique nearby reference point. It never falls back to a
    coordinate-only nearest place.
    """

    normalized_property = normalize_text(property_name)
    tokens = normalized_property.split()
    scene_tokens = _PROPERTY_SCENE_HINT_TOKENS.get(scene_type, set())
    if (
        latitude is None
        or longitude is None
        or len(tokens) < 2
        or not scene_tokens
    ):
        return {
            "status": "unmapped",
            "reason": "property_name_official_city_not_found",
        }

    country_name = normalize_country_name(country)
    token_views = [tokens]
    token_views.extend(
        tokens[len(descriptor) :]
        for descriptor in _PROPERTY_CITY_LEADING_DESCRIPTORS
        if tuple(tokens[: len(descriptor)]) == descriptor
        and len(tokens) > len(descriptor) + 1
    )
    for viewed_tokens in token_views:
        for prefix_size in range(len(viewed_tokens) - 1, 0, -1):
            phrase = " ".join(viewed_tokens[:prefix_size])
            suffix_tokens = set(viewed_tokens[prefix_size:])
            if (
                len(phrase) < 3
                or phrase in _GENERIC_CITY_WORDS
                or not suffix_tokens.intersection(scene_tokens)
            ):
                continue
            candidates = locations.get(phrase, [])
            safe_candidates = [
                location
                for location in candidates
                if _property_hint_location_is_safe(
                    location,
                    matched_phrase=phrase,
                    country_name=country_name,
                )
            ]
            if not safe_candidates:
                continue

            referenced = [
                location
                for location in safe_candidates
                if location.get("latitude") is not None
                and location.get("longitude") is not None
            ]
            if not referenced:
                return {
                    "status": "review_required",
                    "reason": "property_name_official_city_coordinate_required",
                }
            ranked = sorted(
                (
                    (
                        haversine_km(
                            latitude,
                            longitude,
                            float(location["latitude"]),
                            float(location["longitude"]),
                        ),
                        location,
                    )
                    for location in referenced
                )
                ,
                key=lambda pair: (pair[0], str(pair[1].get("code") or "")),
            )
            within_range = [
                pair
                for pair in ranked
                if pair[0]
                <= _property_hint_distance_limit(
                    pair[1],
                    source_role=source_role,
                    max_distance_km=max_distance_km,
                )
            ]
            if not within_range:
                return {
                    "status": "review_required",
                    "reason": "property_name_official_city_coordinate_conflict",
                }
            best_distance, best = within_range[0]
            if (
                len(within_range) > 1
                and str(within_range[1][1].get("code")) != str(best.get("code"))
                and abs(within_range[1][0] - best_distance) < 1.0
            ):
                return {
                    "status": "review_required",
                    "reason": "property_name_official_city_ambiguous",
                }
            exact = phrase in set(best.get("exact_names_normalized") or [])
            return {
                "status": "verified",
                "reason": (
                    f"{source_role}_property_name_city_exact_coordinate"
                    if exact
                    else f"{source_role}_property_name_city_alias_coordinate"
                ),
                "distance_km": round(best_distance, 3),
                "matched_phrase": phrase,
                "source_role": source_role,
                "location": best,
            }

    return {
        "status": "unmapped",
        "reason": "property_name_official_city_not_found",
    }


def _match_property_name_sources(
    *,
    property_name: str,
    scene_type: str,
    country: str,
    latitude: float | None,
    longitude: float | None,
    unlocode_locations: dict[str, list[dict[str, Any]]],
    gns_locations: dict[str, list[dict[str, Any]]],
    max_distance_km: float,
) -> dict[str, Any]:
    results = [
        match_property_name_official_location(
            property_name=property_name,
            scene_type=scene_type,
            country=country,
            latitude=latitude,
            longitude=longitude,
            locations=unlocode_locations,
            source_role="unlocode",
            max_distance_km=max_distance_km,
        )
    ]
    if gns_locations:
        results.append(
            match_property_name_official_location(
                property_name=property_name,
                scene_type=scene_type,
                country=country,
                latitude=latitude,
                longitude=longitude,
                locations=gns_locations,
                source_role="gns",
                max_distance_km=max_distance_km,
            )
        )
    verified = [result for result in results if result["status"] == "verified"]
    if len(verified) == 1:
        return verified[0]
    if len(verified) > 1:
        normalized_names = {
            normalize_text(result["location"].get("preferred_name"))
            for result in verified
        }
        if len(normalized_names) == 1:
            return next(
                result
                for result in verified
                if result["source_role"] == "unlocode"
            )
        return {
            "status": "review_required",
            "reason": "property_name_official_city_source_conflict",
        }
    review = next(
        (result for result in results if result["status"] == "review_required"),
        None,
    )
    return review or {
        "status": "unmapped",
        "reason": "property_name_official_city_not_found",
    }


def _property_hint_location_is_safe(
    location: dict[str, Any],
    *,
    matched_phrase: str,
    country_name: str,
) -> bool:
    labels = {
        normalize_text(location.get("name")),
        normalize_text(location.get("preferred_name")),
    }
    labels.discard("")
    if not labels or matched_phrase == country_name and len(matched_phrase) < 4:
        return False
    return not any(
        set(label.split()).intersection(_FACILITY_NAME_TOKENS) for label in labels
    )


def _property_hint_distance_limit(
    location: dict[str, Any],
    *,
    source_role: str,
    max_distance_km: float,
) -> float:
    if source_role != "gns":
        return max_distance_km
    designation = str(location.get("feature_designation") or "").upper()
    if designation in _GNS_ADMIN_PLACE_DESIGNATIONS:
        return max_distance_km
    return min(max_distance_km, _GNS_NON_ADMIN_PROPERTY_HINT_MAX_DISTANCE_KM)


def match_nearest_gns_city(
    *,
    scene_type: str,
    latitude: float | None,
    longitude: float | None,
    locations: dict[str, list[dict[str, Any]]],
    unique_locations: list[dict[str, Any]] | None = None,
    source_city: str = "",
    admin1_aliases: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Resolve a property to one uniquely nearby official GNS settlement.

    Administrative seats are preferred because they are the more stable main-city
    rollup. Ordinary populated places use a shorter radius. A close runner-up
    blocks automatic assignment so dense or overlapping cities remain reviewable.
    """

    if latitude is None or longitude is None:
        return {
            "status": "unmapped",
            "reason": "gns_nearest_city_coordinate_required",
        }

    reference_locations = unique_locations or unique_official_locations(locations)
    source_admin_codes = {
        code
        for alias in _admin_area_name_aliases(source_city)
        for code in (admin1_aliases or {}).get(alias, set())
    }
    if len(source_admin_codes) > 1:
        return {
            "status": "review_required",
            "reason": "gns_source_admin_area_ambiguous",
        }
    if source_admin_codes:
        source_admin_code = next(iter(source_admin_codes))
        reference_locations = [
            location
            for location in reference_locations
            if str(location.get("subdivision") or "") == source_admin_code
        ]
        if not reference_locations:
            return {
                "status": "unmapped",
                "reason": "gns_nearest_city_not_within_source_admin_area",
            }
    ranked = sorted(
        (
            (
                haversine_km(
                    latitude,
                    longitude,
                    float(location["latitude"]),
                    float(location["longitude"]),
                ),
                location,
            )
            for location in reference_locations
        ),
        key=lambda pair: (pair[0], str(pair[1].get("code") or "")),
    )
    admin_limit = None
    if scene_type == "airport_terminal" and not source_admin_codes:
        admin_limit = _GNS_NEAREST_UNSCOPED_AIRPORT_ADMIN_MAX_DISTANCE_KM
    result = _select_nearest_gns_city(
        ranked=ranked,
        scene_type=scene_type,
        admin_max_distance_km=admin_limit,
    )
    if source_admin_codes and result["status"] == "unmapped":
        result["reason"] = "gns_nearest_city_not_within_source_admin_area"
    return result


def unique_official_locations(
    locations: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    unique = {
        str(location.get("code") or ""): location
        for rows in locations.values()
        for location in rows
        if location.get("code")
        and location.get("latitude") is not None
        and location.get("longitude") is not None
    }
    return list(unique.values())


def _select_nearest_gns_city(
    *,
    ranked: list[tuple[float, dict[str, Any]]],
    scene_type: str,
    admin_max_distance_km: float | None = None,
) -> dict[str, Any]:
    admin_limit = (
        admin_max_distance_km
        if admin_max_distance_km is not None
        else _GNS_NEAREST_ADMIN_MAX_DISTANCE_KM.get(
            scene_type,
            _GNS_NEAREST_DEFAULT_ADMIN_MAX_DISTANCE_KM,
        )
    )
    non_admin_limit = _GNS_NEAREST_NON_ADMIN_MAX_DISTANCE_KM.get(
        scene_type,
        _GNS_NEAREST_DEFAULT_NON_ADMIN_MAX_DISTANCE_KM,
    )
    admin_candidates = [
        pair
        for pair in ranked
        if pair[0] <= admin_limit
        and str(pair[1].get("feature_designation") or "").upper()
        in _GNS_ADMIN_PLACE_DESIGNATIONS
    ]
    candidate_tier = admin_candidates
    reason = "gns_nearest_admin_coordinate_unique"
    max_distance_km = admin_limit
    if not candidate_tier:
        candidate_tier = [pair for pair in ranked if pair[0] <= non_admin_limit]
        reason = "gns_nearest_populated_place_coordinate_unique"
        max_distance_km = non_admin_limit
    if not candidate_tier:
        return {
            "status": "unmapped",
            "reason": "gns_nearest_city_not_within_threshold",
        }

    best_distance, best = candidate_tier[0]
    if len(candidate_tier) > 1:
        next_distance = candidate_tier[1][0]
        distance_gap = next_distance - best_distance
        distance_ratio = next_distance / max(best_distance, 0.1)
        if (
            distance_gap < _GNS_NEAREST_MIN_DISTANCE_GAP_KM
            or distance_ratio < _GNS_NEAREST_MIN_DISTANCE_RATIO
        ):
            return {
                "status": "review_required",
                "reason": "gns_nearest_city_ambiguous",
            }
    return {
        "status": "verified",
        "reason": reason,
        "distance_km": round(best_distance, 3),
        "max_distance_km": max_distance_km,
        "location": best,
    }


def _consolidate_cross_source_cities(
    cities: dict[str, dict[str, Any]],
    *,
    property_overrides: list[dict[str, Any]],
    max_distance_km: float,
) -> None:
    """Prefer UN/LOCODE when GNS resolves the same official place."""

    unlocode_cities = [
        city for city_id, city in cities.items() if ":unlocode:" in city_id
    ]
    gns_city_ids = [city_id for city_id in cities if ":gns:" in city_id]
    replacements: dict[str, str] = {}
    for gns_city_id in gns_city_ids:
        gns_city = cities[gns_city_id]
        gns_labels = _normalized_city_labels(gns_city)
        matches = [
            city
            for city in unlocode_cities
            if gns_labels.intersection(_normalized_city_labels(city))
            and _city_references_are_compatible(
                city,
                gns_city,
                max_distance_km=max_distance_km,
            )
        ]
        if len(matches) != 1:
            continue
        preferred = matches[0]
        preferred_id = str(preferred["city_id"])
        for field in ("aliases", "official_exact_names", "official_derived_aliases"):
            for value in gns_city.get(field) or []:
                if value and value not in preferred[field]:
                    preferred[field].append(value)
        for point in gns_city.get("reference_points") or []:
            if point not in preferred["reference_points"]:
                preferred["reference_points"].append(point)
        preferred["reference_match_required"] = bool(
            preferred.get("reference_match_required")
            or preferred.get("reference_points")
        )
        replacements[gns_city_id] = preferred_id
        del cities[gns_city_id]

    for override in property_overrides:
        replacement = replacements.get(str(override.get("city_id") or ""))
        if replacement is None:
            continue
        override["city_id"] = replacement
        mapping_method = str(override.get("mapping_method") or "")
        override["mapping_method"] = (
            f"unlocode_{mapping_method.removeprefix('gns_')}"
            if mapping_method.startswith("gns_")
            else "unlocode_property_name_city_alias_coordinate"
        )


def _normalized_city_labels(city: dict[str, Any]) -> set[str]:
    return {
        normalize_text(value)
        for value in (
            city.get("canonical_name"),
            city.get("official_name"),
            *(city.get("aliases") or []),
        )
        if str(value or "").strip()
    }


def _city_references_are_compatible(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    max_distance_km: float,
) -> bool:
    first_points = first.get("reference_points") or []
    second_points = second.get("reference_points") or []
    if not first_points or not second_points:
        return True
    return any(
        haversine_km(
            float(first_point["latitude"]),
            float(first_point["longitude"]),
            float(second_point["latitude"]),
            float(second_point["longitude"]),
        )
        <= max_distance_km
        for first_point in first_points
        for second_point in second_points
    )


def match_gns_location(
    *,
    source_city: str,
    latitude: float | None,
    longitude: float | None,
    locations: dict[str, list[dict[str, Any]]],
    max_distance_km: float,
    preferred_location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Match an exact GNS official name or variant with a nearby coordinate."""

    candidates = locations.get(normalize_text(source_city), [])
    if not candidates:
        return {"status": "unmapped", "reason": "gns_official_name_not_found"}
    if latitude is None or longitude is None:
        return {"status": "review_required", "reason": "coordinate_required"}
    if preferred_location is not None:
        candidates = [preferred_location]
    nearby = sorted(
        (
            (haversine_km(latitude, longitude, item["latitude"], item["longitude"]), item)
            for item in candidates
        ),
        key=lambda pair: pair[0],
    )
    within_range = [pair for pair in nearby if pair[0] <= max_distance_km]
    if not within_range:
        return {
            "status": "review_required",
            "reason": "gns_official_name_coordinate_conflict",
        }
    best_distance, best = within_range[0]
    if len(within_range) > 1 and abs(within_range[1][0] - best_distance) < 1.0:
        return {"status": "review_required", "reason": "gns_ambiguous_official_location"}
    exact = normalize_text(source_city) in set(best.get("exact_names_normalized") or [])
    return {
        "status": "verified",
        "reason": (
            "gns_reference_exact_coordinate"
            if exact
            else "gns_reference_alias_coordinate"
        ),
        "distance_km": round(best_distance, 3),
        "location": best,
    }


def _preferred_exact_locations_by_source_city(
    *,
    rows: list[dict[str, Any]],
    locations: dict[str, list[dict[str, Any]]],
    max_distance_km: float,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        source_city = str(row.get("source_city") or row.get("city") or "").strip()
        normalized = normalize_text(source_city)
        latitude = _optional_float(row.get("latitude"))
        longitude = _optional_float(row.get("longitude"))
        if normalized in locations and latitude is not None and longitude is not None:
            grouped[normalized].append((latitude, longitude))

    result: dict[str, dict[str, Any]] = {}
    for normalized, points in grouped.items():
        candidates = {
            str(item["code"]): item for item in locations.get(normalized, [])
        }
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in candidates.values():
            distances = [
                haversine_km(lat, lon, item["latitude"], item["longitude"])
                for lat, lon in points
            ]
            if distances and max(distances) <= max_distance_km:
                ranked.append((sum(distances) / len(distances), item))
        ranked.sort(key=lambda pair: (pair[0], str(pair[1]["code"])))
        if ranked and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] >= 1.0):
            result[normalized] = ranked[0][1]
    return result


def match_unlocode_location(
    *,
    source_city: str,
    latitude: float | None,
    longitude: float | None,
    locations: dict[str, list[dict[str, Any]]],
    max_distance_km: float,
    subdivision_names: set[str] | None = None,
    preferred_name_variant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_source = normalize_text(source_city)
    candidates = locations.get(normalized_source, [])
    if not candidates:
        if normalized_source in (subdivision_names or set()):
            return {
                "status": "unmapped",
                "reason": "high_level_admin_area_not_city",
            }
        variant = (
            _match_preferred_name_variant(
                preferred_name_variant,
                latitude=latitude,
                longitude=longitude,
                max_distance_km=max_distance_km,
            )
            if preferred_name_variant is not None
            else _nearby_name_variant(
                source_city=source_city,
                latitude=latitude,
                longitude=longitude,
                locations=locations,
                max_distance_km=max_distance_km,
            )
        )
        if variant is not None:
            return variant
        return {"status": "unmapped", "reason": "official_name_not_found"}
    if latitude is None or longitude is None:
        return {"status": "review_required", "reason": "coordinate_required"}
    referenced = [
        item
        for item in candidates
        if item.get("latitude") is not None and item.get("longitude") is not None
    ]
    if not referenced:
        if len(candidates) > 1:
            return {
                "status": "review_required",
                "reason": "ambiguous_official_location_without_reference",
            }
        location = candidates[0]
        exact = normalized_source in set(location.get("exact_names_normalized") or [])
        return {
            "status": "verified",
            "reason": (
                "official_name_unique_no_reference"
                if exact
                else "official_alias_unique_no_reference"
            ),
            "location": location,
        }
    nearby = sorted(
        (
            (haversine_km(latitude, longitude, item["latitude"], item["longitude"]), item)
            for item in referenced
        ),
        key=lambda pair: pair[0],
    )
    within_range = [pair for pair in nearby if pair[0] <= max_distance_km]
    if not within_range:
        variant = _nearby_name_variant(
            source_city=source_city,
            latitude=latitude,
            longitude=longitude,
            locations=locations,
            max_distance_km=max_distance_km,
            minimum_similarity=0.7,
        )
        if variant is not None:
            return variant
        return {
            "status": "review_required",
            "reason": "official_name_coordinate_conflict",
        }
    best_distance, best = within_range[0]
    if len(within_range) > 1 and abs(within_range[1][0] - best_distance) < 1.0:
        return {"status": "review_required", "reason": "ambiguous_official_location"}
    exact = normalized_source in set(best.get("exact_names_normalized") or [])
    return {
        "status": "verified",
        "reason": "reference_exact_coordinate" if exact else "reference_alias_coordinate",
        "distance_km": round(best_distance, 3),
        "location": best,
    }


_GENERIC_CITY_WORDS = {
    "al",
    "central",
    "cidade",
    "city",
    "ciudad",
    "de",
    "del",
    "district",
    "east",
    "el",
    "greater",
    "la",
    "metropolitan",
    "municipality",
    "north",
    "south",
    "ville",
    "west",
}


def _nearby_name_variant(
    *,
    source_city: str,
    latitude: float | None,
    longitude: float | None,
    locations: dict[str, list[dict[str, Any]]],
    max_distance_km: float,
    minimum_similarity: float = 0.5,
) -> dict[str, Any] | None:
    if latitude is None or longitude is None:
        return None
    ranked = _rank_name_variants(
        source_city=source_city,
        points=[(latitude, longitude)],
        locations=locations,
        max_distance_km=max_distance_km,
        minimum_similarity=minimum_similarity,
    )
    return _select_ranked_name_variant(ranked)


def _preferred_name_variants_by_source_city(
    *,
    rows: list[dict[str, Any]],
    locations: dict[str, list[dict[str, Any]]],
    subdivision_names: set[str],
    max_distance_km: float,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    source_labels: dict[str, str] = {}
    for row in rows:
        source_city = str(row.get("source_city") or row.get("city") or "").strip()
        normalized = normalize_text(source_city)
        if not normalized or normalized in locations or normalized in subdivision_names:
            continue
        latitude = _optional_float(row.get("latitude"))
        longitude = _optional_float(row.get("longitude"))
        if latitude is None or longitude is None:
            continue
        grouped[normalized].append((latitude, longitude))
        source_labels.setdefault(normalized, source_city)

    preferred: dict[str, dict[str, Any]] = {}
    for normalized, points in grouped.items():
        ranked = _rank_name_variants(
            source_city=source_labels[normalized],
            points=points,
            locations=locations,
            max_distance_km=max_distance_km,
        )
        result = _select_ranked_name_variant(ranked)
        if result and result.get("status") == "verified":
            preferred[normalized] = result["location"]
    return preferred


def _rank_name_variants(
    *,
    source_city: str,
    points: list[tuple[float, float]],
    locations: dict[str, list[dict[str, Any]]],
    max_distance_km: float,
    minimum_similarity: float = 0.5,
) -> list[tuple[float, float, dict[str, Any]]]:
    unique: dict[tuple[str, str, float, float], dict[str, Any]] = {}
    for rows in locations.values():
        for item in rows:
            item_latitude = item.get("latitude")
            item_longitude = item.get("longitude")
            if item_latitude is None or item_longitude is None:
                continue
            key = (
                str(item.get("code") or ""),
                str(item.get("preferred_name") or ""),
                float(item_latitude),
                float(item_longitude),
            )
            unique[key] = item

    ranked: list[tuple[float, float, dict[str, Any]]] = []
    for item in unique.values():
        distances = sorted(
            haversine_km(
                point_latitude,
                point_longitude,
                item["latitude"],
                item["longitude"],
            )
            for point_latitude, point_longitude in points
        )
        median_distance = distances[len(distances) // 2]
        if median_distance > max_distance_km:
            continue
        similarity = max(
            (_city_name_similarity(source_city, alias) for alias in item["match_aliases"]),
            default=0.0,
        )
        if similarity >= minimum_similarity:
            ranked.append((similarity, median_distance, item))
    return ranked


def _select_ranked_name_variant(
    ranked: list[tuple[float, float, dict[str, Any]]],
) -> dict[str, Any] | None:
    if not ranked:
        return None
    ranked.sort(key=lambda row: (-row[0], row[1], str(row[2].get("code") or "")))
    best_similarity, best_distance, best = ranked[0]
    if len(ranked) > 1:
        next_similarity, next_distance, _ = ranked[1]
        if (
            best_similarity - next_similarity < 0.05
            and abs(best_distance - next_distance) < 5.0
        ):
            return {
                "status": "review_required",
                "reason": "ambiguous_official_name_variant",
            }
    return {
        "status": "verified",
        "reason": "reference_name_variant_coordinate",
        "distance_km": round(best_distance, 3),
        "name_similarity": round(best_similarity, 3),
        "location": best,
    }


def _match_preferred_name_variant(
    location: dict[str, Any],
    *,
    latitude: float | None,
    longitude: float | None,
    max_distance_km: float,
) -> dict[str, Any] | None:
    if latitude is None or longitude is None:
        return None
    distance = haversine_km(
        latitude,
        longitude,
        location["latitude"],
        location["longitude"],
    )
    if distance > max_distance_km:
        return {
            "status": "review_required",
            "reason": "official_name_variant_coordinate_conflict",
        }
    return {
        "status": "verified",
        "reason": "reference_name_variant_coordinate",
        "distance_km": round(distance, 3),
        "location": location,
    }


def _city_name_similarity(first: str, second: str) -> float:
    normalized_first = normalize_text(first)
    normalized_second = normalize_text(second)
    first_core = " ".join(
        token
        for token in normalized_first.split()
        if token not in _GENERIC_CITY_WORDS
    )
    second_core = " ".join(
        token
        for token in normalized_second.split()
        if token not in _GENERIC_CITY_WORDS
    )
    if not first_core or not second_core:
        return 0.0
    if first_core == second_core:
        return 1.0
    return difflib.SequenceMatcher(None, first_core, second_core).ratio()


def parse_unlocode_coordinates(value: str) -> tuple[float, float] | None:
    match = re.fullmatch(
        r"(\d{2})(\d{2})([NS])\s+(\d{3})(\d{2})([EW])", str(value or "").strip()
    )
    if not match:
        return None
    latitude = int(match.group(1)) + int(match.group(2)) / 60
    longitude = int(match.group(4)) + int(match.group(5)) / 60
    if match.group(3) == "S":
        latitude *= -1
    if match.group(6) == "W":
        longitude *= -1
    return latitude, longitude


def haversine_km(
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


def normalize_country_name(value: str) -> str:
    normalized = normalize_text(value)
    return normalized.removeprefix("the ").strip()


def _unlocode_source_payload() -> dict[str, str]:
    return {
        "authority": UNLOCODE_AUTHORITY,
        "url": UNLOCODE_PUBLICATION_URL,
        "date": UNLOCODE_RELEASE,
        "role": "intergovernmental_location_standard",
    }


def _gns_source_payload(
    *,
    country: str,
    filename: str | None,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    country_manifest = ((manifest or {}).get("countries") or {}).get(country) or {}
    return {
        "authority": GNS_AUTHORITY,
        "url": GNS_PUBLICATION_URL,
        "date": GNS_RELEASE,
        "role": "official_foreign_geographic_names_populated_place_fallback",
        "archive_url": country_manifest.get("url"),
        "archive_filename": filename,
        "archive_sha256": country_manifest.get("sha256"),
    }


def _mapping_version(source_roles: set[str]) -> str:
    if "gns" in source_roles:
        return f"unlocode-{UNLOCODE_RELEASE}+gns-{GNS_RELEASE}"
    return f"unlocode-{UNLOCODE_RELEASE}"


def _unavailable_country_audit(
    rows: list[dict[str, Any]], reason: str
) -> dict[str, Any]:
    return {
        "property_count": len(rows),
        "verified_count": 0,
        "unresolved_count": len(rows),
        "ready_to_apply": False,
        "match_method_counts": {},
        "unresolved_reason_counts": {reason: len(rows)},
        "unresolved": [
            {
                "property_id": str(row.get("property_id") or row.get("id") or ""),
                "property_name": str(
                    row.get("property_name") or row.get("canonical_name") or ""
                ),
                "source_city": str(row.get("source_city") or row.get("city") or ""),
                "reason": reason,
            }
            for row in rows
        ],
    }


def _sum_named_counts(rows: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    total: Counter[str] = Counter()
    for row in rows:
        total.update(row.get(field) or {})
    return dict(sorted(total.items()))


def _unresolved_locality_reasons(rows: list[dict[str, Any]]) -> dict[str, str]:
    reasons: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        normalized = normalize_text(row.get("source_city"))
        reason = str(row.get("reason") or "official_name_not_found")
        if normalized:
            reasons[normalized].add(reason)
    return {
        locality: _preferred_unresolved_reason(values)
        for locality, values in sorted(reasons.items())
    }


def _preferred_unresolved_reason(reasons: set[str]) -> str:
    priority = (
        "official_name_coordinate_conflict",
        "ambiguous_official_location_without_reference",
        "ambiguous_official_location",
        "coordinate_required",
        "high_level_admin_area_not_city",
        "official_name_not_found",
    )
    return next((reason for reason in priority if reason in reasons), sorted(reasons)[0])


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
