import yaml

from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


def test_overlay_sync_persists_only_quality_passed_candidates_and_is_idempotent(tmp_path) -> None:
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

    assert first.created is True
    assert len(repository.list()) == 1
    assert any(packet.entity.property_name == "Overlay Test Stadium" for packet in packets)
    assert not any(packet.entity.property_name == "Overlay Blocked Airport" for packet in packets)
    assert all(packet.candidate_quality_status == "ready" for packet in packets)

    second = sync_overlay_to_active_repository(repository, overlay_path=overlay_path)

    assert second.created is False
    assert second.skipped_reason == "overlay hash already synced to active repository"
    assert len(repository.list()) == 1


def test_overlay_sync_refreshes_derived_when_hash_already_synced(
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
    assert refresh_calls == [str(first.run_id), str(first.run_id)]
    assert second.derived_refresh == {
        "mode": "gpt_derived_info_refresh",
        "scan_run_id": str(first.run_id),
        "target_count": 1,
        "error_count": 0,
    }


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

    assert second.created is True
    assert len(repository.list()) == 1
    assert not any(packet.entity.property_name == "Overlay Test Stadium" for packet in egypt_packets)
    assert not any(packet.entity.property_name == "Overlay Blocked Airport" for packet in egypt_packets)
    assert len(egypt_packets) == 4


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
