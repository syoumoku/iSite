from scripts.publish_public_snapshot import (
    DISPLAY_TABLES,
    PUBLIC_EMPTY_TABLES,
    build_remote_restore_script,
    normalize_pg_dsn,
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
    assert "source_cache" in PUBLIC_EMPTY_TABLES
    assert "raw_evidence_items" in PUBLIC_EMPTY_TABLES
    assert "candidate_drafts" in PUBLIC_EMPTY_TABLES
    assert set(DISPLAY_TABLES).isdisjoint(PUBLIC_EMPTY_TABLES)


def test_remote_restore_script_restores_stage_before_swapping_public_db() -> None:
    script = build_remote_restore_script(
        remote_dir="/opt/isite2",
        remote_compose_file="docker-compose.prod.yml",
        db_service="postgres",
        web_service="web",
        db_user="isite",
        target_db="isite2_public",
        stage_db="isite2_public_stage",
        release_name="public_20260520T023000Z",
        expected_counts={"properties": 4, "evidence_items": 12},
    )

    assert "gzip -dc \"$RELEASE_DIR/schema.sql.gz\"" in script
    assert "gzip -dc \"$RELEASE_DIR/data.sql.gz\"" in script
    assert "SELECT COUNT(*) FROM public.properties;" in script
    assert "ALTER DATABASE $STAGE_DB RENAME TO $TARGET_DB;" in script
    assert script.index("SELECT COUNT(*) FROM public.properties;") < script.index(
        "ALTER DATABASE $STAGE_DB RENAME TO $TARGET_DB;"
    )
    assert '"${COMPOSE[@]}" up -d "$WEB_SERVICE"' in script
