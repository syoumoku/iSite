import pytest
from sqlalchemy.exc import SQLAlchemyError

from isite2.db.models import PropertyDB
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.repositories.sqlalchemy import (
    SQLAlchemyScanRunRepository,
    create_postgis_first_repository,
)


def test_postgis_first_repository_can_fail_without_sqlite_fallback() -> None:
    with pytest.raises(SQLAlchemyError):
        create_postgis_first_repository(
            "postgresql+psycopg://isite:isite@127.0.0.1:1/isite2_missing",
            fallback_url=None,
        )


def test_sqlalchemy_repository_persists_scan_packets_across_instances(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria", "Egypt"],
            "full_scan": True,
            "output_formats": ["geojson"],
        },
        repository,
    )

    reopened = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    loaded = reopened.get(result.scan_run.run_id)

    assert loaded is not None
    assert loaded.scan_run.candidate_count == 8
    assert len(loaded.packets) == 8
    assert loaded.persisted_counts["scan_candidates"] == 8
    assert loaded.persisted_counts["evidence_items"] >= 16
    assert loaded.persisted_counts["source_cache"] >= 16
    assert all(packet.evidence for packet in loaded.packets)
    assert all(packet.entity.hero_image is not None for packet in loaded.packets)
    assert all(
        str(packet.entity.hero_image.source_url).startswith("https://")
        for packet in loaded.packets
    )


def test_sqlalchemy_repository_scan_run_filter_limits_map_candidate_pool(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    algeria = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria"],
            "full_scan": True,
            "output_formats": ["geojson"],
        },
        repository,
    )
    egypt = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "output_formats": ["geojson"],
        },
        repository,
    )

    algeria_packets = repository.list_properties({"scan_run_id": algeria.scan_run.run_id})
    egypt_packets = repository.list_properties({"scan_run_id": egypt.scan_run.run_id})

    assert len(algeria_packets) == 4
    assert len(egypt_packets) == 4
    assert {packet.entity.country for packet in algeria_packets} == {"Algeria"}
    assert {packet.entity.country for packet in egypt_packets} == {"Egypt"}


def test_sqlalchemy_repository_defaults_to_latest_packet_per_property(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
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


def test_sqlalchemy_repository_upserts_properties_by_identity_key(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")

    first_registry = _single_airport_registry("Cairo International Airport")
    second_registry = _single_airport_registry("Cairo Intl. Airport Official Profile")
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=first_registry,
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=second_registry,
    )

    with repository.session_factory() as session:
        property_rows = session.query(PropertyDB).all()

    assert len(property_rows) == 1
    assert len(repository.list_properties({"country": "Egypt"})) == 1


def _single_airport_registry(property_name: str) -> dict:
    return {
        "countries": {
            "Egypt": {
                "aliases": ["Egypt"],
                "bbox": {
                    "min_latitude": 22.0,
                    "max_latitude": 32.0,
                    "min_longitude": 24.0,
                    "max_longitude": 37.0,
                },
                "candidates": [
                    {
                        "property_name": property_name,
                        "city": "Cairo",
                        "scene_type": "airport_terminal",
                        "annual_visits": 10_000_000,
                        "coordinate": {
                            "latitude": 30.1219,
                            "longitude": 31.4056,
                            "geocode_precision": "airport terminal centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-12",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/cairo-airport.jpg",
                            "alt_text": "Cairo International Airport terminal",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/cairo-airport-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "annual_passenger_throughput",
                                "indicator_name": "annual_passenger_throughput",
                                "field_value": "10,000,000 passengers",
                                "source_name": "Example Airport Authority",
                                "source_tier": "Tier 1",
                                "source_url": "https://example.org/cairo-airport-traffic",
                                "source_date": "2026",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }
