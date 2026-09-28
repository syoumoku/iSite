from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from isite2.db.models import (
    PropertyCityAssignmentDB,
    PropertyAliasDB,
    PropertyDB,
    PublicApiMapFeatureDB,
    PublicApiPropertyIndexDB,
    PublicApiPropertyPacketDB,
    PublicApiReviewQueueRowDB,
    ScanCandidateDB,
    ScanRunDB,
)
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.repositories.sqlalchemy import (
    SQLAlchemyScanRunRepository,
    _ensure_optional_pgvector_schema,
    create_postgis_first_repository,
)


def test_postgis_first_repository_can_fail_without_sqlite_fallback() -> None:
    with pytest.raises(SQLAlchemyError):
        create_postgis_first_repository(
            "postgresql+psycopg://isite:isite@127.0.0.1:1/isite2_missing",
            fallback_url=None,
        )


def test_optional_pgvector_schema_uses_savepoint_for_missing_extension() -> None:
    class FailingConnection:
        used_nested = False
        exited_nested = False

        def begin_nested(self):
            self.used_nested = True
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            self.exited_nested = True
            return False

        def exec_driver_sql(self, _sql: str) -> None:
            raise SQLAlchemyError("vector extension unavailable")

    connection = FailingConnection()

    _ensure_optional_pgvector_schema(connection)

    assert connection.used_nested is True
    assert connection.exited_nested is True


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


def test_repository_uses_canonical_city_id_for_upsert_and_city_summary(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'city-normalization.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")

    def registry(city: str, latitude: float, longitude: float) -> dict:
        return {
            "countries": {
                "Algeria": {
                    "bbox": {
                        "min_latitude": 18.0,
                        "max_latitude": 38.0,
                        "min_longitude": -9.0,
                        "max_longitude": 12.0,
                    },
                    "candidates": [
                        {
                            "property_name": "Example Business Tower",
                            "city": city,
                            "scene_type": "office_government",
                            "coordinate": {
                                "latitude": latitude,
                                "longitude": longitude,
                                "geocode_precision": "property",
                                "map_source": "official directory",
                                "coordinate_status": "Verified",
                            },
                            "evidence": [
                                {
                                    "field_group": "office_gfa",
                                    "indicator_name": "office_gfa",
                                    "field_value": "50,000 sqm office GFA",
                                    "source_name": "Official directory",
                                    "source_tier": "Tier 1",
                                    "source_url": "https://example.gov.dz/tower",
                                    "source_date": "2026-08-18",
                                    "evidence_type": "Direct",
                                }
                            ],
                        }
                    ]
                }
            }
        }

    scope = {
        "level": "country",
        "countries": ["Algeria"],
        "full_scan": True,
        "scene_types": ["office_government"],
        "output_formats": ["geojson"],
    }
    first = run_scan_pipeline(scope, repository, source_registry=registry("Hydra", 36.74, 3.03))
    second = run_scan_pipeline(
        scope,
        repository,
        source_registry=registry("Bab Ezzouar", 36.72, 3.18),
    )

    with repository.session_factory() as session:
        properties = session.query(PropertyDB).all()
        assignments = session.query(PropertyCityAssignmentDB).all()
    assert len(properties) == 1
    assert properties[0].city == "Algiers"
    assert properties[0].city_id == "DZ:algiers"
    assert len(assignments) == 1
    assert assignments[0].source_city == "Bab Ezzouar"
    assert assignments[0].locality == "Bab Ezzouar"
    assert first.packets[0].entity.property_id == second.packets[0].entity.property_id

    summaries = repository.list_city_summaries(
        {"country": "Algeria"},
        include_blocked_quality=True,
    )
    assert summaries[0]["city"] == "Algiers"
    assert summaries[0]["city_id"] == "DZ:algiers"
    assert summaries[0]["locality_count"] == 1


def test_repository_persists_aliases_and_searches_visible_properties(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    registry = _single_airport_registry("Houari Boumediene International Airport")
    registry["countries"]["Egypt"]["candidates"][0]["aliases"] = [
        "Aéroport d'Alger",
        "مطار الجزائر",
        "AÉROPORT D'ALGER",
    ]

    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=registry,
    )

    loaded = repository.get(result.scan_run.run_id)
    assert loaded is not None
    assert loaded.packets[0].entity.aliases == ["Aéroport d'Alger", "مطار الجزائر"]
    assert repository.search_properties("aeroport d alger", limit=20)[0][
        "property_name"
    ] == "Houari Boumediene International Airport"
    assert repository.search_properties("مطار الجزائر", limit=20)[0][
        "match_type"
    ] == "alias_exact"
    with repository.session_factory() as session:
        assert len(session.query(PropertyAliasDB).all()) == 2


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


def test_sqlalchemy_summaries_count_evidence_at_property_grain(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    registry = _many_airport_registry(2)
    for candidate in registry["countries"]["Egypt"]["candidates"]:
        candidate["evidence"][0]["source_url"] = "https://example.org/shared-directory"

    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=registry,
    )

    country_summary = repository.list_country_summaries()
    city_summary = repository.list_city_summaries({"country": "Egypt"})

    assert country_summary[0]["candidate_count"] == 2
    assert country_summary[0]["source_count"] == 2
    assert city_summary[0]["candidate_count"] == 2
    assert city_summary[0]["source_count"] == 2


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

    tied_candidate_time = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    with repository.session_factory() as session:
        for candidate in session.query(ScanCandidateDB).all():
            candidate.created_at = tied_candidate_time
        first_run = session.get(ScanRunDB, str(first.scan_run.run_id))
        second_run = session.get(ScanRunDB, str(second.scan_run.run_id))
        assert first_run is not None
        assert second_run is not None
        first_run.started_at = datetime(2026, 8, 18, 1, 0, tzinfo=UTC)
        second_run.started_at = datetime(2026, 8, 18, 2, 0, tzinfo=UTC)
        session.commit()

    latest_packets = repository.list_properties({"country": "Algeria"})
    first_run_packets = repository.list_properties({"scan_run_id": first.scan_run.run_id})
    second_run_packets = repository.list_properties({"scan_run_id": second.scan_run.run_id})

    assert len(latest_packets) == 4
    assert len(first_run_packets) == 4
    assert len(second_run_packets) == 4
    with repository.session_factory() as session:
        latest_rows = repository._latest_candidate_rows_per_property(session)
    assert {row.scan_run_id for row in latest_rows} == {str(second.scan_run.run_id)}


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


def test_sqlalchemy_repository_preserves_existing_hero_image_when_upsert_missing_image(
    tmp_path,
) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")

    first_registry = _single_airport_registry("Cairo International Airport")
    second_registry = _single_airport_registry("Cairo Intl. Airport Official Profile")
    second_registry["countries"]["Egypt"]["candidates"][0].pop("hero_image")
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
        property_row = session.query(PropertyDB).one()

    assert property_row.hero_image["url"] == "https://example.org/cairo-airport.jpg"


def test_sqlalchemy_fast_public_rows_do_not_query_per_property(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Egypt"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_many_airport_registry(28),
    )
    query_count = 0

    def count_query(*_args) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(repository.engine, "before_cursor_execute", count_query)
    try:
        map_rows = repository.list_map_property_rows({"country": "Egypt"})
        review_rows = repository.list_review_queue_rows({"country": "Egypt"}, status="open")
    finally:
        event.remove(repository.engine, "before_cursor_execute", count_query)

    assert len(map_rows) == 28
    assert isinstance(review_rows, list)
    assert query_count <= 20


def test_sqlalchemy_repository_reads_public_api_preaggregation(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    with repository.session_factory.begin() as session:
        session.add(
            PublicApiPropertyIndexDB(
                property_id="property-1",
                scan_run_id="run-1",
                property_name="Cairo International Airport",
                aliases=["مطار القاهرة الدولي"],
                search_text_normalized=(
                    "cairo international airport مطار القاهرة الدولي"
                ),
                country="Egypt",
                city="Cairo",
                scene_type="airport_terminal",
                longitude=31.4056,
                latitude=30.1219,
                google_maps_link="https://maps.example/cairo",
                geocode_precision="airport terminal centroid",
                map_source="test",
                coordinate_status="Verified",
                evidence_status="Direct Evidence",
                value_class="High Value",
                action_class="Prioritize",
                recommended_solution="DAS",
                annual_visits_est=2025,
                proxy_level="Direct",
                busy_hour_traffic_gb=123.0,
                indoor_system_presence="Unknown",
                indoor_rat="Unknown",
                candidate_quality_status="ready",
                main_table_ready=True,
                map_ready=True,
                export_ready=True,
                map_coordinate_ready=True,
                has_review_issue=True,
                review_count=1,
                source_count=2,
                source_urls=["https://source.example/a", "https://source.example/b"],
                main_metric_text="20 million passengers",
                visibility={"main_table_ready": True, "map_ready": True, "export_ready": True},
                quality_issues=[],
                sort_order=0,
            )
        )
        session.add(
            PublicApiPropertyPacketDB(
                id="zh:property-1",
                locale="zh",
                property_id="property-1",
                scan_run_id="run-1",
                country="Egypt",
                city="Cairo",
                scene_type="airport_terminal",
                evidence_status="Direct Evidence",
                value_class="High Value",
                action_class="Prioritize",
                recommended_solution="DAS",
                indoor_system_presence="Unknown",
                indoor_rat="Unknown",
                proxy_level="Direct",
                candidate_quality_status="ready",
                main_table_ready=True,
                map_ready=True,
                export_ready=True,
                has_review_issue=True,
                sort_order=0,
                packet_json={
                    "entity": {"property_id": "property-1", "property_name": "Cairo"},
                    "scene": {"annual_visits_est": 2025, "annual_visits_raw": 2025},
                    "demand": {
                        "daily_visits": 5.5,
                        "busy_hour_users": 1.0,
                        "busy_hour_traffic_gb": 123.0,
                    },
                    "localized": {"locale": "zh", "primary_metric": {"display_text": "2000万旅客"}},
                },
            )
        )
        session.add(
            PublicApiMapFeatureDB(
                id="zh:property-1",
                locale="zh",
                property_id="property-1",
                scan_run_id="run-1",
                country="Egypt",
                city="Cairo",
                scene_type="airport_terminal",
                evidence_status="Direct Evidence",
                value_class="High Value",
                action_class="Prioritize",
                recommended_solution="DAS",
                indoor_system_presence="Unknown",
                indoor_rat="Unknown",
                proxy_level="Direct",
                candidate_quality_status="ready",
                map_ready=True,
                map_coordinate_ready=True,
                has_review_issue=True,
                sort_order=0,
                feature_json={
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [31.4056, 30.1219]},
                    "properties": {
                        "property_id": "property-1",
                        "annual_visits_est": 2025,
                        "busy_hour_traffic_gb": 123.0,
                        "localized": {"locale": "zh"},
                    },
                },
            )
        )
        session.add(
            PublicApiReviewQueueRowDB(
                id="zh:review-1",
                review_id="review-1",
                locale="zh",
                property_id="property-1",
                scan_run_id="run-1",
                country="Egypt",
                city="Cairo",
                scene_type="airport_terminal",
                evidence_status="Direct Evidence",
                value_class="High Value",
                action_class="Prioritize",
                indoor_system_presence="Unknown",
                candidate_quality_status="ready",
                status="open",
                sort_order=0,
                row_json={
                    "property_id": "property-1",
                    "status": "open",
                    "localized": {"locale": "zh", "reason": "补充二源证据"},
                },
            )
        )

    page = repository.list_public_property_page(
        {"country": "Egypt"},
        locale="zh",
        include_blocked_quality=False,
        limit=1,
    )
    assert page is not None
    assert page["candidate_count"] == 1
    assert page["packets"][0]["localized"]["primary_metric"]["display_text"] == "2000万旅客"
    assert page["packets"][0]["scene"]["annual_visits_est"] is None
    assert page["packets"][0]["scene"]["annual_visits_raw"] is None
    assert page["packets"][0]["demand"]["busy_hour_traffic_gb"] is None
    detail = repository.get_public_property_packet("property-1", locale="zh")
    assert detail is not None
    assert detail["scene"]["annual_visits_est"] is None

    features = repository.list_public_map_features({"country": "Egypt"}, locale="zh")
    assert features is not None
    assert features[0]["properties"]["property_id"] == "property-1"
    assert features[0]["properties"]["annual_visits_est"] is None
    assert features[0]["properties"]["busy_hour_traffic_gb"] is None

    review_rows = repository.list_public_review_queue_rows(
        {"country": "Egypt"},
        locale="zh",
        status="open",
    )
    assert review_rows == [
        {
            "property_id": "property-1",
            "status": "open",
            "localized": {"locale": "zh", "reason": "补充二源证据"},
        }
    ]

    country_summary = repository.list_public_country_summaries(locale="zh")
    assert country_summary is not None
    assert country_summary[0]["candidate_count"] == 1
    assert country_summary[0]["source_count"] == 2
    assert country_summary[0]["localized"]["locale"] == "zh"

    city_summary = repository.list_public_city_summaries({"country": "Egypt"}, locale="zh")
    assert city_summary is not None
    assert city_summary[0]["city"] == "Cairo"
    assert city_summary[0]["map_point_count"] == 1
    assert repository.list_public_property_page(
        {"scan_run_id": "run-1"},
        locale="zh",
    ) is None
    search_results = repository.search_public_properties(
        "مطار القاهرة الدولي",
        limit=20,
    )
    assert search_results[0]["property_id"] == "property-1"
    assert search_results[0]["matched_name"] == "مطار القاهرة الدولي"
    assert search_results[0]["match_type"] == "alias_exact"


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


def _many_airport_registry(count: int) -> dict:
    registry = _single_airport_registry("Synthetic Airport 000")
    candidates = []
    for index in range(count):
        candidate = _single_airport_registry(
            f"Synthetic Airport {index:03d}"
        )["countries"]["Egypt"]["candidates"][0]
        candidate["coordinate"]["latitude"] = 30.0 + (index * 0.001)
        candidate["coordinate"]["longitude"] = 31.0 + (index * 0.001)
        candidate["evidence"][0]["source_url"] = (
            f"https://example.org/synthetic-airport-{index:03d}-traffic"
        )
        candidate["hero_image"]["url"] = (
            f"https://example.org/synthetic-airport-{index:03d}.jpg"
        )
        candidates.append(candidate)
    registry["countries"]["Egypt"]["candidates"] = candidates
    return registry
