from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from isite2.domain.enums import (
    ActionClass,
    BuildEvidenceStatus,
    CrossCheckStatus,
    EvidenceStatus,
    EvidenceType,
    IndoorRAT,
    IndoorSystemPresence,
    IndoorSystemType,
    ProxyLevel,
    RecommendedSolution,
    SceneForm,
    SourceTier,
    ValueClass,
)
from isite2.domain.models import (
    BuildStatus,
    Conclusion,
    EvidenceItem,
    PropertyEntity,
    ScanScope,
    SceneModelResult,
    SitePacket,
)
from isite2.growth.complaint_refresh import (
    refresh_property_complaints,
    source_is_allowed,
)
from isite2.growth.ookla import latlon_to_quadkey, quadkey_centroid
from isite2.growth.ookla_refresh import refresh_ookla_from_artifact
from isite2.growth.traffic_refresh import (
    refresh_traffic_estimates_v2,
    rollback_traffic_estimates_v2,
)
from isite2.public_api_preaggregation import build_public_api_preaggregation
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


def test_traffic_v2_persists_and_synchronizes_legacy_p50(tmp_path) -> None:
    repository, packet, run_id, _db_url = _saved_brazil_airport(tmp_path)

    first = refresh_traffic_estimates_v2(
        repository.engine,
        scan_run_id=str(run_id),
    )
    second = refresh_traffic_estimates_v2(
        repository.engine,
        scan_run_id=str(run_id),
    )
    loaded = repository.get_property_packet_for_run(str(packet.entity.property_id), str(run_id))

    assert first["counts"]["refreshed"] == 1
    assert second["counts"]["skipped_unchanged"] == 1
    assert loaded is not None
    assert loaded.traffic_estimate is not None
    assert loaded.traffic_estimate.estimate_method == "direct_annual"
    assert loaded.traffic_estimate.activation_status == "activated_direct"
    assert loaded.traffic_estimate.annual_visits_p50 == 37_600_000
    assert loaded.scene.annual_visits_est == 37_600_000
    assert loaded.scene.annual_visits_raw == 37_600_000


def test_shadow_mode_persists_v2_without_overwriting_legacy_fields(tmp_path) -> None:
    repository, packet, run_id, _db_url = _saved_brazil_airport(tmp_path)

    summary = refresh_traffic_estimates_v2(
        repository.engine,
        scan_run_id=str(run_id),
        activation_mode="shadow",
    )
    loaded = repository.get_property_packet_for_run(str(packet.entity.property_id), str(run_id))

    assert summary["counts"]["shadow_only"] == 1
    assert loaded is not None and loaded.traffic_estimate is not None
    assert loaded.traffic_estimate.activation_status == "shadow_forced"
    assert loaded.scene.annual_visits_est == 2025
    assert loaded.scene.annual_visits_raw is None


def test_v1_rollback_restores_pre_v2_scene_and_demand_snapshot(tmp_path) -> None:
    repository, packet, run_id, _db_url = _saved_brazil_airport(tmp_path)
    refresh_traffic_estimates_v2(repository.engine, scan_run_id=str(run_id))

    summary = rollback_traffic_estimates_v2(
        repository.engine,
        scan_run_id=str(run_id),
    )
    loaded = repository.get_property_packet_for_run(str(packet.entity.property_id), str(run_id))

    assert summary["counts"]["restored_v1"] == 1
    assert loaded is not None and loaded.traffic_estimate is not None
    assert loaded.traffic_estimate.activation_status == "rolled_back_v1"
    assert loaded.scene.annual_visits_est == 2025
    assert loaded.scene.annual_visits_raw is None
    assert loaded.demand is None


def test_public_index_does_not_promote_shadow_v2_range(tmp_path) -> None:
    repository, _packet, run_id, db_url = _saved_brazil_airport(tmp_path)
    refresh_traffic_estimates_v2(
        repository.engine,
        scan_run_id=str(run_id),
        activation_mode="shadow",
    )

    public = build_public_api_preaggregation(db_url, locales=["en"])
    index_row = public.rows["public_api_property_index"][0]
    packet_json = public.rows["public_api_property_packets"][0]["packet_json"]

    assert index_row["annual_visits_p50"] is None
    assert index_row["traffic_model_version"] is None
    assert index_row["feature_flags"]["traffic_v2"] is True
    assert index_row["feature_flags"]["traffic_v2_activated"] is False
    assert packet_json["traffic_estimate"]["activation_status"] == "shadow_forced"
    assert public.metadata["complaint_coverage"] == {
        "status": "no_qualified_property_level_data",
        "property_signal_count": 0,
        "valid_observation_count": 0,
    }


def test_complaints_are_aggregated_without_public_raw_text(tmp_path) -> None:
    repository, packet, _run_id, db_url = _saved_brazil_airport(tmp_path)
    policy = tmp_path / "complaint_sources.yaml"
    policy.write_text(
        """
default_policy: deny
property_level_sources:
  - domain: reviews.example
    approval_status: approved
    automation_allowed: true
    robots_allowed: true
    terms_allow_automation: true
    no_login_required: true
    last_verified_at: 2026-07-25
    verification_url: https://reviews.example/terms
rejected_source_patterns: [login, captcha]
""".strip(),
        encoding="utf-8",
    )
    records = [
        {
            "property_id": str(packet.entity.property_id),
            "text": (
                f"{packet.entity.property_name} em {packet.entity.city}, Brazil: "
                f"sem sinal 4G, ocorrência {index}"
            ),
            "source_url": f"https://reviews.example/items/{index}",
            "source_name": "Public Reviews",
            "observed_at": f"2026-07-{10 + index:02d}T12:00:00Z",
            "robots_allowed": True,
            "terms_allow_automation": True,
            "no_login_required": True,
        }
        for index in range(3)
    ]

    summary = refresh_property_complaints(
        repository.engine,
        records,
        source_policy_path=policy,
        source_manifest_path=str(tmp_path / "manifest.json"),
    )
    loaded = repository.get_property(packet.entity.property_id)

    assert summary["counts"]["observations_inserted"] == 3
    assert summary["public_raw_text"] is False
    assert loaded is not None and loaded.network_signals is not None
    assert loaded.network_signals.complaints is not None
    assert loaded.network_signals.complaints.valid_complaint_count == 3

    public = build_public_api_preaggregation(db_url, locales=["en"])
    assert public.metadata["complaint_coverage"] == {
        "status": "qualified_property_level_data_available",
        "property_signal_count": 1,
        "valid_observation_count": 3,
    }
    packet_json = public.rows["public_api_property_packets"][0]["packet_json"]
    assert packet_json["feature_flags"]["complaints"] is True
    assert "sanitized_text" not in json.dumps(packet_json)


def test_rejected_complaint_batch_does_not_recompute_all_property_rollups(
    tmp_path,
) -> None:
    repository, packet, _run_id, _db_url = _saved_brazil_airport(tmp_path)
    policy = tmp_path / "complaint_sources.yaml"
    policy.write_text(
        "default_policy: deny\nproperty_level_sources: []\n",
        encoding="utf-8",
    )

    summary = refresh_property_complaints(
        repository.engine,
        [
            {
                "property_id": str(packet.entity.property_id),
                "text": "sem sinal 4G",
                "source_url": "https://unapproved.example/review",
                "robots_allowed": True,
                "terms_allow_automation": True,
                "no_login_required": True,
            }
        ],
        source_policy_path=policy,
    )

    assert summary["counts"]["source_policy_rejected"] == 1
    assert summary["touched_property_count"] == 0
    assert summary["rollups"] == {
        "property_count": 0,
        "percentile_updates": 0,
    }


def test_complaint_record_cannot_self_attest_source_compliance() -> None:
    policy = {
        "property_level_sources": [
            {
                "domain": "reviews.example",
                "automation_allowed": True,
            }
        ]
    }
    record = {
        "robots_allowed": True,
        "terms_allow_automation": True,
        "no_login_required": True,
    }

    assert source_is_allowed("https://reviews.example/item/1", record, policy) is False


def test_ookla_is_internal_by_default_and_public_packet_keeps_feature_flag(
    tmp_path,
    monkeypatch,
) -> None:
    repository, packet, _run_id, db_url = _saved_brazil_airport(tmp_path)
    quadkey = latlon_to_quadkey(packet.entity.latitude, packet.entity.longitude)
    tile_latitude, tile_longitude = quadkey_centroid(quadkey)
    artifact = tmp_path / "ookla.json"
    artifact.write_text(
        json.dumps(
            {
                "source_period": "2026 Q1",
                "results": [
                    {
                        "id": str(packet.entity.property_id),
                        "country": "Brazil",
                        "latitude": packet.entity.latitude,
                        "longitude": packet.entity.longitude,
                        "ookla": {
                            "mobile": {
                                "period": "2026 Q1",
                                "source_url": "https://ookla.example/mobile.parquet",
                                "quadkey": quadkey,
                                "tile_centroid": {
                                    "latitude": tile_latitude,
                                    "longitude": tile_longitude,
                                },
                                "distance_to_tile_centroid_m": 100,
                                "avg_download_mbps": 20,
                                "avg_upload_mbps": 5,
                                "avg_latency_ms": 30,
                                "avg_loaded_latency_down_ms": 300,
                                "avg_loaded_latency_up_ms": 250,
                                "tests": 20,
                                "devices": 8,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    refresh_ookla_from_artifact(repository.engine, artifact)
    loaded = repository.get_property(packet.entity.property_id)
    assert loaded is not None and loaded.network_signals is not None
    assert loaded.network_signals.ookla.mobile is not None

    monkeypatch.delenv("ISITE2_PUBLIC_OOKLA_ENABLED", raising=False)
    public = build_public_api_preaggregation(db_url, locales=["en"])
    packet_json = public.rows["public_api_property_packets"][0]["packet_json"]
    assert packet_json["network_signals"]["ookla"] == {"mobile": None, "fixed": None}
    assert packet_json["feature_flags"]["ookla_public"] is False


def _saved_brazil_airport(
    tmp_path: Path,
) -> tuple[SQLAlchemyScanRunRepository, SitePacket, object, str]:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'signals.db'}"
    repository = SQLAlchemyScanRunRepository.from_url(db_url, storage_mode="sqlite")
    property_id = uuid4()
    packet = SitePacket(
        entity=PropertyEntity(
            property_id=property_id,
            country="Brazil",
            city="São Paulo",
            property_name="Aeroporto Internacional Teste",
            scene_type="airport_terminal",
            scene_form=SceneForm.INDOOR,
            latitude=-23.5505,
            longitude=-46.6333,
            geocode_precision="terminal centroid",
        ),
        scene=SceneModelResult(
            area_metric_name="Annual Passenger Throughput",
            area_metric_status="Direct",
            proxy_basis="Direct annual passenger throughput",
            proxy_level=ProxyLevel.P0_DIRECT,
            annual_visits_est=2025,
        ),
        evidence=[
            EvidenceItem(
                property_id=property_id,
                field_group="annual_passenger_throughput",
                indicator_name="annual_passenger_throughput",
                field_value="37.6 million passengers in 2024",
                unit="passengers/year",
                source_name="Airport annual report",
                source_tier=SourceTier.TIER_1,
                source_url="https://airport.example/annual-report",
                evidence_type=EvidenceType.DIRECT,
                cross_check_status=CrossCheckStatus.SINGLE_SOURCE,
            )
        ],
        build_status=BuildStatus(
            indoor_system_presence=IndoorSystemPresence.UNKNOWN,
            indoor_system_type=IndoorSystemType.UNKNOWN,
            indoor_rat=IndoorRAT.UNKNOWN,
            build_evidence_status=BuildEvidenceStatus.UNKNOWN,
        ),
        conclusion=Conclusion(
            evidence_status=EvidenceStatus.SUPPORTED,
            value_class=ValueClass.NATIONAL_FLAGSHIP,
            action_class=ActionClass.SURVEY_FIRST,
            recommended_solution=RecommendedSolution.PRRU,
            reason_to_recommend="Direct annual passenger evidence supports high value.",
            next_action="Verify indoor RF performance.",
        ),
    )
    result = repository.create(
        ScanScope(
            level="country",
            countries=["Brazil"],
            scene_types=["airport_terminal"],
        )
    )
    result.packets = [packet]
    result.scan_run.status = "completed"
    repository.save(result)
    return repository, packet, result.scan_run.run_id, db_url
