from scripts.publish_public_snapshot import (
    DISPLAY_TABLES,
    PUBLIC_EMPTY_TABLES,
    PUBLIC_API_GENERATED_SCHEMAS,
    PUBLIC_API_TABLES,
    build_remote_restore_script,
    build_snapshot,
    is_sqlite_dsn,
    normalize_pg_dsn,
    prepublish_localization_refresh,
    sqlite_path_from_dsn,
)
from isite2.public_api_preaggregation import (
    _map_feature,
    _pending_text_count,
    _property_index_row,
)


def test_normalize_pg_dsn_for_pg_dump() -> None:
    assert (
        normalize_pg_dsn("postgresql+psycopg://isite:pw@localhost:5432/isite2")
        == "postgresql://isite:pw@localhost:5432/isite2"
    )
    assert (
        normalize_pg_dsn("postgresql+psycopg2://isite:pw@localhost:5432/isite2")
        == "postgresql://isite:pw@localhost:5432/isite2"
    )


def test_public_snapshot_table_policy_keeps_backstage_tables_empty() -> None:
    assert "properties" in DISPLAY_TABLES
    assert "evidence_items" in DISPLAY_TABLES
    assert "localized_text_cache" in DISPLAY_TABLES
    assert "network_performance_tiles" in DISPLAY_TABLES
    assert "public_api_property_index" in PUBLIC_API_TABLES
    assert "public_api_property_packets" in PUBLIC_API_TABLES
    assert "public_api_map_features" in PUBLIC_API_TABLES
    assert "public_api_review_queue_rows" in PUBLIC_API_TABLES
    assert "source_cache" in PUBLIC_EMPTY_TABLES
    assert "raw_evidence_items" in PUBLIC_EMPTY_TABLES
    assert "candidate_drafts" in PUBLIC_EMPTY_TABLES
    assert set(DISPLAY_TABLES).isdisjoint(PUBLIC_EMPTY_TABLES)
    assert set(PUBLIC_API_TABLES).isdisjoint(DISPLAY_TABLES)
    assert set(PUBLIC_API_TABLES).isdisjoint(PUBLIC_EMPTY_TABLES)


def test_public_api_preaggregation_counts_pending_text_strings() -> None:
    payload = {
        "localized": {
            "reason": "本地化待刷新",
            "next_action": "Localization pending",
            "safe": "ready",
        }
    }

    assert _pending_text_count(payload) == 2


def test_public_api_preaggregation_blocks_year_like_annual_visits() -> None:
    row = {
        "property_id": "property-1",
        "scan_run_id": "run-1",
        "property_name": "Airport",
        "country": "Brazil",
        "city": "São Paulo",
        "scene_type": "airport_terminal",
        "longitude": -46.4731,
        "latitude": -23.4356,
        "google_maps_link": "https://maps.example/airport",
        "geocode_precision": "airport centroid",
        "map_source": "test",
        "coordinate_status": "Verified",
        "evidence_status": "Supported",
        "value_class": "City Core",
        "action_class": "Survey First",
        "recommended_solution": "pRRU",
        "annual_visits_est": 2025,
        "proxy_level": "P0 Direct",
        "busy_hour_traffic_gb": 42.0,
        "indoor_system_presence": "Unknown",
        "indoor_rat": "Unknown",
        "candidate_quality_status": "ready",
        "review_count": 0,
        "source_count": 2,
        "source_urls": ["https://example.org/traffic"],
        "main_metric_text": "2025 annual passenger traffic: 47.2 million passengers",
        "visibility": {"main_table_ready": True, "map_ready": True, "export_ready": True},
        "quality_issues": [],
        "hero_image": {},
    }

    index_row = _property_index_row(row, 0)
    feature = _map_feature(row, "en", "2026-06-01T00:00:00+00:00")

    assert index_row["annual_visits_est"] is None
    assert index_row["busy_hour_traffic_gb"] is None
    assert feature["properties"]["annual_visits_est"] is None
    assert feature["properties"]["busy_hour_traffic_gb"] is None


def test_public_api_property_index_includes_alias_search_document() -> None:
    row = {
        "property_id": "property-1",
        "scan_run_id": "run-1",
        "property_name": "Houari Boumediene International Airport",
        "aliases": ["Aéroport d'Alger", "مطار الجزائر"],
        "country": "Algeria",
        "city": "Algiers",
        "city_id": "DZ:algiers",
        "city_assignment": {
            "city_id": "DZ:algiers",
            "source_city": "Bab Ezzouar",
            "locality": "Bab Ezzouar",
            "mapping_status": "verified",
        },
        "scene_type": "airport_terminal",
        "evidence_status": "Supported",
        "value_class": "City Core",
        "action_class": "Survey First",
        "recommended_solution": "Survey First",
        "proxy_level": "P0 Direct",
        "indoor_system_presence": "Unknown",
        "indoor_rat": "Unknown",
        "candidate_quality_status": "ready",
        "visibility": {
            "main_table_ready": True,
            "map_ready": True,
            "export_ready": True,
        },
    }

    index_row = _property_index_row(row, 0)

    assert index_row["aliases"] == ["Aéroport d'Alger", "مطار الجزائر"]
    assert "aeroport d alger" in index_row["search_text_normalized"]
    assert "مطار الجزائر" in index_row["search_text_normalized"]
    assert index_row["city_id"] == "DZ:algiers"
    assert index_row["city_assignment"]["source_city"] == "Bab Ezzouar"


def test_public_api_generated_schema_preserves_city_normalization_fields() -> None:
    schemas = PUBLIC_API_GENERATED_SCHEMAS

    index_columns = {name for name, _type, _required in schemas["public_api_property_index"]}
    assert {"city_id", "city_assignment"}.issubset(index_columns)
    for table in (
        "public_api_property_packets",
        "public_api_map_features",
        "public_api_review_queue_rows",
    ):
        columns = {name for name, _type, _required in schemas[table]}
        assert "city_id" in columns


def test_sqlite_snapshot_builds_postgres_restore_artifacts(tmp_path) -> None:
    import gzip
    import json
    import sqlite3

    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    try:
        for table in DISPLAY_TABLES + PUBLIC_EMPTY_TABLES:
            connection.execute(
                f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, payload JSON, created_at DATETIME)'
            )
        connection.execute('ALTER TABLE "evidence_items" ADD COLUMN property_id TEXT')
        connection.execute('ALTER TABLE "evidence_items" ADD COLUMN scan_run_id TEXT')
        connection.execute('ALTER TABLE "scan_candidates" ADD COLUMN property_id TEXT')
        connection.execute('ALTER TABLE "scan_candidates" ADD COLUMN scan_run_id TEXT')
        connection.execute('ALTER TABLE "scan_candidates" ADD COLUMN country TEXT')
        connection.execute('ALTER TABLE "scan_candidates" ADD COLUMN scene_type TEXT')
        connection.execute('ALTER TABLE "scan_candidates" ADD COLUMN candidate_quality_status TEXT')
        connection.execute('ALTER TABLE "review_queue" ADD COLUMN property_id TEXT')
        connection.execute('ALTER TABLE "review_queue" ADD COLUMN scan_run_id TEXT')
        connection.execute('ALTER TABLE "review_queue" ADD COLUMN status TEXT')
        connection.execute('ALTER TABLE "localized_text_cache" ADD COLUMN target_locale TEXT')
        connection.execute('ALTER TABLE "localized_text_cache" ADD COLUMN text_kind TEXT')
        connection.execute('ALTER TABLE "localized_text_cache" ADD COLUMN source_text_hash TEXT')
        connection.execute('ALTER TABLE "localized_text_cache" ADD COLUMN schema_version TEXT')
        connection.execute(
            'INSERT INTO "localized_text_cache" '
            "(id, target_locale, text_kind, source_text_hash, schema_version, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "loc-1",
                "zh",
                "reason_to_recommend",
                "hash-1",
                "localization.v1",
                '{"translated": true}',
            ),
        )
        connection.execute(
            'INSERT INTO "properties" (id, payload, created_at) VALUES (?, ?, ?)',
            ("property-1", '{"visible": true}', "2026-06-01T02:30:00+00:00"),
        )
        connection.execute(
            'INSERT INTO "network_performance_tiles" (id, payload, created_at) '
            "VALUES (?, ?, ?)",
            (
                "network-tile-1",
                '{"country": "Germany", "service_type": "mobile"}',
                "2026-08-14T07:30:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    release_dir = tmp_path / "release"
    release_dir.mkdir()
    snapshot = build_snapshot(
        f"sqlite+pysqlite:///{source}",
        "public_20260601T023000Z",
        release_dir,
    )

    manifest = json.loads(snapshot.manifest.read_text(encoding="utf-8"))
    assert manifest["display_table_counts"]["properties"] == 1
    assert manifest["display_table_counts"]["network_performance_tiles"] == 1
    assert manifest["public_api_table_counts"]["public_api_property_index"] == 0
    assert manifest["public_api_preaggregation"]["status"] == "skipped_missing_source_columns"
    assert manifest["excluded_table_source_counts"]["source_cache"] == 0
    with gzip.open(snapshot.schema_gz, "rt", encoding="utf-8") as handle:
        schema_sql = handle.read()
    with gzip.open(snapshot.data_gz, "rt", encoding="utf-8") as handle:
        data_sql = handle.read()
    assert 'CREATE TABLE IF NOT EXISTS public."properties"' in schema_sql
    assert 'CREATE TABLE IF NOT EXISTS public."network_performance_tiles"' in schema_sql
    assert 'CREATE TABLE IF NOT EXISTS public."localized_text_cache"' in schema_sql
    assert 'CREATE TABLE IF NOT EXISTS public."public_api_property_index"' in schema_sql
    assert 'CREATE TABLE IF NOT EXISTS public."public_api_property_packets"' in schema_sql
    assert 'CREATE TABLE IF NOT EXISTS public."public_api_map_features"' in schema_sql
    assert 'CREATE TABLE IF NOT EXISTS public."public_api_review_queue_rows"' in schema_sql
    assert '"payload" JSONB' in schema_sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_public_evidence_property"' in schema_sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_public_evidence_property_run"' in schema_sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_public_review_status_property_run"' in schema_sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_public_scan_candidates_run_property_created"' in schema_sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_public_localized_text_cache_lookup"' in schema_sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_public_api_map_features_locale_status"' in schema_sql
    assert 'INSERT INTO public."properties"' in data_sql
    assert 'INSERT INTO public."network_performance_tiles"' in data_sql
    assert 'INSERT INTO public."localized_text_cache"' in data_sql
    assert "'{''visible'': true}'" not in data_sql
    assert "'{\"visible\": true}'" in data_sql


def test_prepublish_localization_refresh_checks_then_fills_missing(monkeypatch) -> None:
    import scripts.publish_public_snapshot as publish_public_snapshot

    calls = []

    class FakeRepository:
        engine = object()

        def list_properties(self, _filters):
            return ["packet-1", "packet-2"]

    def fake_from_url(*_args, **_kwargs):
        return FakeRepository()

    def fake_refresh(packets, engine, **kwargs):
        calls.append(
            {
                "packets": list(packets),
                "engine": engine,
                "dry_run": kwargs["dry_run"],
                "provider_mode": kwargs["provider_mode"],
                "text_kinds": kwargs["text_kinds"],
            }
        )
        if len(calls) == 1:
            return {
                "counts": {"dry_run_missing_count": 3},
                "missing_count_by_text_kind": {
                    "reason_to_recommend": 1,
                    "primary_metric.display_text": 2,
                },
                "error_count": 0,
            }
        if len(calls) == 2:
            return {"counts": {"translated_count": 3}, "error_count": 0}
        return {"counts": {"dry_run_missing_count": 0}, "error_count": 0}

    monkeypatch.setattr(
        publish_public_snapshot.SQLAlchemyScanRunRepository,
        "from_url",
        staticmethod(fake_from_url),
    )
    monkeypatch.setattr(
        publish_public_snapshot,
        "refresh_localization_cache_for_packets",
        fake_refresh,
    )

    summary = prepublish_localization_refresh(
        "sqlite+pysqlite:///outputs/isite2_dev.db",
        provider_mode="codex-oauth",
    )

    assert [call["dry_run"] for call in calls] == [True, False, True]
    assert calls[0]["provider_mode"] == "codex-oauth"
    assert "reason_to_recommend" in calls[0]["text_kinds"]
    assert "primary_metric.display_text" in calls[0]["text_kinds"]
    assert summary["status"] == "refreshed"
    assert summary["missing_before_count"] == 3
    assert summary["missing_after_count"] == 0


def test_prepublish_localization_refresh_blocks_remaining_gaps(monkeypatch) -> None:
    import pytest
    import scripts.publish_public_snapshot as publish_public_snapshot

    class FakeRepository:
        engine = object()

        def list_properties(self, _filters):
            return ["packet-1"]

    monkeypatch.setattr(
        publish_public_snapshot.SQLAlchemyScanRunRepository,
        "from_url",
        staticmethod(lambda *_args, **_kwargs: FakeRepository()),
    )
    monkeypatch.setattr(
        publish_public_snapshot,
        "refresh_localization_cache_for_packets",
        lambda *_args, **_kwargs: {
            "counts": {"dry_run_missing_count": 1},
            "error_count": 0,
        },
    )

    with pytest.raises(RuntimeError, match="Pre-publish localization refresh failed"):
        prepublish_localization_refresh("sqlite+pysqlite:///outputs/isite2_dev.db")


def test_sqlite_dsn_helpers_resolve_project_relative_paths() -> None:
    assert is_sqlite_dsn("sqlite+pysqlite:///outputs/isite2_dev.db")
    assert str(sqlite_path_from_dsn("sqlite:///outputs/isite2_dev.db")).endswith(
        "outputs/isite2_dev.db"
    )


def test_remote_restore_script_restores_stage_before_swapping_public_db() -> None:
    script = build_remote_restore_script(
        remote_dir="/opt/isite2",
        remote_compose_file="docker-compose.prod.yml",
        remote_env_file="deploy/public.env",
        db_service="postgres",
        web_service="web",
        db_user="isite",
        target_db="isite2_public",
        stage_db="isite2_public_stage",
        release_name="public_20260520T023000Z",
        expected_counts={"properties": 4, "evidence_items": 12},
    )

    assert 'gzip -dc "$RELEASE_DIR/schema.sql.gz"' in script
    assert 'disk_used_percent=$(df -P "$REMOTE_DIR"' in script
    assert "publish blocked: remote disk usage" in script
    assert "/var/lib/isite2-disk-guard/block-heavy-jobs" in script
    assert 'COMPOSE+=(--env-file "$ENV_FILE")' in script
    assert "ENV_FILE=deploy/public.env" in script
    assert 'dropdb --if-exists -U "$DB_USER" "$STAGE_DB" </dev/null' in script
    assert "SELECT COUNT(*) FROM public.properties;' </dev/null)" in script
    assert 'gzip -dc "$RELEASE_DIR/data.sql.gz"' in script
    assert "SELECT COUNT(*) FROM public.properties;" in script
    assert "ALTER DATABASE $STAGE_DB RENAME TO $TARGET_DB;" in script
    assert script.index("SELECT COUNT(*) FROM public.properties;") < script.index(
        "ALTER DATABASE $STAGE_DB RENAME TO $TARGET_DB;"
    )
    assert '"${COMPOSE[@]}" up -d "$WEB_SERVICE"' in script
