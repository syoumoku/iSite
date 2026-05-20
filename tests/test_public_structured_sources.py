from __future__ import annotations

import json

from isite2.growth.public_structured_sources import (
    DbpediaAdapter,
    MediaWikiAdapter,
    OurAirportsAdapter,
    PublicStructuredTarget,
    _infobox_metrics,
)


class FakeHttpClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url: str, *, timeout: int = 30) -> dict:
        self.urls.append(url)
        if "list=search" in url:
            return {"query": {"search": [{"title": "Maracana Stadium"}]}}
        if "prop=revisions" in url:
            return {
                "query": {
                    "pages": [
                        {
                            "title": "Maracana Stadium",
                            "fullurl": "https://en.wikipedia.org/wiki/Maracana_Stadium",
                            "thumbnail": {"source": "https://upload.wikimedia.org/maracana.jpg"},
                            "revisions": [
                                {
                                    "slots": {
                                        "main": {
                                            "content": "{{Infobox venue\n| name = Maracana Stadium\n| capacity = 78,838\n}}\nThe stadium has 78,838 seats."
                                        }
                                    }
                                }
                            ],
                        }
                    ]
                }
            }
        if "dbpedia.org" in url:
            return {
                "results": {
                    "bindings": [
                        {
                            "resource": {
                                "value": "http://dbpedia.org/resource/Maracan%C3%A3_Stadium"
                            },
                            "label": {"value": "Maracana Stadium"},
                            "seatingCapacity": {"value": "78838"},
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected json url: {url}")

    def get_text(self, url: str, *, timeout: int = 30) -> str:
        self.urls.append(url)
        return (
            "id,ident,type,name,latitude_deg,longitude_deg,iata_code,gps_code,home_link,wikipedia_link\n"
            "1,SBGL,large_airport,Rio de Janeiro/Galeao International Airport,"
            "-22.809999,-43.250557,GIG,SBGL,https://www.riogaleao.com/,"
            "https://en.wikipedia.org/wiki/Rio_de_Janeiro/Gale%C3%A3o_International_Airport\n"
        )


def test_mediawiki_adapter_extracts_primary_metric_and_hero() -> None:
    target = _target(property_name="Maracana Stadium", scene_type="stadium")
    evidence = MediaWikiAdapter(FakeHttpClient()).collect(target)

    assert evidence
    assert any(item.field_group == "seat_count" for item in evidence)
    assert any("78,838" in item.field_value for item in evidence)
    assert evidence[0].hero_image["url"].startswith("https://upload.wikimedia.org/")


def test_dbpedia_adapter_extracts_structured_capacity() -> None:
    target = _target(property_name="Maracana Stadium", scene_type="stadium")
    evidence = DbpediaAdapter(FakeHttpClient()).collect(target)

    assert len(evidence) == 1
    assert evidence[0].field_group == "seat_count"
    assert evidence[0].field_value == "DBpedia seatingCapacity: 78838"


def test_ourairports_adapter_matches_by_coordinate_and_name() -> None:
    target = _target(
        property_name="Rio de Janeiro Galeao International Airport",
        scene_type="airport_terminal",
        latitude=-22.81,
        longitude=-43.25,
    )
    evidence = OurAirportsAdapter(FakeHttpClient()).collect(target)

    assert len(evidence) == 1
    assert evidence[0].field_group == "airport_identity"
    assert "IATA GIG" in evidence[0].field_value


def test_ourairports_adapter_rejects_nearby_name_mismatch() -> None:
    target = _target(
        property_name="Brasilia International Airport",
        scene_type="airport_terminal",
        latitude=-22.81,
        longitude=-43.25,
    )

    assert OurAirportsAdapter(FakeHttpClient()).collect(target) == []


def test_public_structured_evidence_to_candidate_draft_marks_known_property() -> None:
    target = _target(property_name="Maracana Stadium", scene_type="stadium")
    evidence = MediaWikiAdapter(FakeHttpClient()).collect(target)[0]
    draft = evidence.to_candidate_draft(
        registry={
            "countries": {
                "Brazil": {
                    "bbox": {
                        "min_latitude": -35,
                        "max_latitude": 6,
                        "min_longitude": -75,
                        "max_longitude": -30,
                    }
                }
            }
        }
    )

    assert draft.identity_match_status == "known_property"
    assert draft.matched_property_id == target.property_id
    assert json.loads(json.dumps(draft.registry_candidate(), ensure_ascii=False))


def test_airport_infobox_uses_stat_header_and_rejects_year_as_metric() -> None:
    content = """
{{Infobox airport
| name = Afonso Pena International Airport
| stat-year = 2025
| stat1-header = Passengers
| stat1-data = 6,082,222 {{increase}} 17%
| stat2-header = Aircraft operations
| stat2-data = 27
}}
"""

    metrics = _infobox_metrics("airport_terminal", content)

    assert metrics == [
        (
            "annual_passenger_throughput",
            "MediaWiki infobox Passengers (2025): 6,082,222 17%",
        )
    ]


def test_stadium_infobox_capacity_aliases_become_seat_count() -> None:
    content = """
{{Infobox venue
| name = Example National Stadium
| seating capacity = 45,000 seats
| capacity = 45,000
| opened = 2012
}}
"""

    metrics = _infobox_metrics("stadium", content)

    assert ("seat_count", "MediaWiki infobox seating capacity: 45,000 seats") in metrics
    assert ("seat_count", "MediaWiki infobox capacity: 45,000") in metrics
    assert all(field_group == "seat_count" for field_group, _ in metrics)


def _target(
    *,
    property_name: str = "Maracana Stadium",
    scene_type: str = "stadium",
    latitude: float = -22.9122,
    longitude: float = -43.2302,
) -> PublicStructuredTarget:
    return PublicStructuredTarget(
        property_id="prop-1",
        country="Brazil",
        city="Rio de Janeiro",
        property_name=property_name,
        scene_type=scene_type,
        latitude=latitude,
        longitude=longitude,
        geocode_precision="venue centroid",
        map_source="test",
        map_source_date="2026-05-13",
        annual_visits_est=1_000_000,
    )
