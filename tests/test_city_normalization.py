from __future__ import annotations


def test_mongolia_airport_uses_reviewed_statutory_city_not_served_city():
    from isite2.growth.city_normalization import canonicalize_city

    for property_id in (
        "77ff56ac-93c6-4117-8614-75ea8a593e00",
        "6b38f238-8071-4dd3-b596-575b06224283",
    ):
        args = dict(
            country="Mongolia", source_city="Khushig Valley",
            property_id=property_id,
            property_name="Chinggis Khaan International Airport",
            scene_type="airport_terminal", latitude=47.6512559,
            longitude=106.8215825,
        )
        result = canonicalize_city(**args)
        assert result.mapping_status == "verified"
        assert result.canonical_city == "Hunnu City"
        assert result.locality == "Khushig Valley"
        assert result.admin_area_2 == "Sergelen"
        assert canonicalize_city(**{**args, "latitude": 47.916667}).mapping_status != "verified"
        assert canonicalize_city(**{**args, "property_name": "Another Airport"}).mapping_status != "verified"


import sqlite3
import zipfile
from pathlib import Path

from scripts.run_city_normalization_refresh import (
    _merge_sqlite_exact_identity_collisions,
)

from isite2.growth.city_normalization import (
    apply_verified_city_assignments_to_overlay,
    build_city_normalization_plan,
    canonicalize_city,
)
from isite2.growth.global_city_registry import (
    build_unlocode_registry,
    load_gns_populated_places,
    load_unlocode_locations,
    match_gns_location,
    match_nearest_gns_city,
    match_property_name_official_location,
    match_unlocode_location,
)
from isite2.growth.property_identity import property_identity_key


def test_algeria_localities_roll_up_to_main_city() -> None:
    bab_ezzouar = canonicalize_city(
        country="Algeria",
        source_city="Bab Ezzouar",
        latitude=36.72,
        longitude=3.1858,
    )
    hydra = canonicalize_city(
        country="Algeria",
        source_city="Hydra",
        latitude=36.7383,
        longitude=3.03,
    )
    es_senia = canonicalize_city(
        country="Algeria",
        source_city="Es Senia",
        latitude=35.6552,
        longitude=-0.6037,
    )
    bir_el_djir = canonicalize_city(
        country="Algeria",
        source_city="Bir El Djir",
        latitude=35.7119,
        longitude=-0.5648,
    )

    assert (bab_ezzouar.canonical_city, bab_ezzouar.locality) == (
        "Algiers",
        "Bab Ezzouar",
    )
    assert (hydra.canonical_city, hydra.locality) == ("Algiers", "Hydra")
    assert es_senia.canonical_city == "Oran"
    assert bir_el_djir.canonical_city == "Oran"
    assert all(
        assignment.mapping_status == "verified"
        for assignment in (bab_ezzouar, hydra, es_senia, bir_el_djir)
    )


def test_accent_aliases_share_one_city_id() -> None:
    plain = canonicalize_city(country="Algeria", source_city="Setif")
    accented = canonicalize_city(country="Algeria", source_city="Sétif")

    assert plain.canonical_city == accented.canonical_city == "Sétif"
    assert plain.city_id == accented.city_id


def test_unknown_or_high_level_area_is_not_auto_promoted_to_city() -> None:
    unknown = canonicalize_city(country="Algeria", source_city="Wilaya d'Alger")

    assert unknown.mapping_status == "unmapped"
    assert unknown.canonical_city == ""
    assert unknown.source_city == "Wilaya d'Alger"


def test_coordinate_and_name_conflict_requires_review() -> None:
    payload = {
        "version": "test",
        "countries": {
            "Algeria": {
                "country_code": "DZ",
                "source": {
                    "authority": "Official test authority",
                    "url": "https://example.gov.dz/cities",
                    "date": "2026-08-18",
                },
                "cities": [
                    {
                        "city_id": "DZ:algiers",
                        "canonical_name": "Algiers",
                        "aliases": ["Algiers"],
                        "localities": ["Hydra"],
                        "bounds": {
                            "min_latitude": 36.6,
                            "max_latitude": 36.9,
                            "min_longitude": 2.8,
                            "max_longitude": 3.3,
                        },
                    },
                    {
                        "city_id": "DZ:oran",
                        "canonical_name": "Oran",
                        "aliases": ["Oran"],
                        "localities": ["Es Senia"],
                        "bounds": {
                            "min_latitude": 35.5,
                            "max_latitude": 35.9,
                            "min_longitude": -0.9,
                            "max_longitude": -0.3,
                        },
                    },
                ],
            }
        },
    }

    assignment = canonicalize_city(
        country="Algeria",
        source_city="Hydra",
        latitude=35.7,
        longitude=-0.6,
        config=payload,
    )

    assert assignment.mapping_status == "review_required"
    assert assignment.canonical_city == ""
    assert assignment.mapping_method == "coordinate_name_conflict"


def test_city_id_stabilizes_property_identity_across_locality_aliases() -> None:
    from_hydra = property_identity_key(
        country="Algeria",
        city="Algiers",
        city_id="DZ:algiers",
        property_name="Example Tower",
        scene_type="office_government",
    )
    from_bab_ezzouar = property_identity_key(
        country="Algeria",
        city="Bab Ezzouar",
        city_id="DZ:algiers",
        property_name="Example Tower",
        scene_type="office_government",
    )

    assert from_hydra == from_bab_ezzouar


def test_city_normalization_plan_is_idempotent_and_reports_distribution() -> None:
    rows = [
        {
            "id": "property-1",
            "canonical_name": "Hydra Tower",
            "city": "Hydra",
            "scene_type": "office_government",
            "latitude": 36.74,
            "longitude": 3.03,
        },
        {
            "id": "property-2",
            "canonical_name": "Airport Hotel",
            "city": "Bab Ezzouar",
            "scene_type": "luxury_hotel_mice",
            "latitude": 36.72,
            "longitude": 3.18,
        },
    ]

    first = build_city_normalization_plan(rows, country="Algeria")
    second_rows = [
        {
            **row,
            "city": assignment["after_city"],
            "source_city": assignment["city_assignment"]["source_city"],
        }
        for row, assignment in zip(rows, first["assignments"], strict=True)
    ]
    second = build_city_normalization_plan(second_rows, country="Algeria")

    assert first["before_city_count"] == 2
    assert first["after_city_distribution"] == {"Algiers": 2}
    assert first["unresolved_count"] == 0
    assert first["identity_collision_count"] == 0
    assert [row["property_identity_key"] for row in first["assignments"]] == [
        row["property_identity_key"] for row in second["assignments"]
    ]


def test_reference_city_requires_exact_name_and_nearby_coordinate() -> None:
    payload = {
        "version": "test",
        "countries": {
            "Brazil": {
                "sources": [{"authority": "Official location register"}],
                "cities": [
                    {
                        "city_id": "BR:unlocode:sao",
                        "canonical_name": "São Paulo",
                        "aliases": ["Sao Paulo", "São Paulo"],
                        "reference_match_required": True,
                        "max_distance_km": 75,
                        "reference_points": [
                            {"latitude": -23.55, "longitude": -46.63}
                        ],
                    }
                ],
            }
        },
    }

    verified = canonicalize_city(
        country="Brazil",
        source_city="Sao Paulo",
        latitude=-23.56,
        longitude=-46.64,
        config=payload,
    )
    conflicting = canonicalize_city(
        country="Brazil",
        source_city="Sao Paulo",
        latitude=-3.1,
        longitude=-60.0,
        config=payload,
    )

    assert verified.mapping_status == "verified"
    assert verified.mapping_method == "reference_exact_coordinate"
    assert conflicting.mapping_status == "review_required"
    assert conflicting.mapping_method == "official_name_coordinate_conflict"


def test_global_registry_only_promotes_coordinate_consistent_locations(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unlocode"
    (root / "locodes").mkdir(parents=True)
    (root / "iso-3166").mkdir(parents=True)
    (root / "iso-3166" / "CountryCodes.csv").write_text(
        "CountryCode,CountryName\nBR,Brazil\n",
        encoding="utf-8",
    )
    (root / "locodes" / "BR.csv").write_text(
        ",BR,SAO,São Paulo,Sao Paulo,SP,--34----,AA,2107,,2333S 04638W,\n",
        encoding="utf-8",
    )
    properties = [
        {
            "id": "near",
            "country": "Brazil",
            "city": "São Paulo",
            "latitude": -23.55,
            "longitude": -46.63,
        },
        {
            "id": "far",
            "country": "Brazil",
            "city": "São Paulo",
            "latitude": -3.1,
            "longitude": -60.0,
        },
    ]

    registry, audit = build_unlocode_registry(
        properties,
        locodes_dir=root / "locodes",
        country_codes_path=root / "iso-3166" / "CountryCodes.csv",
    )

    city = registry["countries"]["Brazil"]["cities"][0]
    assert city["city_id"] == "BR:unlocode:sao"
    assert city["reference_match_required"] is True
    assert audit["countries"]["Brazil"]["verified_count"] == 1
    assert audit["countries"]["Brazil"]["unresolved_count"] == 1
    assert audit["countries"]["Brazil"]["ready_to_apply"] is False


def test_gns_populated_place_is_a_coordinate_checked_unlocode_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unlocode"
    (root / "locodes").mkdir(parents=True)
    (root / "iso-3166").mkdir(parents=True)
    (root / "iso-3166" / "CountryCodes.csv").write_text(
        "CountryCode,CountryName\nAR,Argentina\n",
        encoding="utf-8",
    )
    (root / "locodes" / "AR.csv").write_text("", encoding="utf-8")
    gns_root = tmp_path / "gns"
    _write_gns_zip(
        gns_root / "ar.zip",
        [
            _gns_row(
                ufi="-1012300",
                latitude="-28.469574",
                longitude="-65.785239",
                name="San Fernando del Valle de Catamarca",
                name_type="N",
            ),
            _gns_row(
                ufi="-1012300",
                latitude="-28.469574",
                longitude="-65.785239",
                name="Catamarca",
                name_type="V",
            ),
        ],
    )

    registry, audit = build_unlocode_registry(
        [
            {
                "id": "catamarca-airport",
                "country": "Argentina",
                "city": "Catamarca",
                "latitude": -28.59,
                "longitude": -65.75,
            }
        ],
        locodes_dir=root / "locodes",
        country_codes_path=root / "iso-3166" / "CountryCodes.csv",
        gns_root=gns_root,
        gns_country_files={"Argentina": "ar.zip"},
    )

    city = registry["countries"]["Argentina"]["cities"][0]
    assert city["city_id"] == "AR:gns:-1012300"
    assert city["canonical_name"] == "San Fernando del Valle de Catamarca"
    assert city["source"]["authority"].startswith("National Geospatial")
    assignment = canonicalize_city(
        country="Argentina",
        source_city="Catamarca",
        latitude=-28.59,
        longitude=-65.75,
        config={"version": registry["version"], "countries": registry["countries"]},
    )
    assert assignment.source_authority == city["source"]["authority"]
    assert assignment.source_date == "2013-12-23"
    assert audit["countries"]["Argentina"]["verified_count"] == 1
    assert audit["countries"]["Argentina"]["match_method_counts"] == {
        "gns_reference_alias_coordinate": 1
    }


def test_gns_ignores_non_populated_features_and_requires_nearby_coordinates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "eg.zip"
    _write_gns_zip(
        path,
        [
            _gns_row(
                ufi="1",
                latitude="30.05",
                longitude="31.25",
                name="Cairo Governorate",
                feature_class="A",
                designation="ADM1",
            ),
            _gns_row(
                ufi="2",
                latitude="30.05",
                longitude="31.25",
                name="Cairo",
            ),
        ],
    )

    locations = load_gns_populated_places(path)
    assert "cairo governorate" not in locations
    verified = match_gns_location(
        source_city="Cairo",
        latitude=30.04,
        longitude=31.24,
        locations=locations,
        max_distance_km=75,
    )
    conflict = match_gns_location(
        source_city="Cairo",
        latitude=25.69,
        longitude=32.64,
        locations=locations,
        max_distance_km=75,
    )

    assert verified["status"] == "verified"
    assert verified["reason"] == "gns_reference_exact_coordinate"
    assert conflict == {
        "status": "review_required",
        "reason": "gns_official_name_coordinate_conflict",
    }


def test_property_name_official_city_hint_requires_scene_and_coordinates() -> None:
    resistencia = {
        "code": "-1010390",
        "name": "Resistencia",
        "name_ascii": "Resistencia",
        "preferred_name": "Resistencia",
        "match_aliases": ["Resistencia"],
        "exact_names_normalized": ["resistencia"],
        "feature_designation": "PPLA",
        "latitude": -27.4514,
        "longitude": -58.9867,
    }
    locations = {"resistencia": [resistencia]}

    verified = match_property_name_official_location(
        property_name="Resistencia International Airport",
        scene_type="airport_terminal",
        country="Argentina",
        latitude=-27.4499,
        longitude=-59.0561,
        locations=locations,
        source_role="gns",
        max_distance_km=75,
    )
    far_away = match_property_name_official_location(
        property_name="Resistencia International Airport",
        scene_type="airport_terminal",
        country="Argentina",
        latitude=-34.6037,
        longitude=-58.3816,
        locations=locations,
        source_role="gns",
        max_distance_km=75,
    )

    assert verified["status"] == "verified"
    assert verified["reason"] == "gns_property_name_city_exact_coordinate"
    assert verified["matched_phrase"] == "resistencia"
    assert far_away == {
        "status": "review_required",
        "reason": "property_name_official_city_coordinate_conflict",
    }


def test_property_name_city_hint_rejects_facility_names_and_interior_tokens() -> None:
    university = {
        "code": "1",
        "name": "University of Ghana",
        "preferred_name": "University of Ghana",
        "match_aliases": ["University of Ghana"],
        "exact_names_normalized": ["university of ghana"],
        "feature_designation": "PPL",
        "latitude": 5.6505,
        "longitude": -0.1962,
    }
    lucio_blanco = {
        "code": "2",
        "name": "Lucio Blanco",
        "preferred_name": "Lucio Blanco",
        "match_aliases": ["Lucio Blanco"],
        "exact_names_normalized": ["lucio blanco"],
        "feature_designation": "PPL",
        "latitude": 26.05,
        "longitude": -98.22,
    }

    blocked_facility = match_property_name_official_location(
        property_name="University of Ghana Teaching Hospital",
        scene_type="hospital",
        country="Ghana",
        latitude=5.647,
        longitude=-0.185,
        locations={"university of ghana": [university]},
        source_role="gns",
        max_distance_km=75,
    )
    interior_token = match_property_name_official_location(
        property_name="General Lucio Blanco International Airport",
        scene_type="airport_terminal",
        country="Mexico",
        latitude=26.0,
        longitude=-98.23,
        locations={"lucio blanco": [lucio_blanco]},
        source_role="gns",
        max_distance_km=75,
    )

    assert blocked_facility == {
        "status": "unmapped",
        "reason": "property_name_official_city_not_found",
    }
    assert interior_token == {
        "status": "unmapped",
        "reason": "property_name_official_city_not_found",
    }


def test_property_name_city_hint_uses_shorter_radius_for_non_admin_places() -> None:
    place = {
        "code": "3",
        "name": "Antonio Narino",
        "preferred_name": "Antonio Narino",
        "match_aliases": ["Antonio Narino"],
        "exact_names_normalized": ["antonio narino"],
        "feature_designation": "PPL",
        "latitude": 1.3,
        "longitude": -77.2,
    }

    result = match_property_name_official_location(
        property_name="Antonio Narino Airport",
        scene_type="airport_terminal",
        country="Colombia",
        latitude=1.0,
        longitude=-77.4,
        locations={"antonio narino": [place]},
        source_role="gns",
        max_distance_km=75,
    )

    assert result == {
        "status": "review_required",
        "reason": "property_name_official_city_coordinate_conflict",
    }


def test_property_name_city_hint_accepts_controlled_geographic_descriptor() -> None:
    huatulco = {
        "code": "-1701550",
        "name": "Santa Maria Huatulco",
        "preferred_name": "Santa Maria Huatulco",
        "match_aliases": ["Santa Maria Huatulco", "Huatulco"],
        "exact_names_normalized": ["santa maria huatulco"],
        "feature_designation": "PPL",
        "latitude": 15.832668,
        "longitude": -96.320628,
    }

    result = match_property_name_official_location(
        property_name="Bahías de Huatulco International Airport",
        scene_type="airport_terminal",
        country="Mexico",
        latitude=15.775278,
        longitude=-96.2625,
        locations={"huatulco": [huatulco]},
        source_role="gns",
        max_distance_km=75,
    )

    assert result["status"] == "verified"
    assert result["matched_phrase"] == "huatulco"
    assert result["location"]["preferred_name"] == "Santa Maria Huatulco"


def test_unique_nearby_gns_admin_city_can_resolve_province_labelled_airport() -> None:
    city = {
        "code": "100",
        "name": "Trelew",
        "preferred_name": "Trelew",
        "match_aliases": ["Trelew"],
        "feature_designation": "PPLA2",
        "subdivision": "04",
        "latitude": -43.25,
        "longitude": -65.31,
    }
    distant_city = {
        "code": "200",
        "name": "Rawson",
        "preferred_name": "Rawson",
        "match_aliases": ["Rawson"],
        "feature_designation": "PPLA",
        "subdivision": "04",
        "latitude": -43.30,
        "longitude": -65.10,
    }

    result = match_nearest_gns_city(
        scene_type="airport_terminal",
        latitude=-43.2098,
        longitude=-65.2838,
        locations={"trelew": [city], "rawson": [distant_city]},
        source_city="Chubut Province",
        admin1_aliases={"chubut": {"04"}},
    )

    assert result["status"] == "verified"
    assert result["reason"] == "gns_nearest_admin_coordinate_unique"
    assert result["location"]["preferred_name"] == "Trelew"


def test_nearest_gns_city_rejects_candidate_in_a_different_admin_area() -> None:
    takeo = {
        "code": "100",
        "name": "Takeo",
        "preferred_name": "Takeo",
        "match_aliases": ["Takeo"],
        "feature_designation": "PPLA",
        "subdivision": "19",
        "latitude": 10.99,
        "longitude": 104.78,
    }

    result = match_nearest_gns_city(
        scene_type="airport_terminal",
        latitude=11.08,
        longitude=104.88,
        locations={"takeo": [takeo]},
        source_city="Kandal Province",
        admin1_aliases={"kandal": {"07"}},
    )

    assert result == {
        "status": "unmapped",
        "reason": "gns_nearest_city_not_within_source_admin_area",
    }


def test_nearest_gns_city_rejects_ambiguous_dense_places() -> None:
    first = {
        "code": "100",
        "name": "First City",
        "preferred_name": "First City",
        "match_aliases": ["First City"],
        "feature_designation": "PPLA2",
        "latitude": 10.0,
        "longitude": 10.0,
    }
    second = {
        "code": "200",
        "name": "Second City",
        "preferred_name": "Second City",
        "match_aliases": ["Second City"],
        "feature_designation": "PPLA2",
        "latitude": 10.02,
        "longitude": 10.0,
    }

    result = match_nearest_gns_city(
        scene_type="office_government",
        latitude=10.01,
        longitude=10.0,
        locations={"first city": [first], "second city": [second]},
    )

    assert result == {
        "status": "review_required",
        "reason": "gns_nearest_city_ambiguous",
    }


def test_nearest_gns_city_requires_coordinates_and_a_close_candidate() -> None:
    city = {
        "code": "100",
        "name": "Official City",
        "preferred_name": "Official City",
        "match_aliases": ["Official City"],
        "feature_designation": "PPLA",
        "latitude": 10.0,
        "longitude": 10.0,
    }
    locations = {"official city": [city]}

    missing_coordinates = match_nearest_gns_city(
        scene_type="airport_terminal",
        latitude=None,
        longitude=None,
        locations=locations,
    )
    too_far = match_nearest_gns_city(
        scene_type="office_government",
        latitude=11.0,
        longitude=11.0,
        locations=locations,
    )

    assert missing_coordinates == {
        "status": "unmapped",
        "reason": "gns_nearest_city_coordinate_required",
    }
    assert too_far == {
        "status": "unmapped",
        "reason": "gns_nearest_city_not_within_threshold",
    }


def test_unscoped_airport_cannot_roll_up_to_a_distant_admin_city() -> None:
    city = {
        "code": "100",
        "name": "Tourist City",
        "preferred_name": "Tourist City",
        "match_aliases": ["Tourist City"],
        "feature_designation": "PPLA",
        "latitude": 16.60,
        "longitude": -22.90,
    }

    result = match_nearest_gns_city(
        scene_type="airport_terminal",
        latitude=16.7388,
        longitude=-22.9511,
        locations={"tourist city": [city]},
        source_city="Missing Airport City",
    )

    assert result == {
        "status": "unmapped",
        "reason": "gns_nearest_city_not_within_threshold",
    }


def test_nearest_gns_city_registry_match_is_property_scoped(tmp_path: Path) -> None:
    root = tmp_path / "unlocode"
    (root / "locodes").mkdir(parents=True)
    (root / "iso-3166").mkdir(parents=True)
    (root / "iso-3166" / "CountryCodes.csv").write_text(
        "CountryCode,CountryName\nAR,Argentina\n",
        encoding="utf-8",
    )
    (root / "locodes" / "AR.csv").write_text("", encoding="utf-8")
    gns_root = tmp_path / "gns"
    _write_gns_zip(
        gns_root / "ar.zip",
        [
            _gns_row(
                ufi="100",
                latitude="-43.25",
                longitude="-65.31",
                name="Trelew",
                designation="PPLA2",
                admin1="04",
            ),
            _gns_row(
                ufi="200",
                latitude="-43.30",
                longitude="-65.10",
                name="Rawson",
                designation="PPLA",
                admin1="04",
            ),
            _gns_row(
                ufi="300",
                latitude="-43.30",
                longitude="-65.10",
                name="Chubut Province",
                feature_class="A",
                designation="ADM1",
                admin1="04",
            ),
        ],
    )

    registry, audit = build_unlocode_registry(
        [
            {
                "id": "province-airport",
                "canonical_name": "Almirante Marcos A. Zar Airport",
                "country": "Argentina",
                "city": "Chubut Province",
                "scene_type": "airport_terminal",
                "latitude": -43.2098,
                "longitude": -65.2838,
            }
        ],
        locodes_dir=root / "locodes",
        country_codes_path=root / "iso-3166" / "CountryCodes.csv",
        gns_root=gns_root,
        gns_country_files={"Argentina": "ar.zip"},
    )

    country = registry["countries"]["Argentina"]
    assert country["cities"][0]["canonical_name"] == "Trelew"
    assert "Chubut Province" not in country["cities"][0]["aliases"]
    assert country["property_overrides"] == [
        {
            "property_id": "province-airport",
            "property_name": "Almirante Marcos A. Zar Airport",
            "scene_type": "airport_terminal",
            "source_city": "Chubut Province",
            "city_id": "AR:gns:100",
            "mapping_method": "gns_nearest_admin_coordinate_unique",
            "matched_phrase": "",
            "max_distance_km": 25.0,
        }
    ]
    assert audit["countries"]["Argentina"]["ready_to_apply"] is True


def test_property_name_city_override_is_property_scoped_not_a_province_alias() -> None:
    payload = {
        "version": "test",
        "countries": {
            "Argentina": {
                "property_overrides": [
                    {
                        "property_id": "airport-1",
                        "property_name": "Resistencia International Airport",
                        "scene_type": "airport_terminal",
                        "source_city": "Chaco Province",
                        "city_id": "AR:gns:resistencia",
                        "mapping_method": "gns_property_name_city_exact_coordinate",
                        "max_distance_km": 75,
                    }
                ],
                "cities": [
                    {
                        "city_id": "AR:gns:resistencia",
                        "canonical_name": "Resistencia",
                        "aliases": ["Resistencia"],
                        "reference_match_required": True,
                        "reference_points": [
                            {"latitude": -27.4514, "longitude": -58.9867}
                        ],
                        "source": {"authority": "NGA GNS"},
                    }
                ],
            }
        },
    }

    matched = canonicalize_city(
        country="Argentina",
        source_city="Chaco Province",
        property_id="airport-1",
        property_name="Resistencia International Airport",
        scene_type="airport_terminal",
        latitude=-27.4499,
        longitude=-59.0561,
        config=payload,
    )
    unrelated = canonicalize_city(
        country="Argentina",
        source_city="Chaco Province",
        property_id="hospital-1",
        property_name="Provincial Hospital",
        scene_type="hospital",
        latitude=-27.45,
        longitude=-58.99,
        config=payload,
    )

    assert matched.mapping_status == "verified"
    assert matched.canonical_city == "Resistencia"
    assert matched.mapping_method == "gns_property_name_city_exact_coordinate"
    assert unrelated.mapping_status == "unmapped"


def test_property_name_city_override_blocks_stale_property_metadata() -> None:
    payload = {
        "version": "test",
        "countries": {
            "Argentina": {
                "property_overrides": [
                    {
                        "property_id": "airport-1",
                        "property_name": "Resistencia International Airport",
                        "scene_type": "airport_terminal",
                        "city_id": "AR:gns:resistencia",
                        "max_distance_km": 75,
                    }
                ],
                "cities": [
                    {
                        "city_id": "AR:gns:resistencia",
                        "canonical_name": "Resistencia",
                        "reference_points": [
                            {"latitude": -27.4514, "longitude": -58.9867}
                        ],
                    }
                ],
            }
        },
    }

    stale = canonicalize_city(
        country="Argentina",
        source_city="Chaco Province",
        property_id="airport-1",
        property_name="Different Airport",
        scene_type="airport_terminal",
        latitude=-27.45,
        longitude=-58.99,
        config=payload,
    )

    assert stale.mapping_status == "review_required"
    assert stale.mapping_method == "property_override_input_mismatch"


def test_unique_official_location_without_reference_coordinate_is_retained(
    tmp_path: Path,
) -> None:
    path = tmp_path / "AF.csv"
    path.write_text(
        ",AF,KBL,Kabul,Kabul,,--345---,AI,9601,,,\n",
        encoding="utf-8",
    )

    locations = load_unlocode_locations(path)
    result = match_unlocode_location(
        source_city="Kabul",
        latitude=34.53,
        longitude=69.17,
        locations=locations,
        max_distance_km=75,
    )

    assert result["status"] == "verified"
    assert result["reason"] == "official_name_unique_no_reference"
    assert result["location"]["latitude"] is None
    assert result["location"]["longitude"] is None


def test_controlled_official_alias_requires_coordinate_consistency(
    tmp_path: Path,
) -> None:
    path = tmp_path / "EG.csv"
    path.write_text(
        ",EG,CAI,El Qahira (Cairo),El Qahira (Cairo),C,---45---,AI,2301,,3003N 03115E,\n",
        encoding="utf-8",
    )
    locations = load_unlocode_locations(path)

    verified = match_unlocode_location(
        source_city="Cairo",
        latitude=30.05,
        longitude=31.25,
        locations=locations,
        max_distance_km=75,
    )
    conflicting = match_unlocode_location(
        source_city="Cairo",
        latitude=25.7,
        longitude=32.6,
        locations=locations,
        max_distance_km=75,
    )

    assert verified["status"] == "verified"
    assert verified["reason"] == "reference_alias_coordinate"
    assert verified["location"]["preferred_name"] == "Cairo"
    assert conflicting == {
        "status": "review_required",
        "reason": "official_name_coordinate_conflict",
    }


def test_conflicting_exact_name_can_use_strong_nearby_official_variant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "BO.csv"
    path.write_text(
        "\n".join(
            [
                ",BO,SCX,Santa Cruz de la Sierra,Santa Cruz de la Sierra,SC,"
                "12345---,AI,1207,,1730S 06310W,",
                ",BO,SRZ,Santa Cruz,Santa Cruz,SC,12345---,AI,1207,,1748S 06310W,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    locations = load_unlocode_locations(path)

    result = match_unlocode_location(
        source_city="Santa Cruz de la Sierra",
        latitude=-17.80,
        longitude=-63.17,
        locations=locations,
        max_distance_km=15,
    )

    assert result["status"] == "verified"
    assert result["reason"] == "reference_name_variant_coordinate"
    assert result["location"]["code"] == "SRZ"


def test_conflicting_exact_name_does_not_use_weak_nearby_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "AR.csv"
    path.write_text(
        "\n".join(
            [
                ",AR,BUE,Buenos Aires,Buenos Aires,C,12345---,AI,1207,,3436S 05827W,",
                ",AR,JNI,Junín,Junin,B,12345---,AI,1207,,3435S 06057W,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    locations = load_unlocode_locations(path)

    result = match_unlocode_location(
        source_city="Buenos Aires",
        latitude=-34.59,
        longitude=-60.95,
        locations=locations,
        max_distance_km=15,
    )

    assert result == {
        "status": "review_required",
        "reason": "official_name_coordinate_conflict",
    }


def test_unlocode_change_record_adds_official_legacy_alias(tmp_path: Path) -> None:
    path = tmp_path / "RU.csv"
    path.write_text(
        "\n".join(
            [
                ",RU,MOW,Moskva,Moskva,MOW,123456--,AI,1207,,5545N 03736E,",
                "=,RU,,Moscow = Moskva,Moscow = Moskva,,,,,,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    locations = load_unlocode_locations(path)
    result = match_unlocode_location(
        source_city="Moscow",
        latitude=55.75,
        longitude=37.61,
        locations=locations,
        max_distance_km=75,
    )

    assert result["status"] == "verified"
    assert result["reason"] == "reference_alias_coordinate"
    assert result["location"]["code"] == "MOW"


def test_former_city_name_is_alias_not_canonical_display_name(tmp_path: Path) -> None:
    path = tmp_path / "RU.csv"
    path.write_text(
        ",RU,LED,Saint Petersburg (ex Leningrad),Saint Petersburg (ex Leningrad),"
        "SPE,12345---,AI,0401,,5953N 03015E,\n",
        encoding="utf-8",
    )

    locations = load_unlocode_locations(path)
    result = match_unlocode_location(
        source_city="Saint Petersburg",
        latitude=59.93,
        longitude=30.31,
        locations=locations,
        max_distance_km=75,
    )

    assert result["status"] == "verified"
    assert result["location"]["preferred_name"] == "Saint Petersburg"
    assert "Leningrad" in result["location"]["match_aliases"]


def test_nearby_official_name_variant_can_be_verified(tmp_path: Path) -> None:
    path = tmp_path / "DE.csv"
    path.write_text(
        ",DE,MUC,München,Munchen,BY,12345---,AI,1207,,4808N 01135E,\n",
        encoding="utf-8",
    )
    locations = load_unlocode_locations(path)

    result = match_unlocode_location(
        source_city="Munich",
        latitude=48.14,
        longitude=11.58,
        locations=locations,
        max_distance_km=75,
    )

    assert result["status"] == "verified"
    assert result["reason"] == "reference_name_variant_coordinate"
    assert result["location"]["code"] == "MUC"


def test_nearby_unrelated_official_location_is_not_auto_selected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "EG.csv"
    path.write_text(
        ",EG,CAI,Cairo,Cairo,C,12345---,AI,1207,,3003N 03115E,\n",
        encoding="utf-8",
    )
    locations = load_unlocode_locations(path)

    result = match_unlocode_location(
        source_city="New Administrative Capital",
        latitude=30.01,
        longitude=31.72,
        locations=locations,
        max_distance_km=75,
    )

    assert result == {"status": "unmapped", "reason": "official_name_not_found"}


def test_global_registry_uses_one_official_variant_for_a_source_city_group(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unlocode"
    (root / "locodes").mkdir(parents=True)
    (root / "iso-3166").mkdir(parents=True)
    (root / "iso-3166" / "CountryCodes.csv").write_text(
        "CountryCode,CountryName\nGR,Greece\n",
        encoding="utf-8",
    )
    (root / "locodes" / "GR.csv").write_text(
        "\n".join(
            [
                ",GR,ATH,Athínai,Athinai,I,---45---,AI,1701,,3759N 02344E,",
                ",GR,KLA,Kallithéa/Athínai,Kallithea/Athinai,,--3-----,AA,1307,,3757N 02342E,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    properties = [
        {
            "id": "city-center",
            "country": "Greece",
            "city": "Athens",
            "latitude": 37.98,
            "longitude": 23.73,
        },
        {
            "id": "south-city",
            "country": "Greece",
            "city": "Athens",
            "latitude": 37.95,
            "longitude": 23.70,
        },
    ]

    registry, audit = build_unlocode_registry(
        properties,
        locodes_dir=root / "locodes",
        country_codes_path=root / "iso-3166" / "CountryCodes.csv",
    )

    assert audit["countries"]["Greece"]["unresolved_count"] == 0
    assert audit["countries"]["Greece"]["verified_count"] == 2
    assert [city["city_id"] for city in registry["countries"]["Greece"]["cities"]] == [
        "GR:unlocode:ath"
    ]


def test_subdivision_name_is_reported_as_admin_area_not_missing_official_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "BR.csv"
    path.write_text(
        ",BR,RBR,Rio Branco,Rio Branco,AC,--34----,AA,2107,,0958S 06748W,\n",
        encoding="utf-8",
    )
    locations = load_unlocode_locations(path)

    result = match_unlocode_location(
        source_city="Acre",
        latitude=-9.97,
        longitude=-67.81,
        locations=locations,
        max_distance_km=75,
        subdivision_names={"acre"},
    )

    assert result == {
        "status": "unmapped",
        "reason": "high_level_admin_area_not_city",
    }


_GNS_COLUMNS = [
    "RC",
    "UFI",
    "UNI",
    "LAT",
    "LONG",
    "DMS_LAT",
    "DMS_LONG",
    "MGRS",
    "JOG",
    "FC",
    "DSG",
    "PC",
    "CC1",
    "ADM1",
    "POP",
    "ELEV",
    "CC2",
    "NT",
    "LC",
    "SHORT_FORM",
    "GENERIC",
    "SORT_NAME_RO",
    "FULL_NAME_RO",
    "FULL_NAME_ND_RO",
    "SORT_NAME_RG",
    "FULL_NAME_RG",
    "FULL_NAME_ND_RG",
    "NOTE",
    "MODIFY_DATE",
    "DISPLAY",
]


def _gns_row(
    *,
    ufi: str,
    latitude: str,
    longitude: str,
    name: str,
    name_type: str = "N",
    feature_class: str = "P",
    designation: str = "PPL",
    admin1: str = "",
) -> dict[str, str]:
    row = dict.fromkeys(_GNS_COLUMNS, "")
    row.update(
        {
            "RC": "1",
            "UFI": ufi,
            "UNI": f"{ufi}-name",
            "LAT": latitude,
            "LONG": longitude,
            "FC": feature_class,
            "DSG": designation,
            "CC1": "XX",
            "ADM1": admin1,
            "NT": name_type,
            "SORT_NAME_RO": name.upper().replace(" ", ""),
            "FULL_NAME_RO": name,
            "FULL_NAME_ND_RO": name,
            "MODIFY_DATE": "2013-12-23",
        }
    )
    return row


def _write_gns_zip(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(_GNS_COLUMNS)]
    lines.extend("\t".join(row.get(column, "") for column in _GNS_COLUMNS) for row in rows)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{path.stem}.txt", "\n".join(lines) + "\n")


def test_duplicate_coordinate_free_official_names_remain_ambiguous(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ZZ.csv"
    path.write_text(
        "\n".join(
            [
                ",ZZ,AA1,Springfield,Springfield,A,--3-----,AA,2107,,",
                ",ZZ,AA2,Springfield,Springfield,B,--3-----,AA,2107,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    locations = load_unlocode_locations(path)

    result = match_unlocode_location(
        source_city="Springfield",
        latitude=10,
        longitude=20,
        locations=locations,
        max_distance_km=75,
    )

    assert result == {
        "status": "review_required",
        "reason": "ambiguous_official_location_without_reference",
    }


def test_generated_official_alias_keeps_assignment_method_auditable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unlocode"
    (root / "locodes").mkdir(parents=True)
    (root / "iso-3166").mkdir(parents=True)
    (root / "iso-3166" / "CountryCodes.csv").write_text(
        "CountryCode,CountryName\nEG,Egypt\n",
        encoding="utf-8",
    )
    (root / "locodes" / "EG.csv").write_text(
        ",EG,CAI,El Qahira (Cairo),El Qahira (Cairo),C,---45---,AI,2301,,3003N 03115E,\n",
        encoding="utf-8",
    )
    registry, _ = build_unlocode_registry(
        [
            {
                "id": "cairo",
                "country": "Egypt",
                "city": "Cairo",
                "latitude": 30.05,
                "longitude": 31.25,
            }
        ],
        locodes_dir=root / "locodes",
        country_codes_path=root / "iso-3166" / "CountryCodes.csv",
    )
    payload = {"version": "test", "countries": registry["countries"]}

    assignment = canonicalize_city(
        country="Egypt",
        source_city="Cairo",
        latitude=30.05,
        longitude=31.25,
        config=payload,
    )

    assert assignment.mapping_status == "verified"
    assert assignment.canonical_city == "Cairo"
    assert assignment.mapping_method == "reference_alias_coordinate"


def test_near_duplicate_official_codes_collapse_to_one_city(tmp_path: Path) -> None:
    path = tmp_path / "VN.csv"
    path.write_text(
        "\n".join(
            [
                ",VN,KUG,Pleiku,Pleiku,30,--3-----,AA,2107,,1359N 10800E,",
                ",VN,PXU,Pleiku,Pleiku,28,--34----,AA,2107,,1400N 10801E,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    locations = load_unlocode_locations(path)

    assert len(locations["pleiku"]) == 1
    assert locations["pleiku"][0]["code"] == "KUG"
    assert locations["pleiku"][0]["duplicate_codes"] == ["KUG", "PXU"]


def test_verified_only_overlay_apply_preserves_unresolved_city() -> None:
    payload = {
        "version": "test",
        "countries": {
            "Example": {
                "sources": [{"authority": "Official city register"}],
                "cities": [
                    {
                        "city_id": "EX:city:capital",
                        "canonical_name": "Capital City",
                        "aliases": ["Capital City"],
                    }
                ],
            }
        },
    }
    overlay = {
        "countries": {
            "Example": {
                "candidates": [
                    {
                        "property_id": "verified",
                        "property_name": "Capital Tower",
                        "city": "Capital City",
                        "scene_type": "office_government",
                        "coordinate": {"latitude": 1, "longitude": 2},
                    },
                    {
                        "property_id": "unresolved",
                        "property_name": "Regional Airport",
                        "city": "Example Province",
                        "city_id": "EX:stale",
                        "property_identity_key": "stale-key",
                        "scene_type": "airport_terminal",
                        "coordinate": {"latitude": 3, "longitude": 4},
                    },
                ]
            }
        }
    }

    result = apply_verified_city_assignments_to_overlay(
        overlay,
        country="Example",
        config=payload,
        verified_only=True,
    )
    verified, unresolved = overlay["countries"]["Example"]["candidates"]

    assert result == {"updated": 1, "skipped": 1, "audited": 2}
    assert verified["city_id"] == "EX:city:capital"
    assert unresolved["city"] == "Example Province"
    assert "city_id" not in unresolved
    assert "property_identity_key" not in unresolved
    assert unresolved["city_assignment"]["mapping_status"] == "unmapped"


def test_generated_unresolved_reason_is_preserved_in_assignment() -> None:
    payload = {
        "version": "test",
        "countries": {
            "Brazil": {
                "sources": [{"authority": "UNECE"}],
                "unresolved_localities": {
                    "acre": "high_level_admin_area_not_city",
                },
                "cities": [],
            }
        },
    }

    assignment = canonicalize_city(
        country="Brazil",
        source_city="Acre",
        latitude=-9.97,
        longitude=-67.81,
        config=payload,
    )

    assert assignment.mapping_status == "unmapped"
    assert assignment.mapping_method == "high_level_admin_area_not_city"


def test_property_hint_reuses_matching_unlocode_city_instead_of_duplicating_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "official"
    (root / "locodes").mkdir(parents=True)
    (root / "iso-3166").mkdir(parents=True)
    (root / "gns").mkdir(parents=True)
    (root / "iso-3166" / "CountryCodes.csv").write_text(
        "CountryCode,CountryName\nBF,Burkina Faso\n",
        encoding="utf-8",
    )
    (root / "locodes" / "BF.csv").write_text(
        ",BF,OU,Ouagadougou,Ouagadougou,,--345---,AI,9601,,,\n",
        encoding="utf-8",
    )
    _write_gns_zip(
        root / "gns" / "bf.zip",
        [
            _gns_row(
                ufi="1",
                latitude="12.3714",
                longitude="-1.5197",
                name="Ouagadougou",
                designation="PPLC",
            )
        ],
    )
    properties = [
        {
            "id": "hotel",
            "canonical_name": "Capital Hotel",
            "country": "Burkina Faso",
            "city": "Ouagadougou",
            "source_city": "Ouagadougou",
            "scene_type": "luxury_hotel_mice",
            "latitude": 12.37,
            "longitude": -1.52,
        },
        {
            "id": "airport",
            "canonical_name": "Ouagadougou-Donsin Airport",
            "country": "Burkina Faso",
            "city": "Donsin",
            "source_city": "Donsin",
            "scene_type": "airport_terminal",
            "latitude": 12.58,
            "longitude": -1.43,
        },
    ]

    registry, audit = build_unlocode_registry(
        properties,
        locodes_dir=root / "locodes",
        country_codes_path=root / "iso-3166" / "CountryCodes.csv",
        gns_root=root / "gns",
        gns_country_files={"Burkina Faso": "bf.zip"},
    )
    country = registry["countries"]["Burkina Faso"]
    plan = build_city_normalization_plan(
        properties,
        country="Burkina Faso",
        config={"version": "test", "countries": registry["countries"]},
    )

    assert audit["countries"]["Burkina Faso"]["verified_count"] == 2
    assert [city["city_id"] for city in country["cities"]] == ["BF:unlocode:ou"]
    assert country["property_overrides"][0]["city_id"] == "BF:unlocode:ou"
    assert "Donsin" not in country["cities"][0]["aliases"]
    assert plan["unresolved_count"] == 0
    assert plan["identity_collision_count"] == 0


def test_unlocode_without_coordinates_is_cross_checked_by_gns_before_acceptance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "official"
    (root / "locodes").mkdir(parents=True)
    (root / "iso-3166").mkdir(parents=True)
    (root / "gns").mkdir(parents=True)
    (root / "iso-3166" / "CountryCodes.csv").write_text(
        "CountryCode,CountryName\nMX,Mexico\n",
        encoding="utf-8",
    )
    (root / "locodes" / "MX.csv").write_text(
        ",MX,OAX,Oaxaca,Oaxaca,,--345---,AI,9601,,,\n",
        encoding="utf-8",
    )
    _write_gns_zip(
        root / "gns" / "mx.zip",
        [
            _gns_row(
                ufi="1",
                latitude="17.0606",
                longitude="-96.7253",
                name="Oaxaca",
                designation="PPLA",
            ),
            _gns_row(
                ufi="2",
                latitude="15.8768",
                longitude="-97.0744",
                name="Puerto Escondido",
            ),
        ],
    )
    properties = [
        {
            "id": "airport",
            "canonical_name": "Puerto Escondido International Airport",
            "country": "Mexico",
            "city": "Oaxaca",
            "source_city": "Oaxaca",
            "scene_type": "airport_terminal",
            "latitude": 15.8767,
            "longitude": -97.0889,
        }
    ]

    registry, audit = build_unlocode_registry(
        properties,
        locodes_dir=root / "locodes",
        country_codes_path=root / "iso-3166" / "CountryCodes.csv",
        gns_root=root / "gns",
        gns_country_files={"Mexico": "mx.zip"},
    )
    country = registry["countries"]["Mexico"]

    assert audit["countries"]["Mexico"]["verified_count"] == 1
    assert country["property_overrides"][0]["property_id"] == "airport"
    assert country["property_overrides"][0]["city_id"] == "MX:gns:2"


def test_exact_city_identity_merge_backs_up_and_moves_child_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "city_merge.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE properties (
            id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            country TEXT NOT NULL,
            city TEXT NOT NULL,
            city_id TEXT,
            scene_type TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            geocode_precision TEXT,
            map_source TEXT,
            map_source_date TEXT,
            google_maps_link TEXT,
            coordinate_status TEXT,
            hero_image TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE evidence_items (
            id TEXT PRIMARY KEY,
            property_id TEXT NOT NULL REFERENCES properties(id),
            field_value TEXT NOT NULL
        );
        CREATE TABLE derived_refresh_status (
            property_id TEXT PRIMARY KEY,
            status TEXT NOT NULL
        );
        CREATE TABLE public_api_property_index (
            property_id TEXT PRIMARY KEY,
            packet TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        """
        INSERT INTO properties VALUES (
            ?, 'Example Airport', 'Example', ?, ?, 'airport_terminal',
            ?, ?, ?, ?, NULL, NULL, ?, NULL, '2026-01-01', '2026-01-02'
        )
        """,
        [
            (
                "keeper",
                "Example City",
                "EX:city",
                1.0,
                2.0,
                "city centroid",
                "Source A",
                "Cross-Checked",
            ),
            (
                "duplicate",
                "Example Province",
                None,
                1.01,
                2.01,
                "property centroid",
                "Source B",
                "Verified",
            ),
        ],
    )
    connection.executemany(
        "INSERT INTO evidence_items VALUES (?, ?, ?)",
        [("e1", "keeper", "one"), ("e2", "duplicate", "two")],
    )
    connection.execute(
        "INSERT INTO derived_refresh_status VALUES ('keeper', 'ready')"
    )
    connection.execute(
        "INSERT INTO public_api_property_index VALUES ('duplicate', 'stale')"
    )
    connection.commit()
    connection.close()
    assignment = {"city_id": "EX:city", "mapping_status": "verified"}
    reports = [
        {
            "country": "Example",
            "identity_collisions": [
                {
                    "property_identity_key": "example|airport|ex city|example airport",
                    "properties": [
                        {"property_id": "keeper", "city_assignment": assignment},
                        {"property_id": "duplicate", "city_assignment": assignment},
                    ],
                }
            ],
        }
    ]

    audit = _merge_sqlite_exact_identity_collisions(
        database_url=f"sqlite+pysqlite:///{database_path}",
        reports=reports,
        audit_path=tmp_path / "merge_audit.json",
    )

    connection = sqlite3.connect(database_path)
    assert connection.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM evidence_items WHERE property_id = 'keeper'"
    ).fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM public_api_property_index"
    ).fetchone()[0] == 0
    latitude, status = connection.execute(
        "SELECT latitude, coordinate_status FROM properties WHERE id = 'keeper'"
    ).fetchone()
    connection.close()
    assert (latitude, status) == (1.01, "Verified")
    assert audit["removed_property_count"] == 1
    assert Path(audit["backup"]).exists()
