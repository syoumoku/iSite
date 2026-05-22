import os
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

TEST_DB_PATH = Path("outputs") / "test_api_dev.db"
os.environ["DATABASE_URL"] = "postgresql+psycopg://isite:isite@127.0.0.1:1/isite2_test"
os.environ["ISITE2_SQLITE_FALLBACK_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH}"

import isite2.api.main as api_main  # noqa: E402
from isite2.api.main import app, curation_store, repository  # noqa: E402
from isite2.growth.evidence_curation import run_pending_evidence_curation  # noqa: E402
from isite2.growth.evidence_intake import CandidateDraft  # noqa: E402
from isite2.rules.config_loader import load_source_registry, scene_definitions  # noqa: E402


def setup_function() -> None:
    api_main.app.state.overlay_sync_enabled = False
    api_main._reset_satellite_tile_breaker()
    repository.clear()


def test_health_and_rules_endpoints() -> None:
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert "airport_terminal" in client.get("/rules/scenes").json()["scenes"]
    output_template = client.get("/rules/output-template").json()
    assert output_template["excel"]["main_columns"][-1] == "Google地图链接"


def test_public_view_runtime_config_and_route_gate(monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_APP_MODE", "public_view")
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TEMPLATE", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_TOKEN", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_ATTRIBUTION", raising=False)
    monkeypatch.delenv("ISITE2_SATELLITE_TILE_SIZE", raising=False)
    client = TestClient(app)

    config = client.get("/runtime-config").json()
    assert config == {
        "mode": "public_view",
        "features": {
            "exports": False,
            "rag": False,
            "connectors": False,
            "geocode": False,
        },
        "map": {
            "satelliteTileTemplate": "/map/satellite-tiles/{z}/{y}/{x}",
            "satelliteTileSize": 512,
            "satelliteAttribution": "Source: MapTiler Satellite",
        },
    }
    assert client.get("/health").status_code == 200
    assert client.get("/rules/scenes").status_code == 200
    assert client.get("/scan-runs").status_code == 200
    assert client.get("/map/country-summary").status_code == 200
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


def test_satellite_tile_proxy_uses_bundled_maptiler_token_by_default(tmp_path, monkeypatch) -> None:
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


def test_satellite_tile_proxy_uses_maptiler_token_without_exposing_it(tmp_path, monkeypatch) -> None:
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
        raw_collection["features"][0]["properties"]["candidate_quality_status"]
        == "blocked_quality"
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
        },
    ).json()
    assert properties["candidate_count"] == 1
    assert properties["display_count"] == 1
    property_id = properties["packets"][0]["entity"]["property_id"]

    detail = client.get(f"/properties/{property_id}").json()
    assert detail["entity"]["property_name"].endswith("MVP Candidate")
    assert detail["candidate_quality_status"] == "blocked_quality"
    assert detail["visibility"]["map_ready"] is False
    assert detail["evidence"]
    assert detail["inference"]
    assert detail["review_queue"][0]["next_action"].startswith("补查")
    assert any(item["review_type"] == "candidate_quality" for item in detail["review_queue"])

    review_queue = client.get("/review-queue", params={"status": "open"}).json()
    assert len(review_queue) == 4
    assert {item["scene_type"] for item in review_queue} == {"airport_terminal", "stadium"}
    assert any(item["candidate_quality_status"] == "blocked_quality" for item in review_queue)


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
        item
        for item in candidate_drafts["items"]
        if item["property_name"] == draft.property_name
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
    assert feature["properties"]["annual_visits_est"] == 10000000.0
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
    names = {
        feature["properties"]["property_name"] for feature in feature_collection["features"]
    }

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
    all_summary = client.get("/map/country-summary").json()
    summary_by_country = {row["country"]: row for row in all_summary}

    assert len(all_features["features"]) == 8
    assert all_feature_countries == {"Algeria", "Egypt"}
    assert set(summary_by_country) == {"Algeria", "Egypt"}
    assert summary_by_country["Algeria"]["candidate_count"] == 4
    assert summary_by_country["Egypt"]["candidate_count"] == 4

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

    excel = client.post("/outputs/excel", json={}).json()
    ppt = client.post(
        "/outputs/ppt",
        json={"country": "Algeria", "scene_type": "airport_terminal"},
    ).json()

    assert excel["artifact_type"] == "excel"
    assert excel["candidate_count"] == 8
    assert excel["filter_snapshot"] == {}
    assert "scan_run_id" not in excel
    assert ppt["artifact_type"] == "ppt"
    assert ppt["candidate_count"] == 1
    assert ppt["filter_snapshot"] == {
        "country": "Algeria",
        "scene_type": "airport_terminal",
    }


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

    assert created["candidate_count"] == 9
    assert countries == {"Algeria", "Egypt", "Nigeria"}
    assert created["scope"]["countries"] == ["Algeria", "Egypt", "Nigeria"]
