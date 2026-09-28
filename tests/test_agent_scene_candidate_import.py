from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import yaml

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_apac_other_scene_agent_import.py"
)
_SPEC = importlib.util.spec_from_file_location("run_apac_other_scene_agent_import", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
agent_import = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(agent_import)


def test_real_image_placeholder_tokens_do_not_match_place_name_substrings() -> None:
    assert agent_import._real_image(
        "https://upload.wikimedia.org/wikipedia/commons/Stade_municipal_de_Bohicon_tonnerre.jpg"
    )
    assert agent_import._real_image(
        "https://assets.example.com/hotels/City_Lodge_Hotel_Maputo.jpg"
    )


def test_real_image_rejects_actual_placeholder_icon_logo_and_map_assets() -> None:
    assert not agent_import._real_image("https://cdn.example.com/assets/icon.png")
    assert not agent_import._real_image("https://cdn.example.com/img/logo_startup.png")
    assert not agent_import._real_image("https://maps.example.com/static-map/property.png")
    assert not agent_import._real_image("https://googleads.g.doubleclick.net/example.jpg")


def test_hero_image_keeps_missing_image_explicit() -> None:
    assert agent_import._hero_image({"image_url": ""}) is None
    assert agent_import._hero_image({}) is None


def test_hero_image_builds_payload_for_real_image() -> None:
    item = {
        "property_name": "Example Venue",
        "image_url": "https://example.com/venue.jpg",
        "image_source_name": "Venue operator",
        "image_source_url": "https://example.com/venue",
    }

    assert agent_import._hero_image(item) == {
        "url": "https://example.com/venue.jpg",
        "alt_text": "Example Venue public property image",
        "source_name": "Venue operator",
        "source_url": "https://example.com/venue",
    }


def test_cruise_port_accepts_passengers_or_scheduled_call_frequency() -> None:
    assert agent_import._metric(
        {
            "scene_type": "cruise_port",
            "objective_metric_name": "international_cruise_calls",
            "objective_metric_value": "3 scheduled calls visible for August 2026",
        }
    ) == ("international_cruise_frequency", "international_cruise_frequency")
    assert agent_import._metric(
        {
            "scene_type": "cruise_port",
            "objective_metric_name": "passenger throughput",
            "objective_metric_value": "120,000 cruise passengers in 2025",
        }
    ) == ("passenger_throughput", "passenger_throughput")


def test_evidence_value_falls_back_to_structured_evidence_rows() -> None:
    item = {
        "evidence": [
            {
                "source_name": "Venue operator",
                "source_url": "https://example.com/facts",
            }
        ]
    }

    assert (
        agent_import._evidence_value(item, "evidence_source_url", "source_url")
        == "https://example.com/facts"
    )
    assert (
        agent_import._evidence_value(item, "evidence_source_name", "source_name")
        == "Venue operator"
    )


def test_evidence_value_prefers_flat_import_field() -> None:
    item = {
        "evidence_source_url": "https://example.com/flat",
        "evidence": [{"source_url": "https://example.com/nested"}],
    }

    assert (
        agent_import._evidence_value(item, "evidence_source_url", "source_url")
        == "https://example.com/flat"
    )


def test_geocode_precision_adds_scene_context_for_generic_exact_centroid() -> None:
    assert "cruise port" in agent_import._geocode_precision(
        {
            "scene_type": "cruise_port",
            "geocode_precision": "exact named venue feature centroid",
        }
    )
    assert "airport" in agent_import._geocode_precision(
        {
            "scene_type": "airport_terminal",
            "geocode_precision": "exact named venue feature centroid",
        }
    )


def test_mall_nla_is_accepted_as_mixed_use_area() -> None:
    assert agent_import._metric(
        {
            "scene_type": "mall_mixed_use",
            "objective_metric_name": "mixed_use_nla",
            "objective_metric_value": "26,112 square metres NLA",
        }
    ) == ("mixed_use_area", "mixed_use_area")


def test_transport_line_count_wins_over_generic_passenger_wording() -> None:
    item = {
        "scene_type": "transport_hub",
        "objective_metric_name": "line_count",
        "objective_metric_value": "2 passenger rail lines connect at the station",
    }

    assert agent_import._accepted_transport_metric(item)
    assert agent_import._metric(item) == ("line_count", "line_count")


def test_transport_generic_passenger_text_is_not_assumed_daily_ridership() -> None:
    item = {
        "scene_type": "transport_hub",
        "objective_metric_name": "passenger services",
        "objective_metric_value": "major passenger station",
    }

    assert not agent_import._accepted_transport_metric(item)
    assert agent_import._metric(item) is None


def test_objective_metric_name_alone_does_not_supply_a_value() -> None:
    item = {
        "scene_type": "stadium",
        "objective_metric_name": "seat_count",
        "objective_metric_value": "",
    }

    assert not agent_import._has_objective_metric_value(item)
    assert agent_import._has_objective_metric_value(
        {"objective_metric_value": "25,000 seats"}
    )


def test_designated_missing_metric_draft_uses_context_identity_evidence() -> None:
    draft = agent_import._draft(
        {
            "country": "Algeria",
            "city": "Algiers",
            "property_name": "Designated Office Tower",
            "scene_type": "office_government",
            "latitude": 36.75,
            "longitude": 3.05,
            "evidence_source_name": "Official entity page",
            "evidence_source_url": "https://example.org/designated-office",
            "image_url": "https://example.org/designated-office.jpg",
        },
        ("property_identity", "property_identity"),
        {
            "countries": {
                "Algeria": {
                    "bbox": {
                        "min_latitude": 18.8,
                        "max_latitude": 37.2,
                        "min_longitude": -8.7,
                        "max_longitude": 12.0,
                    }
                }
            }
        },
        source_type="designated_list",
        source_date="2026-08-10",
        region="North Africa",
        designated_missing_metric=True,
    )

    assert draft.evidence_type == "Context"
    assert draft.field_group == "property_identity"
    assert "primary metric missing" in draft.field_value


def test_mark_designated_missing_metric_candidates_is_key_scoped(tmp_path) -> None:
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "version": "runtime-0.1",
                "countries": {
                    "Algeria": {
                        "candidates": [
                            {
                                "property_name": "Target Tower",
                                "city": "Algiers",
                                "scene_type": "office_government",
                            },
                            {
                                "property_name": "Other Tower",
                                "city": "Algiers",
                                "scene_type": "office_government",
                            },
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    key = agent_import.candidate_key(
        "Algeria", "Algiers", "Target Tower", "office_government"
    )

    agent_import._mark_designated_missing_metric_candidates(
        overlay_path,
        {key},
        designation_source="ISITE LIST.xlsx",
    )

    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    target, other = overlay["countries"]["Algeria"]["candidates"]
    assert target["designated_lead"] is True
    assert target["primary_metric_status"] == "missing"
    assert target["designation_source"] == "ISITE LIST.xlsx"
    assert "designated_lead" not in other


def test_image_only_change_updates_existing_overlay_candidate(tmp_path) -> None:
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "version": "runtime-0.1",
                "countries": {
                    "Morocco": {
                        "candidates": [
                            {
                                "property_name": "Example Station",
                                "city": "Rabat",
                                "scene_type": "transport_hub",
                                "evidence": [],
                            }
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    draft = SimpleNamespace(
        country="Morocco",
        city="Rabat",
        property_name="Example Station",
        scene_type="transport_hub",
        hero_image={
            "url": "https://example.org/station.jpg",
            "source_url": "https://example.org/station",
        },
    )

    assert agent_import._sync_accepted_hero_images_to_overlay(
        overlay_path,
        [draft],
    ) == 1
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    candidate = overlay["countries"]["Morocco"]["candidates"][0]
    assert candidate["hero_image"]["url"] == "https://example.org/station.jpg"
    assert agent_import._sync_accepted_hero_images_to_overlay(
        overlay_path,
        [draft],
    ) == 0


def test_accepted_property_ids_scopes_refresh_to_imported_candidates() -> None:
    accepted = [
        SimpleNamespace(
            country="Georgia",
            city="Batumi",
            property_name="Batumi Arena",
            scene_type="stadium",
        )
    ]
    target_id = uuid4()
    packets = [
        SimpleNamespace(
            entity=SimpleNamespace(
                property_id=target_id,
                country="Georgia",
                city="Batumi",
                property_name="Batumi Arena",
                scene_type="stadium",
            )
        ),
        SimpleNamespace(
            entity=SimpleNamespace(
                property_id=uuid4(),
                country="Georgia",
                city="Tbilisi",
                property_name="Dinamo Arena",
                scene_type=SimpleNamespace(value="stadium"),
            )
        ),
    ]
    repository = SimpleNamespace(list_properties=lambda _filters: packets)

    assert agent_import._accepted_property_ids(repository, uuid4(), accepted) == [
        str(target_id)
    ]
