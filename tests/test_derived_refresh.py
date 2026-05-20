from __future__ import annotations

from sqlalchemy import text

from isite2.growth.derived_refresh import (
    HARD_EVIDENCE_LABEL,
    LOW_EVIDENCE_LABEL,
    RuleSafetyFallbackProvider,
    refresh_active_derived_info,
)
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


class FakeGptDerivedProvider:
    provider_name = "fake_gpt"

    def __init__(self) -> None:
        self.calls = 0

    def derive(self, packet: dict) -> dict:
        self.calls += 1
        return {
            "provider_type": "gpt",
            "provider_model": "fake-gpt",
            "metric_availability_level": HARD_EVIDENCE_LABEL,
            "selected_primary_metric": {
                "field_group": "annual_passenger_throughput",
                "field_value": "12,000,000 passengers in 2025",
                "source_urls": ["https://example.org/traffic"],
                "numeric_value": 12_000_000,
                "unit": "visits/year",
            },
            "annual_visits_est": 12_000_000,
            "capacity_estimate": 12_000_000,
            "capacity_unit": "visits/year",
            "capacity_basis": "Annual passenger throughput",
            "evidence_status": "Supported",
            "value_class": "National Flagship",
            "action_class": "Survey First",
            "recommended_solution": "pRRU",
            "reason_to_recommend": "GPT聚合客观客流证据后判断为国家级旗舰机场，推荐pRRU。",
            "next_action": "补查运营商室分公告，并核验机场2025年客流统计的第二来源。",
            "inference_basis": "12,000,000 passengers in 2025",
            "inference_chain": "official traffic evidence -> annual_visits_est=12000000",
            "inference_confidence": "Conservative",
            "review_items": [],
        }


class OverconfidentGatewayProvider(FakeGptDerivedProvider):
    def derive(self, packet: dict) -> dict:
        decision = super().derive(packet)
        decision["selected_primary_metric"] = {
            "field_group": "gateway_role",
            "field_value": "Gateway role only",
            "numeric_value": 20_000_000,
            "unit": "visits/year",
        }
        decision["annual_visits_est"] = 20_000_000
        return decision


class VersionedFakeGptProvider(FakeGptDerivedProvider):
    def __init__(self, annual_visits: int = 12_000_000) -> None:
        super().__init__()
        self.annual_visits = annual_visits

    def derive(self, packet: dict) -> dict:
        decision = super().derive(packet)
        decision["annual_visits_est"] = self.annual_visits
        decision["selected_primary_metric"]["numeric_value"] = self.annual_visits
        decision["selected_primary_metric"]["field_value"] = (
            f"{self.annual_visits:,} passengers in 2025"
        )
        decision["inference_chain"] = (
            f"official traffic evidence -> annual_visits_est={self.annual_visits}"
        )
        return decision


def test_gpt_derived_refresh_updates_scene_demand_and_conclusion(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]

    summary = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=FakeGptDerivedProvider(),
        cache_dir=None,
    )
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert summary["provider"] == "fake_gpt"
    assert packet.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert packet.scene.area_metric_status == "GPT Aggregated Direct Metric"
    assert packet.scene.area_metric_name == "Annual Passenger Throughput"
    assert packet.scene.annual_visits_est == 12_000_000
    assert packet.demand is not None
    assert packet.demand.daily_visits and packet.demand.daily_visits > 32_000
    assert packet.conclusion.value_class == "National Flagship"
    assert packet.conclusion.reason_to_recommend.startswith("GPT聚合客观客流证据")
    assert (
        packet.inference[0].inference_chain
        == "official traffic evidence -> annual_visits_est=12000000"
    )


def test_gpt_refresh_downgrades_gateway_role_to_low_evidence(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE evidence_items
                SET field_group = 'gateway_role',
                    indicator_name = 'gateway_role',
                    field_value = 'Gateway airport role without passenger throughput'
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        )

    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=OverconfidentGatewayProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == LOW_EVIDENCE_LABEL
    assert refreshed.scene.annual_visits_est is None
    assert refreshed.conclusion.evidence_status == "Insufficient"
    assert any(
        review.review_type == "gpt_derived_info_refresh"
        for review in refreshed.review_queue
    )


def test_rule_refresh_derives_proxy_visits_and_busy_hour_trace_from_primary_metric(
    tmp_path,
) -> None:
    repository = _repository_with_stadium_capacity(tmp_path)
    result = repository.list()[0]

    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=RuleSafetyFallbackProvider(),
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert refreshed.scene.metric_availability_level == HARD_EVIDENCE_LABEL
    assert refreshed.scene.annual_visits_raw is None
    assert refreshed.scene.annual_visits_est == 600_000
    assert refreshed.demand is not None
    assert refreshed.demand.busy_hour_traffic_gb is not None
    inference_by_field = {item.inferred_field: item for item in refreshed.inference}
    assert "annual_visits_est" in inference_by_field
    assert "busy_hour_traffic_gb" in inference_by_field
    assert "seat_count evidence" in inference_by_field["annual_visits_est"].inference_chain
    assert "40000 x 15" in inference_by_field["annual_visits_est"].inference_chain
    assert "busy_hour_users = daily_visits x attach_rate" in (
        inference_by_field["busy_hour_traffic_gb"].inference_chain
    )


def test_unchanged_evidence_package_reuses_db_decision_without_gpt_call(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    first_provider = VersionedFakeGptProvider(annual_visits=12_000_000)

    first = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=first_provider,
        cache_dir=None,
    )
    second_provider = VersionedFakeGptProvider(annual_visits=99_000_000)
    second = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=second_provider,
        cache_dir=None,
    )
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert first_provider.calls == 1
    assert second_provider.calls == 0
    assert first["counts"]["gpt_analysis_requested_count"] == 1
    assert second["counts"]["skipped_unchanged_evidence"] == 1
    assert packet.scene.annual_visits_est == 12_000_000


def test_evidence_package_change_triggers_new_gpt_call(tmp_path) -> None:
    repository = _repository_with_airport(tmp_path)
    result = repository.list()[0]
    packet = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]
    property_id = str(packet.entity.property_id)

    first_provider = VersionedFakeGptProvider(annual_visits=12_000_000)
    refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=first_provider,
        cache_dir=None,
    )
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO evidence_items (
                    id, property_id, scan_run_id, field_group, indicator_name,
                    field_value, evidence_type, source_name, source_tier, source_url,
                    source_date, cross_check_status, created_at
                ) VALUES (
                    'extra-evidence', :property_id, :scan_run_id,
                    'annual_passenger_throughput', 'annual_passenger_throughput',
                    '13,000,000 passengers in 2025', 'Direct', 'Second source',
                    'Tier 2', 'https://example.org/second-source', '2026',
                    'Partial Cross-check', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "property_id": property_id,
                "scan_run_id": str(result.scan_run.run_id),
            },
        )

    second_provider = VersionedFakeGptProvider(annual_visits=13_000_000)
    second = refresh_active_derived_info(
        repository.engine,
        scan_run_id=str(result.scan_run.run_id),
        provider=second_provider,
        cache_dir=None,
    )
    refreshed = repository.list_properties({"scan_run_id": result.scan_run.run_id})[0]

    assert first_provider.calls == 1
    assert second_provider.calls == 1
    assert second["counts"]["gpt_analysis_requested_count"] == 1
    assert refreshed.scene.annual_visits_est == 13_000_000


def _repository_with_airport(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2.db'}",
        storage_mode="sqlite",
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
        source_registry=_single_airport_registry(),
    )
    return repository


def _repository_with_stadium_capacity(tmp_path) -> SQLAlchemyScanRunRepository:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_stadium.db'}",
        storage_mode="sqlite",
    )
    run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Ghana"],
            "full_scan": True,
            "scene_types": ["stadium"],
            "output_formats": ["geojson"],
        },
        repository,
        source_registry=_single_stadium_registry(),
    )
    return repository


def _single_airport_registry() -> dict:
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
                        "property_name": "Cairo International Airport",
                        "city": "Cairo",
                        "scene_type": "airport_terminal",
                        "annual_visits": 10_000_000,
                        "coordinate": {
                            "latitude": 30.1219,
                            "longitude": 31.4056,
                            "geocode_precision": "airport terminal centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-14",
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
                                "field_value": "10,000,000 passengers in 2024",
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


def _single_stadium_registry() -> dict:
    return {
        "countries": {
            "Ghana": {
                "aliases": ["Ghana"],
                "bbox": {
                    "min_latitude": 4.5,
                    "max_latitude": 11.5,
                    "min_longitude": -3.5,
                    "max_longitude": 1.5,
                },
                "candidates": [
                    {
                        "property_name": "Accra Sports Stadium",
                        "city": "Accra",
                        "scene_type": "stadium",
                        "coordinate": {
                            "latitude": 5.5529,
                            "longitude": -0.1914,
                            "geocode_precision": "stadium venue centroid",
                            "map_source": "test registry",
                            "map_source_date": "2026-05-15",
                            "coordinate_status": "Verified",
                        },
                        "hero_image": {
                            "url": "https://example.org/accra-sports-stadium.jpg",
                            "alt_text": "Accra Sports Stadium",
                            "source_name": "Example Image",
                            "source_url": "https://example.org/accra-stadium-image",
                            "source_date": "2026",
                        },
                        "discovery_source": "test_registry",
                        "evidence": [
                            {
                                "field_group": "seat_count",
                                "indicator_name": "seat_count",
                                "field_value": "40,000 seats",
                                "source_name": "Example Stadium Authority",
                                "source_tier": "Tier 1",
                                "source_url": "https://example.org/accra-stadium-capacity",
                                "source_date": "2026",
                                "evidence_type": "Direct",
                            }
                        ],
                    }
                ],
            }
        }
    }
