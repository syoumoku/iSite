import os
import re
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

TEST_DB_PATH = Path("outputs") / "test_api_dev.db"
os.environ["DATABASE_URL"] = "postgresql+psycopg://isite:isite@127.0.0.1:1/isite2_test"
os.environ["ISITE2_SQLITE_FALLBACK_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH}"

import isite2.api.main as api_main  # noqa: E402
from isite2.api.main import app, curation_store, repository  # noqa: E402
from isite2.db.models import (  # noqa: E402
    BuildStatusDB,
    ConclusionDB,
    NetworkPerformanceObservationDB,
    NetworkPerformanceTileDB,
    PropertyDB,
    PropertyNetworkPerformanceRollupDB,
    PublicApiMapFeatureDB,
    PublicApiPropertyIndexDB,
    PublicApiPropertyPacketDB,
    PublicApiReviewQueueRowDB,
    SceneModelResultDB,
)
from isite2.growth.footfall import parse_placer_footfall_cells  # noqa: E402
from isite2.growth.ookla import latlon_to_quadkey, quadkey_centroid  # noqa: E402
from isite2.growth.evidence_curation import run_pending_evidence_curation  # noqa: E402
from isite2.growth.evidence_intake import CandidateDraft  # noqa: E402
from isite2.rules.config_loader import load_source_registry, scene_definitions  # noqa: E402
from isite2.output.excel import write_excel_skeleton  # noqa: E402
from isite2.public_reports import file_sha256  # noqa: E402


def setup_function() -> None:
    api_main.app.state.overlay_sync_enabled = False
    api_main._reset_satellite_tile_breaker()
    api_main._COUNTRY_SUMMARY_CACHE.clear()
    repository.clear()


def test_health_and_rules_endpoints() -> None:
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert "airport_terminal" in client.get("/rules/scenes").json()["scenes"]
    output_template = client.get("/rules/output-template").json()
    assert "Google地图链接" in output_template["excel"]["main_columns"]
    main_columns = output_template["excel"]["main_columns"]
    assert main_columns.index("主指标量化值") == main_columns.index("物业点重要证据") + 1
    localization = client.get("/rules/localization", params={"locale": "zh"}).json()
    assert localization["locale"] == "zh"
    assert localization["labels"]["scenes"]["airport_terminal"] == "机场"


def test_runtime_config_feature_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_FEATURE_EXPORTS_ENABLED", "0")
    monkeypatch.setenv("ISITE2_FEATURE_RAG_ENABLED", "0")
    monkeypatch.setenv("ISITE2_FEATURE_CONNECTORS_ENABLED", "0")
    monkeypatch.setenv("ISITE2_FEATURE_GEOCODE_ENABLED", "0")
    client = TestClient(app)

    config = client.get("/runtime-config").json()
    assert config["mode"] == "local"
    assert config["features"] == {
        "exports": False,
        "rag": False,
        "connectors": False,
        "geocode": False,
        "trafficV2": True,
        "complaintAggregation": True,
        "ooklaPublic": False,
        "ooklaPublicCountries": [],
    }


def test_local_public_ui_surface_matches_public_chrome_without_route_gate(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_UI_SURFACE_MODE", "public_view")
    monkeypatch.setenv("ISITE2_APP_AUTH_ENABLED", "1")
    monkeypatch.setenv("ISITE2_APP_AUTH_USER", "visitor")
    monkeypatch.setenv("ISITE2_APP_AUTH_PASSWORD", "visitor123456")
    client = TestClient(app)

    config = client.get("/runtime-config").json()
    assert config["mode"] == "public_view"
    assert config["features"] == {
        "exports": False,
        "rag": False,
        "connectors": False,
        "geocode": False,
        "trafficV2": True,
        "complaintAggregation": True,
        "ooklaPublic": False,
        "ooklaPublicCountries": [],
    }
    assert config["auth"] == {
        "enabled": True,
        "guestClickLimit": 10,
        "usernameHint": "visitor",
    }
    assert client.get("/docs").status_code == 200


def test_public_view_runtime_config_and_route_gate(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_APP_MODE", "public_view")
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TEMPLATE", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TOKEN", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_ATTRIBUTION", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_SIZE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ISITE2_APP_AUTH_ENABLED", "1")
    monkeypatch.setenv("ISITE2_APP_AUTH_USER", "visitor")
    monkeypatch.setenv("ISITE2_APP_AUTH_PASSWORD", "visitor123456")
    monkeypatch.setenv("ISITE2_APP_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ISITE2_GUEST_CLICK_LIMIT", "10")
    client = TestClient(app)

    config = client.get("/runtime-config").json()
    footfall_provider = config["map"].pop("footfallProvider")
    assert "T" in footfall_provider.pop("checkedAt")
    assert footfall_provider == {
        "provider": "public_open_data",
        "configured": True,
        "requiresApiKey": False,
        "endpointConfigured": False,
        "placerConfigured": False,
        "publicCountries": ["Germany"],
    }
    assert config["map"].pop("propertyOverlayTemplate") == (
        "/map/property-overlays/{property_id}?layer={layer}&radius_m={radius_m}"
    )
    assert "PLACER_API_KEY" not in json.dumps(config)
    assert config == {
        "mode": "public_view",
        "features": {
            "exports": False,
            "rag": False,
            "connectors": False,
            "geocode": False,
            "trafficV2": True,
            "complaintAggregation": True,
            "ooklaPublic": False,
            "ooklaPublicCountries": [],
        },
        "localization": {
            "defaultLocale": "en",
            "supportedLocales": ["en", "zh"],
        },
        "map": {
            "satelliteTileTemplate": "/map/satellite-tiles/{z}/{y}/{x}",
            "satelliteTileSize": 512,
            "satelliteAttribution": "Source: MapTiler Satellite",
        },
        "auth": {
            "enabled": True,
            "guestClickLimit": 10,
            "usernameHint": "visitor",
        },
        "serviceRequests": {
            "enabled": True,
            "dailyLimit": 10,
            "types": ["scan_enhancement", "feature_request", "ppt_report"],
            "updatesLimit": 30,
        },
    }
    session = client.get("/auth/session").json()
    assert session["authenticated"] is False
    assert session["guestClickLimit"] == 10
    assert client.post(
        "/auth/login",
        json={"username": "visitor", "password": "wrong"},
    ).status_code == 401
    login_response = client.post(
        "/auth/login",
        json={"username": "visitor", "password": "visitor123456"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["authenticated"] is True
    assert "isite2_session=" in login_response.headers["set-cookie"]
    assert client.get("/auth/session").json() == {
        "enabled": True,
        "authenticated": True,
        "username": "visitor",
        "guestClickLimit": 10,
        "usernameHint": "visitor",
    }
    assert client.post("/auth/logout").json()["authenticated"] is False
    root_response = client.get("/", follow_redirects=False)
    assert root_response.status_code == 308
    assert root_response.headers["location"] == "/ui/"
    assert client.get("/health").status_code == 200
    assert client.get("/rules/scenes").status_code == 200
    assert client.get("/rules/localization").status_code == 200
    assert client.get("/scan-runs").status_code == 200
    assert client.get("/map/country-summary").status_code == 200
    assert client.get(f"/map/property-overlays/{uuid4()}", params={"layer": "footfall"}).status_code in {404}
    assert client.get("/properties").status_code == 200
    assert client.get("/review-queue").status_code == 200
    assert client.get("/discovery/status").status_code == 200

    blocked_requests = [
        client.post("/scan-runs", json={"scope": {"countries": ["Algeria"]}}),
        client.post("/outputs/excel", json={}),
        client.post("/rag/query", json={"question": "x"}),
        client.get("/connectors/geocode", params={"q": "Algiers"}),
        client.get("/raw-evidence"),
        client.get("/candidate-drafts"),
        client.get("/docs"),
    ]
    assert {response.status_code for response in blocked_requests} == {403}


def test_property_overlay_mobile_network_uses_only_mobile_ookla(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_PUBLIC_OOKLA_ENABLED", "1")
    property_id = _insert_property_with_mobile_ookla()
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "mobile_network"},
    ).json()

    assert payload["status"] == "ready"
    assert payload["provider"] == "ookla_open_data"
    assert "not indoor DAS/build evidence" in payload["proxy_note"]
    assert "5 km" in payload["message"]
    assert len(payload["features"]) == 2
    tile_features = [
        feature for feature in payload["features"]
        if feature["properties"]["feature_kind"] == "tile"
    ]
    center_features = [
        feature for feature in payload["features"]
        if feature["properties"]["feature_kind"] == "tile_center"
    ]
    assert len(tile_features) == 1
    assert len(center_features) == 1
    assert center_features[0]["geometry"]["type"] == "Point"
    properties = tile_features[0]["properties"]
    assert properties["metric_label"] == "Mobile download Mbps"
    assert properties["download_mbps"] == 72.0
    assert properties["source_name"] == "Ookla Open Data"
    assert properties["distance_to_property_m"] <= 5000


def test_property_overlay_mobile_network_returns_5km_nearby_tiles(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_PUBLIC_OOKLA_ENABLED", "1")
    property_id = _insert_property_with_mobile_ookla(canonical_name="Target Brazil Property")
    _insert_property_with_mobile_ookla(
        latitude=-23.57,
        longitude=-46.65,
        canonical_name="Nearby Brazil Property",
        download_mbps=38.0,
        period="2026 Q2",
    )
    _insert_property_with_mobile_ookla(
        latitude=-23.75,
        longitude=-46.85,
        canonical_name="Far Brazil Property",
        download_mbps=110.0,
        period="2026 Q3",
    )
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "mobile_network", "radius_m": 5000},
    ).json()

    tile_features = [
        feature for feature in payload["features"]
        if feature["properties"]["feature_kind"] == "tile"
    ]
    center_features = [
        feature for feature in payload["features"]
        if feature["properties"]["feature_kind"] == "tile_center"
    ]
    cell_ids = {feature["properties"]["cell_id"] for feature in tile_features}
    assert payload["status"] == "ready"
    assert len(tile_features) == 2
    assert len(center_features) == 2
    assert len(cell_ids) == 2
    assert all(feature["properties"]["distance_to_property_m"] <= 5000 for feature in tile_features)
    assert {feature["properties"]["download_mbps"] for feature in tile_features} == {72.0, 38.0}


def test_property_overlay_mobile_network_prefers_radius_tile_table(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_PUBLIC_OOKLA_ENABLED", "1")
    property_id = _insert_property_with_mobile_ookla(
        canonical_name="Radius Tile Target",
        download_mbps=72.0,
    )
    nearby_quadkey = latlon_to_quadkey(-23.552, -46.635)
    tile_lat, tile_lng = quadkey_centroid(nearby_quadkey)
    now = datetime.now(UTC)
    with repository.session_factory.begin() as session:
        session.add(
            NetworkPerformanceTileDB(
                id=str(uuid4()),
                country="Brazil",
                service_type="mobile",
                period="2026 Q1",
                quadkey=nearby_quadkey,
                tile_latitude=tile_lat,
                tile_longitude=tile_lng,
                avg_download_mbps=144.0,
                avg_upload_mbps=31.0,
                avg_latency_ms=21.0,
                avg_loaded_latency_down_ms=88.0,
                avg_loaded_latency_up_ms=110.0,
                tests=42,
                devices=18,
                confidence="high",
                performance_class="good",
                download_percentile=0.9,
                loaded_latency_percentile=0.25,
                source_url="https://ookla.example/mobile.parquet",
                source_checksum="tile-fixture",
                source_accessed_at=now,
                license="Ookla Open Data",
            )
        )
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "mobile_network", "radius_m": 5000},
    ).json()

    tile_features = [
        feature for feature in payload["features"]
        if feature["properties"]["feature_kind"] == "tile"
    ]
    assert payload["status"] == "ready"
    assert len(tile_features) == 1
    assert tile_features[0]["properties"]["download_mbps"] == 144.0
    assert tile_features[0]["properties"]["tile_scope"] == "radius_tile"


def test_property_overlay_mobile_network_public_country_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_UI_SURFACE_MODE", "public_view")
    monkeypatch.delenv("ISITE2_PUBLIC_OOKLA_ENABLED", raising=False)
    monkeypatch.setenv("ISITE2_PUBLIC_OOKLA_COUNTRY_ALLOWLIST", "Brazil")
    property_id = _insert_property_with_mobile_ookla()
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "mobile_network"},
    ).json()

    assert payload["status"] == "ready"
    assert payload["provider"] == "ookla_open_data"


def test_property_overlay_mobile_network_public_allowlist_blocks_other_countries(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_UI_SURFACE_MODE", "public_view")
    monkeypatch.delenv("ISITE2_PUBLIC_OOKLA_ENABLED", raising=False)
    monkeypatch.setenv("ISITE2_PUBLIC_OOKLA_COUNTRY_ALLOWLIST", "Brazil")
    property_id = _insert_property_with_mobile_ookla(country="Chile")
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "mobile_network"},
    ).json()

    assert payload["status"] == "disabled"
    assert payload["features"] == []
    assert "country" in payload["message"]


def test_property_overlay_footfall_returns_placer_cells(monkeypatch) -> None:
    property_id = _insert_property_only()
    sample = {
        "cells": [
            {
                "lat": -23.55,
                "lng": -46.63,
                "visits": 12500,
                "radius_m": 120,
                "source_url": "https://placer.ai",
                "period": "2026-07",
            }
        ]
    }

    class FakeResult:
        status = "ready"
        provider = "placer_ai"
        message = "Placer.ai footfall layer ready."
        cells = parse_placer_footfall_cells(
            sample,
            property_id=str(property_id),
            fallback_latitude=-23.55,
            fallback_longitude=-46.63,
        )

    monkeypatch.setattr(api_main, "cached_footfall_overlay", lambda **_: FakeResult())
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "footfall"},
    ).json()

    assert payload["provider"] == "placer_ai"
    assert payload["status"] == "ready"
    assert payload["features"][0]["properties"]["source_name"] == "Placer.ai"
    assert payload["features"][0]["properties"]["metric_value"] == 12500.0


def test_property_overlay_footfall_uses_germany_public_observations(monkeypatch) -> None:
    monkeypatch.delenv("PLACER_API_KEY", raising=False)
    monkeypatch.delenv("PLACER_FOOTFALL_ENDPOINT_TEMPLATE", raising=False)
    property_id = _insert_property_only(
        country="Germany",
        latitude=53.55278,
        longitude=10.00639,
        canonical_name="Hamburg Hbf",
    )
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "footfall"},
    ).json()

    assert payload["provider"] == "public_open_data"
    assert payload["status"] == "ready"
    assert payload["features"]
    feature_kinds = {feature["properties"]["feature_kind"] for feature in payload["features"]}
    assert {"footfall_cell", "footfall_center"}.issubset(feature_kinds)
    properties = payload["features"][0]["properties"]
    assert properties["source_name"] == "Deutsche Bahn station public profile"
    assert properties["metric_value"] == 550000.0
    assert properties["distance_to_property_m"] <= 1


def test_property_overlay_footfall_without_public_or_placer_data_is_empty(monkeypatch) -> None:
    monkeypatch.delenv("PLACER_API_KEY", raising=False)
    monkeypatch.delenv("PLACER_FOOTFALL_ENDPOINT_TEMPLATE", raising=False)
    property_id = _insert_property_only()
    client = TestClient(app)

    payload = client.get(
        f"/map/property-overlays/{property_id}",
        params={"layer": "footfall"},
    ).json()

    assert payload["status"] == "no_data"
    assert payload["features"] == []
    assert "public footfall" in payload["message"]


def test_public_view_endpoints_use_preaggregated_api_tables(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_APP_MODE", "public_view")
    property_id = "11111111-1111-1111-1111-111111111111"
    with repository.session_factory.begin() as session:
        session.add(
            PublicApiPropertyIndexDB(
                property_id=property_id,
                scan_run_id="run-1",
                property_name="Cairo International Airport",
                country="Egypt",
                city="Cairo",
                city_id="EG:cairo",
                city_assignment={
                    "city_id": "EG:cairo",
                    "canonical_city": "Cairo",
                    "source_city": "Heliopolis",
                    "locality": "Heliopolis",
                    "mapping_status": "verified",
                    "mapping_method": "official_crosswalk",
                },
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
                id=f"zh:{property_id}",
                locale="zh",
                property_id=property_id,
                scan_run_id="run-1",
                country="Egypt",
                city="Cairo",
                city_id="EG:cairo",
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
                    "entity": {
                        "property_id": property_id,
                        "property_name": "Cairo",
                        "city": "Cairo",
                        "city_assignment": {
                            "city_id": "EG:cairo",
                            "source_city": "Heliopolis",
                            "locality": "Heliopolis",
                            "mapping_status": "verified",
                            "mapping_method": "official_crosswalk",
                        },
                    },
                    "scene": {"annual_visits_est": 2025, "annual_visits_raw": 2025},
                    "demand": {
                        "daily_visits": 5.5,
                        "busy_hour_users": 1.0,
                        "busy_hour_traffic_gb": 123.0,
                    },
                    "localized": {"locale": "zh", "primary_metric": {"display_text": "预聚合指标"}},
                },
            )
        )
        session.add(
            PublicApiMapFeatureDB(
                id=f"zh:{property_id}",
                locale="zh",
                property_id=property_id,
                scan_run_id="run-1",
                country="Egypt",
                city="Cairo",
                city_id="EG:cairo",
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
                        "property_id": property_id,
                        "city_id": "EG:cairo",
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
                property_id=property_id,
                scan_run_id="run-1",
                country="Egypt",
                city="Cairo",
                city_id="EG:cairo",
                scene_type="airport_terminal",
                evidence_status="Direct Evidence",
                value_class="High Value",
                action_class="Prioritize",
                indoor_system_presence="Unknown",
                candidate_quality_status="ready",
                status="open",
                sort_order=0,
                row_json={
                    "property_id": property_id,
                    "status": "open",
                    "localized": {"locale": "zh", "reason": "预聚合复核"},
                },
            )
        )

    client = TestClient(app)
    properties = client.get(
        "/properties",
        params={"country": "Egypt", "city_id": "EG:cairo", "locale": "zh"},
    ).json()
    assert properties["candidate_count"] == 1
    assert properties["packets"][0]["localized"]["primary_metric"]["display_text"] == "预聚合指标"
    assert properties["packets"][0]["scene"]["annual_visits_est"] is None
    assert properties["packets"][0]["demand"]["busy_hour_traffic_gb"] is None

    detail = client.get(f"/properties/{property_id}", params={"locale": "zh"}).json()
    assert detail["localized"]["primary_metric"]["display_text"] == "预聚合指标"
    assert detail["scene"]["annual_visits_est"] is None

    features = client.get(
        "/map/properties",
        params={"country": "Egypt", "city_id": "EG:cairo", "locale": "zh"},
    ).json()
    assert features["features"][0]["properties"]["property_id"] == property_id
    assert features["features"][0]["properties"]["annual_visits_est"] is None
    assert features["features"][0]["properties"]["busy_hour_traffic_gb"] is None

    review_rows = client.get(
        "/review-queue",
        params={"country": "Egypt", "status": "open", "locale": "zh"},
    ).json()
    assert review_rows[0]["localized"]["reason"] == "预聚合复核"

    country_summary = client.get("/map/country-summary", params={"locale": "zh"}).json()
    assert country_summary[0]["candidate_count"] == 1
    city_summary = client.get(
        "/map/city-summary",
        params={"country": "Egypt", "city_id": "EG:cairo", "locale": "zh"},
    ).json()
    assert city_summary["cities"][0]["city"] == "Cairo"
    assert city_summary["cities"][0]["city_id"] == "EG:cairo"
    assert city_summary["cities"][0]["locality_count"] == 1


def test_satellite_tile_proxy_caches_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_TILE_CACHE_DIR", str(tmp_path / "tiles"))
    monkeypatch.setenv("ISITE2_SATELLITE_TILE_TEMPLATE", "https://tiles.example/{z}/{y}/{x}.jpg")
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TOKEN", raising=False)
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return b"tile-bytes", "image/png"

    monkeypatch.setattr(api_main, "_fetch_satellite_tile_from_upstream", fake_fetch)
    client = TestClient(app)

    first = client.get("/map/satellite-tiles/1/0/1")
    second = client.get("/map/satellite-tiles/1/0/1")

    assert first.status_code == 200
    assert first.content == b"tile-bytes"
    assert first.headers["content-type"].startswith("image/png")
    assert second.status_code == 200
    assert calls == ["https://tiles.example/1/0/1.jpg"]


def test_hero_image_proxy_caches_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_HERO_IMAGE_CACHE_DIR", str(tmp_path / "hero-images"))
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str, str]:
        calls.append(url)
        return b"image-bytes", "image/webp", "https://cdn.example/final.webp"

    monkeypatch.setattr(api_main, "_fetch_hero_image_from_upstream", fake_fetch)
    client = TestClient(app)

    first = client.get("/map/hero-image", params={"url": "https://images.example/property.jpg"})
    second = client.get("/map/hero-image", params={"url": "https://images.example/property.jpg"})
    head = client.head("/map/hero-image", params={"url": "https://images.example/property.jpg"})

    assert first.status_code == 200
    assert first.content == b"image-bytes"
    assert first.headers["content-type"].startswith("image/webp")
    assert second.status_code == 200
    assert head.status_code == 200
    assert head.content == b""
    assert calls == ["https://images.example/property.jpg"]


def test_hero_image_proxy_stabilizes_wikimedia_file_urls(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_HERO_IMAGE_CACHE_DIR", str(tmp_path / "hero-images"))
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str, str]:
        calls.append(url)
        return b"image-bytes", "image/jpeg", url

    monkeypatch.setattr(api_main, "_fetch_hero_image_from_upstream", fake_fetch)
    client = TestClient(app)

    response = client.get(
        "/map/hero-image",
        params={
            "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Aeroport%20Tamanrasset.jpg"
        },
    )

    assert response.status_code == 200
    assert calls == [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7b/"
        "Aeroport_Tamanrasset.jpg/960px-Aeroport_Tamanrasset.jpg"
    ]
    assert response.headers["x-isite2-hero-image-upstream-url"] == calls[0]


def test_hero_image_proxy_falls_back_for_wikimedia_variants(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_HERO_IMAGE_CACHE_DIR", str(tmp_path / "hero-images"))
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str, str]:
        calls.append(url)
        if "upload.wikimedia.org" in url:
            raise api_main._HeroImageUpstreamError("missing thumbnail", status_code=404)
        return b"image-bytes", "image/jpeg", url

    monkeypatch.setattr(api_main, "_fetch_hero_image_from_upstream", fake_fetch)
    client = TestClient(app)

    response = client.get(
        "/map/hero-image",
        params={"url": "https://commons.wikimedia.org/wiki/Special:FilePath/Test%20Image.jpg"},
    )

    assert response.status_code == 200
    assert calls == [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/7/74/"
        "Test_Image.jpg/960px-Test_Image.jpg",
        "https://commons.wikimedia.org/wiki/Special:Redirect/file/Test_Image.jpg?width=960",
    ]
    assert response.headers["x-isite2-hero-image-upstream-url"] == calls[1]


def test_hero_image_proxy_does_not_failure_cache_rate_limits(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_HERO_IMAGE_CACHE_DIR", str(tmp_path / "hero-images"))
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str, str]:
        calls.append(url)
        if len(calls) == 1:
            raise api_main._HeroImageUpstreamError("rate limited", status_code=429)
        return b"image-bytes", "image/jpeg", url

    monkeypatch.setattr(api_main, "_fetch_hero_image_from_upstream", fake_fetch)
    client = TestClient(app)

    first = client.get("/map/hero-image", params={"url": "https://images.example/a.jpg"})
    second = client.get("/map/hero-image", params={"url": "https://images.example/a.jpg"})

    assert first.status_code == 502
    assert second.status_code == 200
    assert calls == ["https://images.example/a.jpg", "https://images.example/a.jpg"]


def test_hero_image_proxy_blocks_private_hosts() -> None:
    client = TestClient(app)

    local_ip = client.get("/map/hero-image", params={"url": "http://127.0.0.1/a.jpg"})
    localhost = client.get("/map/hero-image", params={"url": "https://localhost/a.jpg"})

    assert local_ip.status_code == 400
    assert localhost.status_code == 400


def test_satellite_tile_proxy_uses_bundled_maptiler_token_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ISITE2_TILE_CACHE_DIR", str(tmp_path / "tiles"))
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TEMPLATE", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TOKEN", raising=False)
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return b"tile-bytes", "image/webp"

    monkeypatch.setattr(api_main, "_fetch_satellite_tile_from_upstream", fake_fetch)
    client = TestClient(app)

    response = client.get("/map/satellite-tiles/5/12/16")

    assert response.status_code == 200
    assert calls == [
        "https://api.maptiler.com/tiles/satellite-v2/5/16/12.jpg?key=rnpOwVToJlpNtgw35V2t"
    ]


def test_satellite_tile_proxy_uses_maptiler_token_without_exposing_it(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ISITE2_TILE_CACHE_DIR", str(tmp_path / "tiles"))
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TEMPLATE", raising=False)
    monkeypatch.setenv("ISITE2_SATELLITE_TILE_TOKEN", "secret-maptiler-token")
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_ATTRIBUTION", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_SIZE", raising=False)
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return b"tile-bytes", "image/jpeg"

    monkeypatch.setattr(api_main, "_fetch_satellite_tile_from_upstream", fake_fetch)
    client = TestClient(app)

    config = client.get("/runtime-config").json()
    response = client.get("/map/satellite-tiles/3/2/4")

    assert response.status_code == 200
    assert calls == [
        "https://api.maptiler.com/tiles/satellite-v2/3/4/2.jpg?key=secret-maptiler-token"
    ]
    assert config["map"]["satelliteTileTemplate"] == "/map/satellite-tiles/{z}/{y}/{x}"
    assert config["map"]["satelliteTileSize"] == 512
    assert config["map"]["satelliteAttribution"] == "Source: MapTiler Satellite"
    assert "secret-maptiler-token" not in str(config)


def test_satellite_tile_proxy_validates_and_negative_caches(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_TILE_CACHE_DIR", str(tmp_path / "tiles"))
    monkeypatch.setenv("ISITE2_SATELLITE_TILE_TEMPLATE", "https://tiles.example/{z}/{y}/{x}.jpg")
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TOKEN", raising=False)
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str]:
        calls.append(url)
        raise RuntimeError("blocked upstream")

    monkeypatch.setattr(api_main, "_fetch_satellite_tile_from_upstream", fake_fetch)
    client = TestClient(app)

    assert client.get("/map/satellite-tiles/19/0/0").status_code == 400
    assert client.get("/map/satellite-tiles/2/4/0").status_code == 400
    first_failure = client.get("/map/satellite-tiles/1/0/1")
    second_failure = client.get("/map/satellite-tiles/1/0/1")

    assert first_failure.status_code == 502
    assert second_failure.status_code == 502
    assert len(calls) == 1


def test_satellite_tile_proxy_breaker_stops_fatal_request_storm(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_TILE_CACHE_DIR", str(tmp_path / "tiles"))
    monkeypatch.setenv("ISITE2_SATELLITE_TILE_TEMPLATE", "https://tiles.example/{z}/{y}/{x}.jpg")
    calls = []

    def fake_fetch(url: str) -> tuple[bytes, str]:
        calls.append(url)
        raise api_main._SatelliteTileUpstreamError("forbidden", status_code=403)

    monkeypatch.setattr(api_main, "_fetch_satellite_tile_from_upstream", fake_fetch)
    client = TestClient(app)

    first_failure = client.get("/map/satellite-tiles/1/0/0")
    breaker_failure = client.get("/map/satellite-tiles/1/0/1")

    assert first_failure.status_code == 502
    assert breaker_failure.status_code == 503
    assert calls == ["https://tiles.example/1/0/0.jpg"]


def test_scan_run_post_list_get_and_top_n_display_only() -> None:
    client = TestClient(app)
    response = client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Exampleland"],
                "full_scan": True,
                "output_formats": ["excel"],
            }
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "completed"
    assert created["candidate_count"] == len(scene_definitions())
    assert len(created["packets"]) == len(scene_definitions())

    listed = client.get("/scan-runs").json()
    assert len(listed) == 1
    assert "packets" not in listed[0]

    fetched = client.get(f"/scan-runs/{created['run_id']}?top_n=2").json()
    assert fetched["candidate_count"] == len(scene_definitions())
    assert fetched["display_count"] == 2
    assert len(fetched["packets"]) == 2


def test_map_properties_returns_geojson_after_scan() -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Exampleland"],
                "full_scan": True,
                "scene_types": ["airport_terminal"],
                "output_formats": ["geojson"],
            }
        },
    )

    feature_collection = client.get("/map/properties").json()
    assert feature_collection["type"] == "FeatureCollection"
    assert feature_collection["features"] == []

    raw_collection = client.get(
        "/map/properties",
        params={"include_blocked_quality": True},
    ).json()
    assert raw_collection["features"][0]["geometry"]["coordinates"] == [100.0, 20.0]
    assert (
        raw_collection["features"][0]["properties"]["candidate_quality_status"] == "blocked_quality"
    )


def test_property_detail_and_review_queue_support_world_map_drawer() -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Exampleland"],
                "full_scan": True,
                "scene_types": ["airport_terminal", "stadium"],
                "output_formats": ["geojson"],
            }
        },
    )

    properties = client.get(
        "/properties",
        params={"scene_type": "airport_terminal", "has_review_issue": True},
    ).json()
    assert properties["candidate_count"] == 0

    properties = client.get(
        "/properties",
        params={
            "scene_type": "airport_terminal",
            "has_review_issue": True,
            "include_blocked_quality": True,
            "locale": "zh",
        },
    ).json()
    assert properties["candidate_count"] == 1
    assert properties["display_count"] == 1
    assert properties["locale"] == "zh"
    assert properties["packets"][0]["localized"]["entity"]["scene_label"] == "机场"
    property_id = properties["packets"][0]["entity"]["property_id"]

    detail = client.get(f"/properties/{property_id}", params={"locale": "zh"}).json()
    assert detail["entity"]["property_name"].endswith("MVP Candidate")
    assert detail["localized"]["entity"]["scene_label"] == "机场"
    assert detail["candidate_quality_status"] == "blocked_quality"
    assert detail["visibility"]["map_ready"] is False
    assert detail["evidence"]
    assert detail["inference"]
    assert detail["review_queue"][0]["next_action"].startswith("补查")
    assert any(item["review_type"] == "candidate_quality" for item in detail["review_queue"])

    english_detail = client.get(f"/properties/{property_id}", params={"locale": "en"}).json()
    english_conclusion = english_detail["localized"]["conclusion"]
    assert not re.search(r"[\u4e00-\u9fff]", english_conclusion["reason_to_recommend"])
    assert not re.search(r"[\u4e00-\u9fff]", english_conclusion["next_action"])
    assert english_conclusion["reason_to_recommend_localization_status"] in {
        "cache_miss",
        "ok",
        "same_locale",
    }
    assert english_conclusion["next_action_localization_status"] in {
        "cache_miss",
        "ok",
        "same_locale",
    }
    assert "primary_metric" in english_detail["localized"]
    assert not re.search(
        r"[\u4e00-\u9fff]",
        english_detail["localized"]["primary_metric"]["display_text"],
    )

    review_queue = client.get("/review-queue", params={"status": "open", "locale": "zh"}).json()
    assert len(review_queue) == 4
    assert {item["scene_type"] for item in review_queue} == {"airport_terminal", "stadium"}
    assert any(item["candidate_quality_status"] == "blocked_quality" for item in review_queue)
    assert all(item["localized"]["locale"] == "zh" for item in review_queue)


def test_raw_evidence_and_candidate_draft_endpoints_show_curation_state(tmp_path) -> None:
    client = TestClient(app)
    draft = _curation_api_draft()

    curation_store.upsert_candidate_evidence(draft)
    raw_evidence = client.get(
        "/raw-evidence",
        params={
            "status": "new",
            "country": "Nigeria",
            "scene_type": "mall_mixed_use",
            "source_type": "operator_venue",
        },
    ).json()
    run_pending_evidence_curation(
        store=curation_store,
        output_dir=tmp_path / "loop",
        overlay_path=tmp_path / "overlay.yaml",
        draft_path=tmp_path / "drafts.json",
    )
    candidate_drafts = client.get(
        "/candidate-drafts",
        params={
            "status": "candidate_accepted",
            "country": "Nigeria",
            "scene_type": "mall_mixed_use",
            "source_type": "operator_venue",
        },
    ).json()

    matching_raw = [
        item for item in raw_evidence["items"] if item["property_name"] == draft.property_name
    ]
    matching_drafts = [
        item for item in candidate_drafts["items"] if item["property_name"] == draft.property_name
    ]

    assert matching_raw
    assert matching_raw[0]["status"] == "new"
    assert matching_raw[0]["source_url"].startswith("https://")
    assert matching_raw[0]["source_type"] == "operator_venue"
    assert matching_drafts
    assert matching_drafts[0]["status"] == "candidate_accepted"
    assert matching_drafts[0]["source_type"] == "operator_venue"


def test_discovery_status_endpoint_returns_backlog_shape() -> None:
    client = TestClient(app)

    status = client.get("/discovery/status").json()

    assert "task_backlog" in status
    assert "progress_status" in status
    assert "progress" in status
    assert "pending_evidence_count" in status
    assert "raw_evidence_status" in status


def test_rag_index_and_query_endpoints_return_cited_answer() -> None:
    client = TestClient(app)
    created = client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria"],
                "full_scan": True,
                "scene_types": ["airport_terminal"],
                "output_formats": ["geojson"],
            }
        },
    ).json()

    index_result = client.post("/rag/index", json={"scan_run_id": created["run_id"]}).json()
    answer = client.post(
        "/rag/query",
        json={
            "scan_run_id": created["run_id"],
            "country": "Algeria",
            "question": "Why is the airport recommended?",
            "top_k": 4,
        },
    ).json()

    assert index_result["indexed_documents"] >= 1
    assert answer["citations"]
    assert answer["priority_recommendations"]
    assert answer["review_actions"]
    assert all(item["source_url"] for item in answer["citations"])


def test_map_properties_supports_extended_filters_and_drawer_fields() -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria"],
                "full_scan": True,
                "scene_types": ["airport_terminal"],
                "output_formats": ["geojson"],
            }
        },
    )

    feature_collection = client.get(
        "/map/properties",
        params={
            "value_class": "City Core",
            "indoor_rat": "Unknown",
            "proxy_level": "P1 Strong Proxy",
            "has_review_issue": True,
        },
    ).json()

    feature = feature_collection["features"][0]
    assert feature["properties"]["indoor_rat"] == "Unknown"
    assert feature["properties"]["proxy_level"] == "P1 Strong Proxy"
    assert feature["properties"]["last_scan_at"]
    assert feature["properties"]["annual_visits_est"] == 9151517.0
    assert feature["properties"]["source_count"] >= 1
    assert feature["properties"]["review_count"] >= 1
    assert feature["properties"]["localized"]["scene_label"]
    assert feature["properties"]["candidate_quality_status"] == "ready"
    assert feature["properties"]["visibility"]["map_ready"] is True


def _curation_api_draft() -> CandidateDraft:
    return CandidateDraft(
        region="Africa",
        country="Nigeria",
        city="Lagos",
        property_name="API Evidence Mall",
        scene_type="mall_mixed_use",
        annual_visits=1_000_000,
        latitude=6.45,
        longitude=3.4,
        geocode_precision="venue centroid",
        map_source="OpenStreetMap public coordinates",
        map_source_date="2026-05-08",
        field_group="mixed_use_role",
        indicator_name="public_destination_role",
        field_value="Major public mall and mixed-use destination in Lagos.",
        source_name="Example public source",
        source_tier="Tier 3",
        source_url="https://example.org/api-evidence-mall",
        source_date="2026-05-08",
        source_type="operator_venue",
        bbox={
            "min_latitude": 4.0,
            "max_latitude": 14.2,
            "min_longitude": 2.5,
            "max_longitude": 15.0,
        },
    )


def test_algeria_scan_is_visible_as_map_opportunity_points() -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )

    feature_collection = client.get("/map/properties", params={"country": "Algeria"}).json()
    names = {feature["properties"]["property_name"] for feature in feature_collection["features"]}

    assert len(feature_collection["features"]) == 4
    assert "Houari Boumediene International Airport" in names
    assert "Centre Commercial et de Loisirs Bab Ezzouar" in names
    assert all(
        2.5 <= feature["geometry"]["coordinates"][0] <= 3.4
        for feature in feature_collection["features"]
    )
    assert all(
        feature["properties"]["hero_image_url"].startswith("https://")
        for feature in feature_collection["features"]
    )

    city_summary = client.get("/map/city-summary", params={"country": "Algeria"}).json()
    assert sum(row["candidate_count"] for row in city_summary["cities"]) == 4
    assert all(row["lat"] and row["lng"] for row in city_summary["cities"])
    assert all(row["position_source"] == "map_ready_average" for row in city_summary["cities"])


def test_country_summary_supports_scan_run_filter_and_kpis() -> None:
    client = TestClient(app)
    algeria = client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    ).json()
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Egypt"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )

    all_features = client.get("/map/properties").json()
    all_feature_countries = {
        feature["properties"]["country"] for feature in all_features["features"]
    }
    top_properties = client.get("/properties", params={"top_n": 5}).json()
    all_summary = client.get("/map/country-summary").json()
    summary_by_country = {row["country"]: row for row in all_summary}

    assert len(all_features["features"]) == 8
    assert top_properties["candidate_count"] == 8
    assert top_properties["display_count"] == 5
    assert len(top_properties["packets"]) == 5
    assert all_feature_countries == {"Algeria", "Egypt"}
    assert set(summary_by_country) == {"Algeria", "Egypt"}
    assert summary_by_country["Algeria"]["candidate_count"] == 4
    assert summary_by_country["Egypt"]["candidate_count"] == 4
    assert summary_by_country["Algeria"]["scan_maturity"]["level"] == "seed_scan"
    assert summary_by_country["Egypt"]["scan_maturity"]["level"] == "seed_scan"

    summary = client.get(
        "/map/country-summary",
        params={"scan_run_id": algeria["run_id"]},
    ).json()

    assert [row["country"] for row in summary] == ["Algeria"]
    assert summary[0]["candidate_count"] == 4
    assert summary[0]["map_point_count"] == 4
    assert summary[0]["source_count"] >= 8


def test_country_summary_recomputes_after_database_changes() -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )
    first_summary = client.get("/map/country-summary").json()
    assert {row["country"] for row in first_summary} == {"Algeria"}

    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Egypt"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )
    second_summary = client.get("/map/country-summary").json()
    summary_by_country = {row["country"]: row for row in second_summary}

    assert set(summary_by_country) == {"Algeria", "Egypt"}
    assert summary_by_country["Algeria"]["candidate_count"] == 4
    assert summary_by_country["Egypt"]["candidate_count"] == 4


def test_outputs_can_export_current_database_pool_with_filters() -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Egypt"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )

    excel = client.post("/outputs/excel", json={"locale": "zh"}).json()
    ppt = client.post(
        "/outputs/ppt",
        json={"country": "Algeria", "scene_type": "airport_terminal"},
    ).json()

    assert excel["artifact_type"] == "excel"
    assert excel["candidate_count"] == 8
    assert excel["filter_snapshot"] == {
        "locale": "zh",
        "evidence_text_mode": "source_and_translation",
    }
    assert "scan_run_id" not in excel
    assert ppt["artifact_type"] == "ppt"
    assert ppt["candidate_count"] == 1
    assert ppt["filter_snapshot"] == {
        "country": "Algeria",
        "scene_type": "airport_terminal",
        "locale": "en",
        "evidence_text_mode": "source_and_translation",
    }


def test_property_search_returns_lightweight_global_results() -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria", "Egypt"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )

    response = client.get("/properties/search", params={"q": "airport", "limit": 20})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "airport"
    assert payload["count"] >= 2
    assert all(
        set(result) == {
            "property_id",
            "property_name",
            "matched_name",
            "match_type",
            "country",
            "city",
            "scene_type",
        }
        for result in payload["results"]
    )
    assert client.get("/properties/search", params={"q": "   "}).status_code == 422


def test_country_excel_download_requires_audited_current_report(tmp_path, monkeypatch) -> None:
    client = TestClient(app)
    client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "country",
                "countries": ["Algeria"],
                "full_scan": True,
                "output_formats": ["geojson"],
            }
        },
    )
    state_hash = repository.country_export_state_hash("Algeria")
    report_root = tmp_path / "public_reports"
    report_dir = report_root / "releases" / "content-hash"
    report_dir.mkdir(parents=True)
    report = report_dir / "isite_algeria_standard_report_zh_20260810T000000.xlsx"
    monkeypatch.setenv("ISITE2_RECOMMENDATION_METRIC_GPT", "0")
    write_excel_skeleton(
        report,
        packets=repository.list_properties({"country": "Algeria"}),
        locale="zh",
    )
    index = {
        "version": 1,
        "reports": {
            "Algeria": {
                "audit_status": "pass",
                "country_state_hash": state_hash,
                "locales": {
                    "zh": {
                        "path": f"releases/content-hash/{report.name}",
                        "filename": report.name,
                        "sha256": file_sha256(report),
                    }
                },
            }
        },
    }
    (report_root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setenv("ISITE2_PUBLIC_REPORT_ROOT", str(report_root))

    response = client.get(
        "/outputs/excel/country",
        params={"country": "Algeria", "locale": "zh"},
    )

    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    assert report.name in response.headers["content-disposition"]

    index["reports"]["Algeria"]["country_state_hash"] = "stale"
    (report_root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    stale = client.get(
        "/outputs/excel/country",
        params={"country": "Algeria", "locale": "zh"},
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"]


def test_registry_backed_scan_expands_runtime_overlay(monkeypatch) -> None:
    client = TestClient(app)
    registry = deepcopy(load_source_registry())
    registry["countries"]["Nigeria"] = {
        "aliases": ["Nigeria"],
        "bbox": {
            "min_latitude": 4.0,
            "max_latitude": 14.2,
            "min_longitude": 2.5,
            "max_longitude": 15.0,
        },
        "candidates": [
            {
                "property_name": "Murtala Muhammed International Airport",
                "city": "Lagos",
                "scene_type": "airport_terminal",
                "annual_visits": 7_000_000,
                "coordinate": {
                    "latitude": 6.5774,
                    "longitude": 3.3212,
                    "geocode_precision": "venue centroid",
                    "map_source": "test runtime overlay",
                    "map_source_date": "2026-05-09",
                    "coordinate_status": "Verified",
                },
                "discovery_source": "test_runtime_overlay",
                "evidence": [
                    {
                        "field_group": "airport_role",
                        "indicator_name": "gateway_role",
                        "field_value": "Primary international airport serving Lagos.",
                        "source_name": "Test public source",
                        "source_tier": "Tier 3",
                        "source_url": "https://example.org/lagos-airport",
                        "source_date": "2026-05-09",
                        "evidence_type": "Direct",
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(api_main, "load_effective_source_registry", lambda: registry)

    created = client.post(
        "/scan-runs",
        json={
            "scope": {
                "level": "region",
                "regions": ["Africa"],
                "countries": [],
                "full_scan": True,
                "output_formats": ["geojson"],
                "custom_filters": {"registry_backed_only": True},
            }
        },
    ).json()
    countries = {packet["entity"]["country"] for packet in created["packets"]}

    assert created["candidate_count"] == len(created["packets"])
    assert "Nigeria" in countries
    assert "Nigeria" in created["scope"]["countries"]
    assert set(created["scope"]["countries"]).issubset(set(registry["countries"]))


def _insert_property_only(
    *,
    country: str = "Brazil",
    latitude: float = -23.55,
    longitude: float = -46.63,
    canonical_name: str = "Overlay Test Property",
) -> str:
    property_id = str(uuid4())
    now = datetime.now(UTC)
    with repository.session_factory.begin() as session:
        session.add(
            PropertyDB(
                id=property_id,
                property_identity_key=f"{country.casefold()}|airport_terminal|overlay|{property_id}",
                canonical_name=canonical_name,
                country=country,
                city="São Paulo",
                scene_type="airport_terminal",
                scene_form="indoor",
                latitude=latitude,
                longitude=longitude,
                geocode_precision="venue centroid",
                coordinate_status="Verified",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SceneModelResultDB(
                id=str(uuid4()),
                property_id=property_id,
                scan_run_id=None,
                area_metric_name="Annual Passenger Throughput",
                area_metric_status="Direct",
                proxy_basis="Direct annual passenger throughput",
                proxy_level="P0 Direct",
                annual_visits_est=1_000_000,
            )
        )
        session.add(
            BuildStatusDB(
                id=str(uuid4()),
                property_id=property_id,
                scan_run_id=None,
                indoor_system_presence="Unknown",
                indoor_system_type="Unknown",
                indoor_rat="Unknown",
                build_evidence_status="Unknown",
            )
        )
        session.add(
            ConclusionDB(
                id=str(uuid4()),
                property_id=property_id,
                scan_run_id=None,
                evidence_status="Direct Evidence",
                value_class="High Value",
                action_class="Survey First",
                recommended_solution="DAS",
                reason_to_recommend="Overlay API test fixture.",
                next_action="Validate overlay source metadata.",
            )
        )
    return property_id


def _insert_property_with_mobile_ookla(
    *,
    country: str = "Brazil",
    latitude: float = -23.55,
    longitude: float = -46.63,
    canonical_name: str = "Overlay Test Property",
    download_mbps: float = 72.0,
    period: str = "2026 Q1",
) -> str:
    property_id = _insert_property_only(
        country=country,
        latitude=latitude,
        longitude=longitude,
        canonical_name=canonical_name,
    )
    quadkey = latlon_to_quadkey(latitude, longitude)
    tile_lat, tile_lng = quadkey_centroid(quadkey)
    observation_id = str(uuid4())
    now = datetime.now(UTC)
    with repository.session_factory.begin() as session:
        session.add(
            NetworkPerformanceObservationDB(
                id=observation_id,
                property_id=property_id,
                country=country,
                service_type="mobile",
                period=period,
                quadkey=quadkey,
                tile_latitude=tile_lat,
                tile_longitude=tile_lng,
                avg_download_mbps=download_mbps,
                avg_upload_mbps=18.0,
                avg_latency_ms=31.0,
                avg_loaded_latency_down_ms=96.0,
                avg_loaded_latency_up_ms=118.0,
                tests=24,
                devices=9,
                match_method="containing_tile",
                distance_m=100.0,
                confidence="high",
                source_url="https://ookla.example/mobile.parquet",
                source_checksum="fixture",
                source_accessed_at=now,
                license="Ookla Open Data",
            )
        )
        session.add(
            PropertyNetworkPerformanceRollupDB(
                id=str(uuid4()),
                property_id=property_id,
                country=country,
                service_type="mobile",
                period=period,
                observation_id=observation_id,
                performance_class="good",
                confidence="high",
                download_percentile=0.82,
                loaded_latency_percentile=0.32,
                calculated_at=now,
            )
        )
    return property_id
