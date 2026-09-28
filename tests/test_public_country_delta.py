import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from scripts.publish_public_country_delta import (
    activate_public_reports,
    build_additive_rollback_sql,
    build_delta_sql,
    build_property_rollback_sql,
    compatible_remote_columns,
    normalize_countries,
    normalize_property_ids,
    strict_city_assignment_gate,
    validate_property_delta_report_state,
)

from isite2.public_api_preaggregation import PublicApiPreaggregation


def test_activate_public_reports_sends_script_over_ssh(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_ssh_input(target: str, command: str, input_text: str) -> str:
        captured.update(target=target, command=command, input_text=input_text)
        return ""

    monkeypatch.setattr(
        "scripts.publish_public_country_delta._ssh_input",
        fake_ssh_input,
    )
    artifacts = SimpleNamespace(release_name="country_delta_20260812T021434Z")

    activate_public_reports(
        artifacts,
        ssh_target="example-host",
        remote_dir="/opt/isite2",
    )

    assert captured["target"] == "example-host"
    assert captured["command"] == "bash -se"
    assert "/opt/isite2/public_reports" in captured["input_text"]
    assert "country_delta_20260812T021434Z" in captured["input_text"]


def test_normalize_countries_deduplicates_and_sorts() -> None:
    assert normalize_countries([" Zimbabwe ", "Armenia", "Armenia"]) == [
        "Armenia",
        "Zimbabwe",
    ]


def test_normalize_property_ids_deduplicates_and_sorts() -> None:
    assert normalize_property_ids([" property-2 ", "property-1", "property-1"]) == [
        "property-1",
        "property-2",
    ]


def test_strict_city_assignment_gate_requires_verified_consistent_city(
    tmp_path: Path,
) -> None:
    path = tmp_path / "city-gate.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE properties (
                id TEXT PRIMARY KEY,
                country TEXT NOT NULL,
                city TEXT NOT NULL,
                city_id TEXT,
                scene_type TEXT NOT NULL,
                property_identity_key TEXT NOT NULL
            );
            CREATE TABLE property_city_assignments (
                property_id TEXT PRIMARY KEY,
                city_id TEXT,
                canonical_city TEXT NOT NULL,
                mapping_status TEXT NOT NULL
            );
            INSERT INTO properties VALUES
                ('property-1', 'Armenia', 'Yerevan', 'AM:unlocode:evn',
                 'airport_terminal', 'armenia|airport|evn|airport');
            INSERT INTO property_city_assignments VALUES
                ('property-1', 'AM:unlocode:evn', 'Yerevan', 'verified');
            """
        )

    assert strict_city_assignment_gate(path, expected_property_count=1)["status"] == "passed"

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE property_city_assignments SET mapping_status = 'review_required'"
        )
        connection.commit()

    try:
        strict_city_assignment_gate(path, expected_property_count=1)
    except RuntimeError as error:
        assert "invalid_assignment_count': 1" in str(error)
    else:
        raise AssertionError("unverified city assignment must block publication")


def test_country_delta_sql_is_scoped_transactional_and_guarded() -> None:
    display_rows = {
        "scan_runs": [{"id": "run-1"}],
        "properties": [
            {
                "id": "property-1",
                "canonical_name": "Truncated Tower",
                "country": "Armenia",
            }
        ],
        "property_aliases": [],
        "scan_candidates": [],
        "evidence_items": [],
        "scene_model_results": [],
        "build_statuses": [],
        "demand_estimates": [],
        "inference_records": [],
        "conclusions": [],
        "review_queue": [],
        "discovery_progress": [],
        "localized_text_cache": [],
        "network_performance_tiles": [],
    }
    public_api = PublicApiPreaggregation(
        rows={
            "public_api_property_index": [
                {
                    "property_id": "property-1",
                    "country": "Armenia",
                }
            ],
            "public_api_property_packets": [],
            "public_api_map_features": [],
            "public_api_review_queue_rows": [],
        }
    )
    columns = {
        table: list(rows[0]) if rows else ["id"]
        for table, rows in display_rows.items()
    }
    columns.update(
        {
            "public_api_property_index": ["property_id", "country"],
            "public_api_property_packets": ["id"],
            "public_api_map_features": ["id"],
            "public_api_review_queue_rows": ["id"],
        }
    )

    sql = build_delta_sql(
        countries=["Armenia"],
        display_rows=display_rows,
        public_api=public_api,
        compatible_columns=columns,
        expected_country_counts={"Armenia": 1},
        require_target_absent=True,
    )

    assert sql.startswith(r"\set ON_ERROR_STOP on" + "\nBEGIN;")
    assert "Truncated Tower" in sql
    assert re.search(r"(?im)^\s*TRUNCATE\b", sql) is None
    assert "pg_advisory_xact_lock" in sql
    assert "target countries are not absent" in sql
    assert "non-target properties changed" in sql
    assert "WHERE country IN ('Armenia')" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_country_delta_uses_remote_compatible_public_columns() -> None:
    display_rows = {
        table: [] for table in (
            "scan_runs",
            "properties",
            "property_aliases",
            "scan_candidates",
            "evidence_items",
            "scene_model_results",
            "build_statuses",
            "demand_estimates",
            "inference_records",
            "conclusions",
            "review_queue",
            "discovery_progress",
            "localized_text_cache",
            "network_performance_tiles",
        )
    }
    public_api = PublicApiPreaggregation(
        rows={
            "public_api_property_index": [
                {
                    "property_id": "property-1",
                    "country": "Armenia",
                    "feature_flags": {"traffic_v2": True},
                }
            ],
            "public_api_property_packets": [],
            "public_api_map_features": [],
            "public_api_review_queue_rows": [],
        }
    )
    remote_columns = {table: ["id"] for table in display_rows}
    remote_columns.update(
        {
            "public_api_property_index": ["property_id", "country"],
            "public_api_property_packets": ["id"],
            "public_api_map_features": ["id"],
            "public_api_review_queue_rows": ["id"],
        }
    )

    compatible, omitted = compatible_remote_columns(
        display_rows=display_rows,
        public_api=public_api,
        remote_columns=remote_columns,
    )

    assert compatible["public_api_property_index"] == ["property_id", "country"]
    assert "feature_flags" in omitted["public_api_property_index"]


def test_country_delta_keeps_city_normalization_columns_when_remote_supports_them() -> None:
    display_rows = {
        table: [] for table in (
            "scan_runs",
            "properties",
            "property_aliases",
            "scan_candidates",
            "evidence_items",
            "scene_model_results",
            "build_statuses",
            "demand_estimates",
            "inference_records",
            "conclusions",
            "review_queue",
            "discovery_progress",
            "localized_text_cache",
            "network_performance_tiles",
        )
    }
    public_api = PublicApiPreaggregation(
        rows={
            "public_api_property_index": [
                {
                    "property_id": "property-1",
                    "country": "Algeria",
                    "city_id": "DZ:algiers",
                    "city_assignment": {"source_city": "Bab Ezzouar"},
                }
            ],
            "public_api_property_packets": [],
            "public_api_map_features": [],
            "public_api_review_queue_rows": [],
        }
    )
    remote_columns = {table: ["id"] for table in display_rows}
    remote_columns.update(
        {
            "public_api_property_index": [
                "property_id",
                "country",
                "city_id",
                "city_assignment",
            ],
            "public_api_property_packets": ["id", "city_id"],
            "public_api_map_features": ["id", "city_id"],
            "public_api_review_queue_rows": ["id", "city_id"],
        }
    )

    compatible, omitted = compatible_remote_columns(
        display_rows=display_rows,
        public_api=public_api,
        remote_columns=remote_columns,
    )

    assert compatible["public_api_property_index"] == [
        "property_id",
        "country",
        "city_id",
        "city_assignment",
    ]
    assert compatible["public_api_property_packets"] == ["id", "city_id"]
    assert "city_id" not in omitted.get("public_api_property_packets", [])


def test_additive_rollback_only_removes_target_country() -> None:
    rollback = build_additive_rollback_sql(["Armenia", "Zimbabwe"])

    assert "TRUNCATE" not in rollback.upper()
    assert "country IN ('Armenia', 'Zimbabwe')" in rollback
    assert "DELETE FROM public.properties" in rollback


def test_property_delta_sql_only_mutates_selected_property_ids_and_backs_up_rows() -> None:
    display_rows = {
        "scan_runs": [{"id": "run-1"}],
        "properties": [
            {
                "id": "property-1",
                "canonical_name": "Selected Tower",
                "country": "Algeria",
            }
        ],
        "property_aliases": [],
        "scan_candidates": [],
        "evidence_items": [],
        "scene_model_results": [],
        "build_statuses": [],
        "demand_estimates": [],
        "inference_records": [],
        "conclusions": [],
        "review_queue": [],
        "discovery_progress": [],
        "localized_text_cache": [],
        "network_performance_tiles": [],
    }
    public_api = PublicApiPreaggregation(
        rows={
            "public_api_property_index": [
                {"property_id": "property-1", "country": "Algeria"}
            ],
            "public_api_property_packets": [],
            "public_api_map_features": [],
            "public_api_review_queue_rows": [],
        }
    )
    columns = {
        table: list(rows[0]) if rows else ["id"]
        for table, rows in display_rows.items()
    }
    columns.update(
        {
            "public_api_property_index": ["property_id", "country"],
            "public_api_property_packets": ["id"],
            "public_api_map_features": ["id"],
            "public_api_review_queue_rows": ["id"],
        }
    )

    sql = build_delta_sql(
        countries=["Algeria"],
        display_rows=display_rows,
        public_api=public_api,
        compatible_columns=columns,
        expected_country_counts={"Algeria": 165},
        require_target_absent=False,
        property_ids=["property-1"],
        release_name="country_delta_20260810T030000Z",
    )

    assert "WHERE id IN ('property-1')" in sql
    assert "WHERE property_id IN ('property-1')" in sql
    assert "WHERE country IN ('Algeria')" not in sql
    assert 'CREATE SCHEMA IF NOT EXISTS "isite2_delta_backup"' in sql
    assert "country_delta_20260810t030000z__properties" in sql
    assert "non-target evidence_items rows changed" in sql
    assert re.search(r"(?im)^\s*TRUNCATE\b", sql) is None


def test_property_rollback_restores_pre_publish_backups() -> None:
    rollback = build_property_rollback_sql(
        ["property-1"],
        release_name="country_delta_20260810T030000Z",
    )

    assert "WHERE id IN ('property-1')" in rollback
    assert 'INSERT INTO public."properties" SELECT * FROM' in rollback
    assert (
        '"isite2_delta_backup".'
        '"country_delta_20260810t030000z__properties"' in rollback
    )
    assert "country IN" not in rollback
    assert "TRUNCATE" not in rollback.upper()


def test_property_delta_report_state_matches_remote_plus_selected_changes() -> None:
    hashes = validate_property_delta_report_state(
        countries=["Algeria"],
        selected_property_ids=["property-2"],
        selected_index_rows=[
            {
                "property_id": "property-2",
                "scan_run_id": "run-new",
                "country": "Algeria",
                "export_ready": True,
            }
        ],
        remote_state={
            "Algeria": {"property-1": "run-1", "property-2": "run-old"}
        },
        source_state={
            "Algeria": {"property-1": "run-1", "property-2": "run-new"}
        },
    )

    assert len(hashes["Algeria"]) == 64


def test_property_delta_report_state_blocks_local_remote_country_drift() -> None:
    try:
        validate_property_delta_report_state(
            countries=["Algeria"],
            selected_property_ids=["property-2"],
            selected_index_rows=[
                {
                    "property_id": "property-2",
                    "scan_run_id": "run-new",
                    "country": "Algeria",
                    "export_ready": True,
                }
            ],
            remote_state={"Algeria": {"property-1": "run-1"}},
            source_state={
                "Algeria": {
                    "property-1": "run-1",
                    "property-2": "run-new",
                    "unrelated-local-only": "run-3",
                }
            },
        )
    except RuntimeError as exc:
        assert "full-country report" in str(exc)
        assert "extra_in_local" in str(exc)
    else:
        raise AssertionError("country drift should block a property-scoped report publish")
