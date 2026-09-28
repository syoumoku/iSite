import yaml

from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


def test_overlay_sync_persists_authoritative_visibility_snapshot_and_is_idempotent(
    tmp_path,
) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_sync.db'}",
        storage_mode="sqlite",
    )
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "version": "runtime-0.1",
                "countries": {
                    "Egypt": {
                        "aliases": ["Egypt"],
                        "bbox": {
                            "min_latitude": 22.0,
                            "max_latitude": 31.8,
                            "min_longitude": 24.6,
                            "max_longitude": 37.1,
                        },
                        "candidates": [
                            _valid_overlay_candidate(),
                            _blocked_overlay_candidate(),
                        ],
                    }
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    first = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)
    packets = repository.list_properties({})
    visible_page = repository.list_property_page(
        {}, surface="main_table", include_blocked_quality=False, limit=20
    )

    assert first.created is True
    assert first.candidate_count == first.registry_candidate_count - 1
    assert first.blocked_candidate_count == 1
    assert len(repository.list()) == 1
    assert any(packet.entity.property_name == "Overlay Test Stadium" for packet in packets)
    assert any(packet.entity.property_name == "Overlay Blocked Airport" for packet in packets)
    assert visible_page["candidate_count"] == first.candidate_count
    assert any(
        packet.entity.property_name == "Overlay Test Stadium"
        for packet in visible_page["packets"]
    )

    second = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)

    assert second.created is False
    assert second.skipped_reason == "overlay hash already synced to active repository"
    assert len(repository.list()) == 1


def test_overlay_sync_skips_derived_when_hash_already_synced(
    tmp_path,
    monkeypatch,
) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_sync.db'}",
        storage_mode="sqlite",
    )
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "version": "runtime-0.1",
                "countries": {
                    "Egypt": {
                        "aliases": ["Egypt"],
                        "bbox": {
                            "min_latitude": 22.0,
                            "max_latitude": 31.8,
                            "min_longitude": 24.6,
                            "max_longitude": 37.1,
                        },
                        "candidates": [_valid_overlay_candidate()],
                    }
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    refresh_calls: list[str] = []

    def fake_refresh(_repository: SQLAlchemyScanRunRepository, scan_run_id) -> dict:
        refresh_calls.append(str(scan_run_id))
        return {
            "mode": "gpt_derived_info_refresh",
            "scan_run_id": str(scan_run_id),
            "target_count": 1,
            "error_count": 0,
        }

    monkeypatch.setattr(
        "isite2.growth.overlay_sync._refresh_derived_after_sync",
        fake_refresh,
    )

    first = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)
    second = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)

    assert first.created is True
    assert second.created is False
    assert refresh_calls == [str(first.run_id)]
    assert second.derived_refresh == {
        "mode": "gpt_derived_info_refresh",
        "scan_run_id": str(first.run_id),
        "skipped_reason": "overlay hash unchanged; derived fields were not refreshed",
    }


def test_overlay_sync_can_skip_derived_refresh_when_hash_already_synced(
    tmp_path,
    monkeypatch,
) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_sync.db'}",
        storage_mode="sqlite",
    )
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "version": "runtime-0.1",
                "countries": {
                    "Egypt": {
                        "aliases": ["Egypt"],
                        "bbox": {
                            "min_latitude": 22.0,
                            "max_latitude": 31.8,
                            "min_longitude": 24.6,
                            "max_longitude": 37.1,
                        },
                        "candidates": [_valid_overlay_candidate()],
                    }
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    refresh_calls: list[str] = []

    def fake_refresh(_repository: SQLAlchemyScanRunRepository, scan_run_id) -> dict:
        refresh_calls.append(str(scan_run_id))
        return {
            "mode": "gpt_derived_info_refresh",
            "scan_run_id": str(scan_run_id),
            "target_count": 1,
            "error_count": 0,
        }

    monkeypatch.setattr(
        "isite2.growth.overlay_sync._refresh_derived_after_sync",
        fake_refresh,
    )

    first = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)
    second = sync_overlay_to_active_repository(
        repository,
        overlay_path=overlay_path,
        refresh_derived=False,
    )

    assert first.created is True
    assert second.created is False
    assert second.skipped_reason == "overlay hash already synced to active repository"
    assert refresh_calls == [str(first.run_id)]
    assert second.derived_refresh == {
        "mode": "gpt_derived_info_refresh",
        "scan_run_id": str(first.run_id),
        "skipped_reason": "overlay sync derived refresh disabled",
    }


def test_overlay_sync_ignores_countries_without_registry_candidates(tmp_path) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_sync.db'}",
        storage_mode="sqlite",
    )
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "version": "runtime-0.1",
                "countries": {
                    "Metadata Only Country": {
                        "aliases": ["Metadata Only Country"],
                        "bbox": {
                            "min_latitude": -5.0,
                            "max_latitude": 5.0,
                            "min_longitude": 10.0,
                            "max_longitude": 20.0,
                        },
                        "candidates": [],
                    },
                    "Egypt": {
                        "aliases": ["Egypt"],
                        "bbox": {
                            "min_latitude": 22.0,
                            "max_latitude": 31.8,
                            "min_longitude": 24.6,
                            "max_longitude": 37.1,
                        },
                        "candidates": [_valid_overlay_candidate()],
                    },
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)
    packets = repository.list_properties({})

    assert result.created is True
    assert all(packet.entity.country != "Metadata Only Country" for packet in packets)


def test_overlay_sync_exposes_designated_lead_with_missing_primary_metric(tmp_path) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_sync.db'}",
        storage_mode="sqlite",
    )
    candidate = _valid_overlay_candidate()
    candidate["property_name"] = "Designated Cairo Stadium"
    candidate["designated_lead"] = True
    candidate["primary_metric_status"] = "missing"
    candidate["designation_source"] = "https://example.org/customer-list"
    candidate["evidence"] = [
        {
            "field_group": "property_identity",
            "indicator_name": "property_identity",
            "field_value": "Exact venue identity confirmed from the designated list.",
            "source_name": "Official venue profile",
            "source_tier": "Tier 2",
            "source_url": "https://example.org/designated-cairo-stadium",
            "source_date": "2026-08-10",
            "evidence_type": "Context",
        }
    ]
    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "version": "runtime-0.1",
                "countries": {
                    "Egypt": {
                        "aliases": ["Egypt"],
                        "bbox": {
                            "min_latitude": 22.0,
                            "max_latitude": 31.8,
                            "min_longitude": 24.6,
                            "max_longitude": 37.1,
                        },
                        "candidates": [candidate],
                    }
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)
    packets = repository.list_properties({"country": "Egypt"})
    designated = next(
        packet
        for packet in packets
        if packet.entity.property_name == "Designated Cairo Stadium"
    )

    assert result.created is True
    assert designated.candidate_quality_status == "review_required"
    assert designated.visibility.map_ready is True
    assert designated.conclusion.evidence_status == "Insufficient"
    assert any(
        item.review_type == "designated_lead_primary_metric"
        for item in designated.review_queue
    )


def test_overlay_sync_replaces_old_sync_run_when_overlay_changes(tmp_path) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'isite2_sync.db'}",
        storage_mode="sqlite",
    )
    overlay_path = tmp_path / "overlay.yaml"
    initial_overlay = {
        "version": "runtime-0.1",
        "countries": {
            "Egypt": {
                "aliases": ["Egypt"],
                "bbox": {
                    "min_latitude": 22.0,
                    "max_latitude": 31.8,
                    "min_longitude": 24.6,
                    "max_longitude": 37.1,
                },
                "candidates": [_valid_overlay_candidate()],
            }
        },
    }
    overlay_path.write_text(
        yaml.safe_dump(initial_overlay, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    first = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)
    assert first.created is True
    assert any(
        packet.entity.property_name == "Overlay Test Stadium"
        for packet in repository.list_properties({"country": "Egypt"})
    )

    updated_overlay = {
        "version": "runtime-0.1",
        "countries": {
            "Egypt": {
                "aliases": ["Egypt"],
                "bbox": initial_overlay["countries"]["Egypt"]["bbox"],
                "candidates": [_blocked_overlay_candidate()],
            }
        },
    }
    overlay_path.write_text(
        yaml.safe_dump(updated_overlay, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    second = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)
    egypt_packets = repository.list_properties({"country": "Egypt"})
    visible_page = repository.list_property_page(
        {"country": "Egypt"},
        surface="main_table",
        include_blocked_quality=False,
        limit=20,
    )

    assert second.created is True
    assert len(repository.list()) == 1
    assert not any(packet.entity.property_name == "Overlay Test Stadium" for packet in egypt_packets)
    assert any(packet.entity.property_name == "Overlay Blocked Airport" for packet in egypt_packets)
    assert visible_page["candidate_count"] <= second.candidate_count
    assert all(
        packet.entity.property_name != "Overlay Blocked Airport"
        for packet in visible_page["packets"]
    )


def _valid_overlay_candidate() -> dict:
    return {
        "property_name": "Overlay Test Stadium",
        "city": "Cairo",
        "scene_type": "stadium",
        "annual_visits": 320000.0,
        "coordinate": {
            "latitude": 30.071,
            "longitude": 31.301,
            "geocode_precision": "stadium centroid",
            "map_source": "overlay fixture",
            "map_source_date": "2026-05-12",
            "coordinate_status": "Verified",
        },
        "hero_image": {
            "url": "https://example.com/overlay-test-stadium.jpg",
            "alt_text": "Overlay Test Stadium public image",
            "source_name": "Example venue page",
            "source_url": "https://example.com/overlay-test-stadium",
            "source_date": "2026-05-12",
            "license": "Public web image metadata",
        },
        "discovery_source": "overlay_test",
        "evidence": [
            {
                "field_group": "seat_count",
                "indicator_name": "seat_count",
                "field_value": "stadium capacity: 20,000 seats",
                "unit": "seats",
                "source_name": "Example venue page",
                "source_tier": "Tier 2",
                "source_url": "https://example.com/overlay-test-stadium",
                "source_date": "2026-05-12",
                "evidence_type": "Direct",
            }
        ],
    }


def _blocked_overlay_candidate() -> dict:
    return {
        "property_name": "Overlay Blocked Airport",
        "city": "Cairo",
        "scene_type": "airport_terminal",
        "annual_visits": 250000.0,
        "coordinate": {
            "latitude": 30.105,
            "longitude": 31.395,
            "geocode_precision": "airport terminal centroid",
            "map_source": "overlay fixture",
            "map_source_date": "2026-05-12",
            "coordinate_status": "Verified",
        },
        "discovery_source": "overlay_test",
        "evidence": [
            {
                "field_group": "airport_role",
                "indicator_name": "gateway_role",
                "field_value": "Regional airport identity only",
                "source_name": "Example airport page",
                "source_tier": "Tier 3",
                "source_url": "https://example.com/overlay-blocked-airport",
                "source_date": "2026-05-12",
                "evidence_type": "Direct",
            }
        ],
    }
