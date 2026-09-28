from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from isite2.db.models import NetworkPerformanceTileDB, PropertyDB
from isite2.growth.ookla import (
    OoklaTile,
    classify_performance,
    latlon_to_quadkey,
    match_property_to_tile,
    ookla_confidence,
    quadkeys_within_radius,
)
from isite2.growth.ookla_refresh import refresh_ookla_radius_tiles_from_arcgis_feature_layer
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


def test_property_matches_containing_z16_tile_first() -> None:
    latitude = -23.5505
    longitude = -46.6333
    quadkey = latlon_to_quadkey(latitude, longitude, zoom=16)
    tile = OoklaTile(
        quadkey=quadkey,
        latitude=latitude + 0.001,
        longitude=longitude + 0.001,
        avg_download_mbps=100,
        avg_upload_mbps=20,
        avg_latency_ms=18,
        avg_loaded_latency_down_ms=120,
        avg_loaded_latency_up_ms=140,
        tests=20,
        devices=8,
    )

    match = match_property_to_tile(latitude, longitude, [tile])

    assert match is not None
    assert match.match_method == "containing_tile"
    assert match.tile.quadkey == quadkey


def test_nearest_tile_beyond_one_kilometer_is_rejected() -> None:
    tile = OoklaTile(
        quadkey="1",
        latitude=-23.50,
        longitude=-46.60,
        avg_download_mbps=100,
        avg_upload_mbps=20,
        avg_latency_ms=18,
        avg_loaded_latency_down_ms=None,
        avg_loaded_latency_up_ms=None,
        tests=20,
        devices=8,
    )

    assert match_property_to_tile(-23.55, -46.63, [tile], max_distance_m=1_000) is None


def test_ookla_confidence_respects_sample_size_and_distance() -> None:
    assert ookla_confidence("containing_tile", 400, tests=10, devices=5) == "high"
    assert ookla_confidence("nearest_tile", 900, tests=3, devices=2) == "medium"
    assert ookla_confidence("nearest_tile", 900, tests=1, devices=1) == "low"


def test_quadkeys_within_radius_expands_beyond_nearest_neighbors() -> None:
    latitude = -23.5505
    longitude = -46.6333
    containing_quadkey = latlon_to_quadkey(latitude, longitude, zoom=16)

    quadkeys = quadkeys_within_radius(latitude, longitude, 5_000, zoom=16)

    assert containing_quadkey in quadkeys
    assert len(quadkeys) > 100
    assert len(quadkeys) == len(set(quadkeys))


def test_performance_class_uses_country_percentiles() -> None:
    assert (
        classify_performance(
            download_mbps=20,
            loaded_latency_ms=300,
            download_p20=30,
            download_p40=60,
            latency_p60=100,
            latency_p80=200,
        )
        == "poor"
    )
    assert (
        classify_performance(
            download_mbps=80,
            loaded_latency_ms=80,
            download_p20=30,
            download_p40=60,
            latency_p60=100,
            latency_p80=200,
        )
        == "good"
    )


def test_arcgis_feature_layer_radius_refresh_persists_mobile_tiles(
    tmp_path,
    monkeypatch,
) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'ookla.db'}",
        storage_mode="sqlite",
    )
    property_id = str(uuid4())
    now = datetime.now(UTC)
    with repository.session_factory.begin() as session:
        session.add(
            PropertyDB(
                id=property_id,
                property_identity_key="germany|transport_hub|hamburg hbf|hamburg",
                canonical_name="Hamburg Hbf",
                country="Germany",
                city="Hamburg",
                scene_type="transport_hub",
                scene_form="indoor",
                latitude=53.552,
                longitude=10.006,
                geocode_precision="station centroid",
                coordinate_status="Verified",
                created_at=now,
                updated_at=now,
            )
        )

    def fake_query(*_args, **_kwargs):
        return [
            {
                "properties": {
                    "QuadKey": latlon_to_quadkey(53.552, 10.006),
                    "CalYear": 2026,
                    "CalQuarter": 1,
                    "AvgDown": 182.734,
                    "AvgUp": 40.551,
                    "AvgLat": 22,
                    "Tests": 59,
                    "Devices": 50,
                }
            }
        ]

    monkeypatch.setattr("isite2.growth.ookla_refresh._query_arcgis_ookla_mobile_tiles", fake_query)

    summary = refresh_ookla_radius_tiles_from_arcgis_feature_layer(
        repository.engine,
        feature_layer_url="https://services.example/FeatureServer/1",
        country="Germany",
        radius_m=5_000,
        source_checksum="arcgis-item:test",
    )

    with repository.session_factory() as session:
        row = session.scalar(select(NetworkPerformanceTileDB))

    assert summary["mode"] == "ookla_arcgis_feature_layer_radius_refresh"
    assert summary["service_type"] == "mobile"
    assert summary["periods"] == ["2026 Q1"]
    assert summary["counts"]["tiles_inserted"] == 1
    assert row is not None
    assert row.country == "Germany"
    assert row.service_type == "mobile"
    assert row.period == "2026 Q1"
    assert row.avg_download_mbps == 182.734
    assert row.source_checksum == "arcgis-item:test"
