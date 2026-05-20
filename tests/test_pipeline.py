from isite2.domain.enums import (
    BuildEvidenceStatus,
    IndoorRAT,
    IndoorSystemPresence,
    IndoorSystemType,
)
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.repositories.memory import InMemoryScanRunRepository
from isite2.rules.config_loader import scene_definitions
from isite2.rules.validation import is_concrete_review_action


def test_run_scan_pipeline_creates_full_scene_candidate_pool() -> None:
    repository = InMemoryScanRunRepository()
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Exampleland"],
            "full_scan": True,
            "output_formats": ["excel", "geojson"],
        },
        repository,
    )

    assert result.scan_run.status == "completed"
    assert result.scan_run.candidate_count == len(scene_definitions())
    assert len(result.packets) == len(scene_definitions())
    assert all(packet.candidate_quality_status == "blocked_quality" for packet in result.packets)
    assert all(packet.visibility.raw_pool for packet in result.packets)
    assert all(not packet.visibility.map_ready for packet in result.packets)
    assert repository.get(result.scan_run.run_id) == result


def test_pipeline_keeps_build_status_independent_and_reviewable() -> None:
    repository = InMemoryScanRunRepository()
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Exampleland"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["excel"],
        },
        repository,
    )
    packet = result.packets[0]

    assert packet.conclusion.evidence_status == "Supported"
    assert packet.build_status.indoor_system_presence == IndoorSystemPresence.NO_PUBLIC_EVIDENCE
    assert packet.build_status.indoor_system_type == IndoorSystemType.UNKNOWN
    assert packet.build_status.build_evidence_status == BuildEvidenceStatus.UNKNOWN
    assert packet.review_queue
    assert is_concrete_review_action(packet.review_queue[0].next_action)
    assert "不能由物业价值推断" in packet.review_queue[0].reason
    assert packet.candidate_quality_status == "blocked_quality"
    assert any(
        item.review_type == "candidate_quality" and item.severity == "critical"
        for item in packet.review_queue
    )


def test_pipeline_updates_build_status_only_from_direct_build_evidence() -> None:
    repository = InMemoryScanRunRepository()
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry={
            "version": "test",
            "countries": {
                "Egypt": {
                    "aliases": ["Egypt"],
                    "bbox": {
                        "min_latitude": 21.7,
                        "max_latitude": 31.8,
                        "min_longitude": 24.6,
                        "max_longitude": 36.9,
                    },
                    "candidates": [
                        {
                            "property_name": "Cairo Airport Build Evidence Sample",
                            "city": "Cairo",
                            "scene_type": "airport_terminal",
                            "annual_visits": 12_000_000,
                            "coordinate": {
                                "latitude": 30.1121,
                                "longitude": 31.4,
                                "geocode_precision": "airport centroid",
                                "map_source": "test",
                                "map_source_date": "2026-05-11",
                                "coordinate_status": "Verified",
                            },
                            "evidence": [
                                {
                                    "field_group": "annual_passenger_throughput",
                                    "indicator_name": "annual_passenger_throughput",
                                    "field_value": "12,000,000 passengers",
                                    "source_name": "Airport Authority",
                                    "source_tier": "Tier 1",
                                    "source_url": "https://airport.example.eg/traffic",
                                    "source_date": "2026",
                                    "evidence_type": "Direct",
                                },
                                {
                                    "field_group": "indoor_5g_upgrade",
                                    "indicator_name": "indoor_5g_upgrade",
                                    "field_value": "Operator announced 5G indoor coverage upgrade.",
                                    "source_name": "Mobile Operator",
                                    "source_tier": "Tier 1",
                                    "source_url": "https://operator.example.eg/indoor-5g",
                                    "source_date": "2026",
                                    "evidence_type": "Direct",
                                },
                            ],
                        }
                    ],
                }
            },
        },
    )

    build_status = result.packets[0].build_status

    assert build_status.indoor_system_presence == IndoorSystemPresence.CONFIRMED_PRESENT
    assert build_status.indoor_system_type == IndoorSystemType.UNKNOWN
    assert build_status.indoor_rat == IndoorRAT.FIVE_G
    assert build_status.build_evidence_status == BuildEvidenceStatus.SUPPORTED


def test_algeria_scan_uses_country_specific_opportunity_fixtures() -> None:
    repository = InMemoryScanRunRepository()
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria"],
            "full_scan": True,
            "output_formats": ["excel", "geojson"],
        },
        repository,
    )

    names = {packet.entity.property_name for packet in result.packets}

    assert result.scan_run.candidate_count == 4
    assert "Houari Boumediene International Airport" in names
    assert "Stade Nelson Mandela" in names
    assert all(packet.entity.country == "Algeria" for packet in result.packets)
    assert all(packet.candidate_quality_status == "ready" for packet in result.packets)
    assert all(packet.visibility.map_ready for packet in result.packets)
    assert all(2.5 <= packet.entity.longitude <= 3.4 for packet in result.packets)
    assert all(36.5 <= packet.entity.latitude <= 36.9 for packet in result.packets)
    assert all(packet.evidence[0].source_url for packet in result.packets)


def test_algeria_egypt_scan_uses_source_registry_and_multiple_evidence_sources() -> None:
    repository = InMemoryScanRunRepository()
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria", "Egypt"],
            "full_scan": True,
            "output_formats": ["excel", "geojson"],
        },
        repository,
    )

    countries = {packet.entity.country for packet in result.packets}
    source_counts = [len({str(e.source_url) for e in packet.evidence}) for packet in result.packets]

    assert result.scan_run.candidate_count == 8
    assert countries == {"Algeria", "Egypt"}
    assert all(packet.entity.coordinate_status == "Verified" for packet in result.packets)
    assert all(packet.entity.hero_image is not None for packet in result.packets)
    assert all(
        str(packet.entity.hero_image.url).startswith("https://")
        for packet in result.packets
    )
    assert all(count >= 2 for count in source_counts)


def test_in_memory_repository_defaults_to_latest_packet_per_property() -> None:
    repository = InMemoryScanRunRepository()
    first = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria"],
            "full_scan": True,
            "output_formats": ["geojson"],
        },
        repository,
    )
    second = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria"],
            "full_scan": True,
            "output_formats": ["geojson"],
        },
        repository,
    )

    latest_packets = repository.list_properties({"country": "Algeria"})
    first_run_packets = repository.list_properties({"scan_run_id": first.scan_run.run_id})
    second_run_packets = repository.list_properties({"scan_run_id": second.scan_run.run_id})

    assert len(latest_packets) == 4
    assert len(first_run_packets) == 4
    assert len(second_run_packets) == 4
