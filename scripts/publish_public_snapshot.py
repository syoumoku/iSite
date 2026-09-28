from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.growth.localization_refresh import (  # noqa: E402
    DEFAULT_LOCALIZATION_TEXT_KINDS,
    refresh_localization_cache_for_packets,
)
from isite2.public_api_preaggregation import (  # noqa: E402
    PUBLIC_API_TABLES,
    PublicApiPreaggregation,
    build_public_api_preaggregation,
)
from isite2.public_reports import (  # noqa: E402
    country_export_state_hash,
    generate_public_report_stage,
    public_report_activation_script,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository  # noqa: E402

DISPLAY_TABLES = [
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
]
PUBLIC_EMPTY_TABLES = [
    "source_cache",
    "raw_evidence_items",
    "candidate_drafts",
]
DEFAULT_RELEASE_ROOT = ROOT / "outputs" / "public_snapshots"
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SQLITE_URL_PREFIXES = ("sqlite+pysqlite:///", "sqlite:///")


@dataclass(frozen=True)
class SnapshotPaths:
    release_name: str
    release_dir: Path
    schema_gz: Path
    data_gz: Path
    manifest: Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a read-only iSite2 display snapshot to a VPS over SSH."
    )
    parser.add_argument(
        "--source-url",
        default=os.getenv("DATABASE_URL") or os.getenv("ISITE2_DATABASE_URL"),
        help="Local Postgres SQLAlchemy URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--ssh-target",
        default=os.getenv("ISITE2_PUBLIC_SSH_TARGET"),
        help="SSH target such as deploy@example.com. Required unless --dry-run is used.",
    )
    parser.add_argument(
        "--remote-dir",
        default=os.getenv("ISITE2_PUBLIC_REMOTE_DIR", "/opt/isite2"),
    )
    parser.add_argument("--remote-compose-file", default="docker-compose.prod.yml")
    parser.add_argument(
        "--remote-env-file",
        default=os.getenv("ISITE2_PUBLIC_REMOTE_ENV_FILE", "deploy/public.env"),
    )
    parser.add_argument("--db-service", default="postgres")
    parser.add_argument("--web-service", default="web")
    parser.add_argument("--db-user", default=os.getenv("ISITE2_PUBLIC_DB_USER", "isite"))
    parser.add_argument("--target-db", default=os.getenv("ISITE2_PUBLIC_DB", "isite2_public"))
    parser.add_argument(
        "--stage-db",
        default=os.getenv("ISITE2_PUBLIC_STAGE_DB", "isite2_public_stage"),
    )
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build snapshot and print remote steps only.",
    )
    parser.add_argument(
        "--skip-localization-refresh",
        action="store_true",
        default=os.getenv("ISITE2_SKIP_PREPUBLISH_LOCALIZATION_REFRESH", "").strip()
        in {"1", "true", "yes"},
        help="Skip pre-publish localization cache gap check and refresh.",
    )
    parser.add_argument(
        "--allow-localization-gaps",
        action="store_true",
        default=os.getenv("ISITE2_ALLOW_PREPUBLISH_LOCALIZATION_GAPS", "").strip()
        in {"1", "true", "yes"},
        help="Do not fail the publish if localization cache gaps remain after refresh.",
    )
    parser.add_argument(
        "--localization-provider",
        choices=["codex-oauth", "auto", "noop"],
        default=os.getenv("ISITE2_LOCALIZATION_PROVIDER", "codex-oauth"),
    )
    parser.add_argument(
        "--localization-locale",
        action="append",
        help="Locale to prewarm before publishing. Repeatable. Defaults to all supported locales.",
    )
    parser.add_argument(
        "--localization-batch-size",
        type=int,
        default=None,
        help="Maximum text candidates per localization batch.",
    )
    parser.add_argument(
        "--localization-limit",
        type=int,
        default=0,
        help="Maximum localization text candidates to process before publishing.",
    )
    args = parser.parse_args()

    if not args.source_url:
        raise SystemExit("DATABASE_URL or --source-url is required")
    if not args.dry_run and not args.ssh_target:
        raise SystemExit("ISITE2_PUBLIC_SSH_TARGET or --ssh-target is required")

    if not is_sqlite_dsn(args.source_url):
        _require_commands(["pg_dump"])
    localization_pre_publish = prepublish_localization_refresh(
        args.source_url,
        enabled=not args.skip_localization_refresh,
        provider_mode=args.localization_provider,
        locales=args.localization_locale,
        batch_size=args.localization_batch_size,
        limit=args.localization_limit,
        allow_gaps=args.allow_localization_gaps,
    )
    release_name = datetime.now(UTC).strftime("public_%Y%m%dT%H%M%SZ")
    release_dir = args.release_root / release_name
    release_dir.mkdir(parents=True, exist_ok=False)
    snapshot = build_snapshot(
        args.source_url,
        release_name,
        release_dir,
        localization_pre_publish=localization_pre_publish,
    )
    country_state_hashes = _country_report_state_hashes(args.source_url)
    report_patch = generate_public_report_stage(
        source_url=args.source_url,
        countries=sorted(country_state_hashes),
        country_state_hashes=country_state_hashes,
        release_dir=release_dir,
        timestamp=release_name,
    )
    manifest = _load_manifest(snapshot.manifest)
    manifest["country_report_state_hashes"] = country_state_hashes
    manifest["artifacts"]["country_reports"] = {
        "countries": sorted(report_patch["reports"]),
        "index_patch": "public_reports/index_patch.json",
    }
    snapshot.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    remote_script = build_remote_restore_script(
        remote_dir=args.remote_dir,
        remote_compose_file=args.remote_compose_file,
        remote_env_file=args.remote_env_file,
        db_service=args.db_service,
        web_service=args.web_service,
        db_user=args.db_user,
        target_db=args.target_db,
        stage_db=args.stage_db,
        release_name=release_name,
        expected_counts=_expected_restore_counts(_load_manifest(snapshot.manifest)),
    )
    print(f"Snapshot: {snapshot.release_dir}")
    print(f"Manifest: {snapshot.manifest}")

    if args.dry_run:
        print("\n--- remote restore script ---")
        print(remote_script)
        return 0

    publish_snapshot(args.ssh_target, args.remote_dir, snapshot, remote_script)
    print(f"Published {release_name} to {args.ssh_target}:{args.remote_dir}")
    return 0


def build_snapshot(
    source_url: str,
    release_name: str,
    release_dir: Path,
    *,
    localization_pre_publish: dict | None = None,
) -> SnapshotPaths:
    schema_sql = release_dir / "schema.sql"
    data_sql = release_dir / "data.sql"
    schema_gz = release_dir / "schema.sql.gz"
    data_gz = release_dir / "data.sql.gz"
    manifest_path = release_dir / "manifest.json"
    public_api = build_public_api_preaggregation(source_url)

    if is_sqlite_dsn(source_url):
        build_sqlite_snapshot_files(source_url, schema_sql, data_sql, public_api)
    else:
        dump_url = normalize_pg_dsn(source_url)
        _run(
            [
                "pg_dump",
                "--schema-only",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(schema_sql),
                dump_url,
            ]
        )
        data_command = [
            "pg_dump",
            "--data-only",
            "--no-owner",
            "--no-privileges",
            "--disable-triggers",
            "--file",
            str(data_sql),
        ]
        for table in DISPLAY_TABLES:
            data_command.extend(["--table", f"public.{table}"])
        data_command.append(dump_url)
        _run(data_command)
        _append_public_api_generated_schema(schema_sql)
        _append_public_api_generated_data(data_sql, public_api)

    _gzip_file(schema_sql, schema_gz)
    _gzip_file(data_sql, data_gz)
    schema_sql.unlink()
    data_sql.unlink()
    display_counts = load_table_counts(source_url, DISPLAY_TABLES)
    public_api_counts = public_api.table_counts

    manifest = {
        "release_name": release_name,
        "published_at": datetime.now(UTC).isoformat(),
        "source_url": _redact_dsn(source_url),
        "schema_version": "0.1",
        "display_tables": DISPLAY_TABLES,
        "public_api_tables": PUBLIC_API_TABLES,
        "public_empty_tables": PUBLIC_EMPTY_TABLES,
        "display_table_counts": display_counts,
        "public_api_table_counts": public_api_counts,
        "public_api_preaggregation": public_api.metadata,
        "localization_pre_publish": localization_pre_publish
        or {
            "mode": "pre_publish_localization_refresh",
            "status": "not_run",
        },
        "excluded_table_source_counts": load_table_counts(source_url, PUBLIC_EMPTY_TABLES),
        "artifacts": {
            "schema_sql_gz": {
                "file": schema_gz.name,
                "sha256": _sha256(schema_gz),
                "bytes": schema_gz.stat().st_size,
            },
            "data_sql_gz": {
                "file": data_gz.name,
                "sha256": _sha256(data_gz),
                "bytes": data_gz.stat().st_size,
            },
        },
        "restore_result": "not_started",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return SnapshotPaths(release_name, release_dir, schema_gz, data_gz, manifest_path)


def prepublish_localization_refresh(
    source_url: str,
    *,
    enabled: bool = True,
    provider_mode: str = "codex-oauth",
    locales: list[str] | None = None,
    batch_size: int | None = None,
    limit: int = 0,
    allow_gaps: bool = False,
) -> dict:
    """Check and prewarm UI/export localization cache before public API JSON is built."""
    if not enabled:
        return {
            "mode": "pre_publish_localization_refresh",
            "status": "skipped",
            "skipped_reason": "disabled_by_flag",
        }
    repository = SQLAlchemyScanRunRepository.from_url(
        source_url,
        storage_mode="postgis" if source_url.startswith("postgresql") else "sqlite",
        create_schema=False,
    )
    packets = repository.list_properties({})
    text_kinds = list(DEFAULT_LOCALIZATION_TEXT_KINDS)
    before = refresh_localization_cache_for_packets(
        packets,
        repository.engine,
        locales=locales,
        text_kinds=text_kinds,
        provider_mode=provider_mode,
        batch_size=batch_size,
        limit=limit,
        dry_run=True,
    )
    missing_before = _localization_missing_count(before)
    summary = {
        "mode": "pre_publish_localization_refresh",
        "status": "no_missing_localization",
        "provider": provider_mode,
        "property_count": len(packets),
        "text_kinds": text_kinds,
        "before": before,
        "refresh": None,
        "after": before,
    }
    if missing_before <= 0:
        print(
            "Pre-publish localization refresh: no missing localized text "
            f"for {len(packets)} properties."
        )
        return summary

    print(
        "Pre-publish localization refresh: "
        f"{missing_before} missing text cache entries; running provider={provider_mode}."
    )
    refresh = refresh_localization_cache_for_packets(
        packets,
        repository.engine,
        locales=locales,
        text_kinds=text_kinds,
        provider_mode=provider_mode,
        batch_size=batch_size,
        limit=limit,
        dry_run=False,
    )
    after = refresh_localization_cache_for_packets(
        packets,
        repository.engine,
        locales=locales,
        text_kinds=text_kinds,
        provider_mode=provider_mode,
        batch_size=batch_size,
        limit=limit,
        dry_run=True,
    )
    missing_after = _localization_missing_count(after)
    error_count = int(refresh.get("error_count") or 0)
    summary.update(
        {
            "status": "refreshed",
            "refresh": refresh,
            "after": after,
            "missing_before_count": missing_before,
            "missing_after_count": missing_after,
            "error_count": error_count,
        }
    )
    if (missing_after > 0 or error_count > 0) and not allow_gaps:
        summary["status"] = "failed_gaps_remaining"
        raise RuntimeError(
            "Pre-publish localization refresh failed: "
            f"missing_after={missing_after}, error_count={error_count}. "
            "Use --allow-localization-gaps only for an explicitly accepted degraded publish."
        )
    if missing_after > 0 or error_count > 0:
        summary["status"] = "refreshed_with_gaps_allowed"
    print(
        "Pre-publish localization refresh complete: "
        f"missing_before={missing_before}, missing_after={missing_after}, errors={error_count}."
    )
    return summary


def _localization_missing_count(summary: dict) -> int:
    counts = summary.get("counts") or {}
    return int(counts.get("dry_run_missing_count") or 0)


def publish_snapshot(
    ssh_target: str,
    remote_dir: str,
    snapshot: SnapshotPaths,
    remote_script: str,
) -> None:
    remote_release_dir = f"{remote_dir.rstrip('/')}/releases/{snapshot.release_name}"
    remote_report_root = f"{remote_dir.rstrip('/')}/public_reports"
    remote_report_incoming = f"{remote_report_root}/incoming/{snapshot.release_name}"
    _run(["ssh", ssh_target, "mkdir", "-p", remote_release_dir])
    _run(["ssh", ssh_target, "mkdir", "-p", remote_report_incoming])
    _run(
        [
            "scp",
            str(snapshot.schema_gz),
            str(snapshot.data_gz),
            str(snapshot.manifest),
            f"{ssh_target}:{remote_release_dir}/",
        ]
    )
    _run(
        [
            "scp",
            "-r",
            str(snapshot.release_dir / "public_reports" / "index_patch.json"),
            str(snapshot.release_dir / "public_reports" / "releases"),
            f"{ssh_target}:{remote_report_incoming}/",
        ]
    )
    _run(["ssh", ssh_target, "bash", "-se"], input_text=remote_script)
    _run(
        ["ssh", ssh_target, "bash", "-se"],
        input_text=public_report_activation_script(
            remote_report_root,
            remote_report_incoming,
        ),
    )


def build_sqlite_snapshot_files(
    source_url: str,
    schema_sql: Path,
    data_sql: Path,
    public_api: PublicApiPreaggregation,
) -> None:
    db_path = sqlite_path_from_dsn(source_url)
    if not db_path.exists():
        raise SystemExit(f"SQLite database does not exist: {db_path}")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        public_tables = DISPLAY_TABLES + PUBLIC_EMPTY_TABLES
        table_columns = {table: _sqlite_table_columns(connection, table) for table in public_tables}
        _write_sqlite_public_schema(schema_sql, table_columns)
        _write_sqlite_public_data(data_sql, connection, table_columns, public_api)
    finally:
        connection.close()


def _write_sqlite_public_schema(
    schema_sql: Path,
    table_columns: dict[str, list[sqlite3.Row]],
) -> None:
    with schema_sql.open("w", encoding="utf-8") as handle:
        handle.write("CREATE SCHEMA IF NOT EXISTS public;\n\n")
        for table, columns in table_columns.items():
            _require_identifier(table)
            definitions = []
            primary_key_columns = []
            for column in columns:
                name = str(column["name"])
                _require_identifier(name)
                definition = f"{_quote_identifier(name)} {_sqlite_type_to_postgres(column['type'])}"
                if int(column["notnull"]):
                    definition += " NOT NULL"
                definitions.append(definition)
                if int(column["pk"]):
                    primary_key_columns.append(name)
            if primary_key_columns:
                quoted = ", ".join(_quote_identifier(column) for column in primary_key_columns)
                definitions.append(f"PRIMARY KEY ({quoted})")
            handle.write(f"CREATE TABLE IF NOT EXISTS public.{_quote_identifier(table)} (\n")
            handle.write(",\n".join(f"  {definition}" for definition in definitions))
            handle.write("\n);\n\n")
        _write_public_api_generated_schema(handle)
        _write_public_indexes(handle, table_columns)
        _write_public_api_generated_indexes(handle)


def _write_public_indexes(handle, table_columns: dict[str, list[sqlite3.Row]]) -> None:
    index_specs = {
        "properties": [
            ("idx_public_properties_country_scene", ["country", "scene_type"]),
            (
                "idx_public_properties_identity",
                ["country", "scene_type", "property_identity_key"],
            ),
        ],
        "scan_candidates": [
            ("idx_public_scan_candidates_property", ["property_id"]),
            ("idx_public_scan_candidates_scan_run", ["scan_run_id"]),
            (
                "idx_public_scan_candidates_run_property_created",
                ["scan_run_id", "property_id", "created_at"],
            ),
            (
                "idx_public_scan_candidates_country_scene_status",
                ["country", "scene_type", "candidate_quality_status"],
            ),
        ],
        "evidence_items": [
            ("idx_public_evidence_property", ["property_id"]),
            ("idx_public_evidence_property_run", ["property_id", "scan_run_id"]),
        ],
        "scene_model_results": [
            ("idx_public_scene_property", ["property_id"]),
            ("idx_public_scene_property_run", ["property_id", "scan_run_id"]),
        ],
        "build_statuses": [
            ("idx_public_build_property", ["property_id"]),
            ("idx_public_build_property_run", ["property_id", "scan_run_id"]),
        ],
        "demand_estimates": [
            ("idx_public_demand_property", ["property_id"]),
            ("idx_public_demand_property_run", ["property_id", "scan_run_id"]),
        ],
        "inference_records": [
            ("idx_public_inference_property", ["property_id"]),
            ("idx_public_inference_property_run", ["property_id", "scan_run_id"]),
        ],
        "conclusions": [
            ("idx_public_conclusions_property", ["property_id"]),
            ("idx_public_conclusions_property_run", ["property_id", "scan_run_id"]),
        ],
        "review_queue": [
            ("idx_public_review_property", ["property_id"]),
            ("idx_public_review_status", ["status"]),
            ("idx_public_review_property_run", ["property_id", "scan_run_id"]),
            (
                "idx_public_review_status_property_run",
                ["status", "property_id", "scan_run_id"],
            ),
        ],
        "discovery_progress": [
            ("idx_public_discovery_progress_country_scene", ["country", "scene_type"]),
        ],
        "localized_text_cache": [
            (
                "idx_public_localized_text_cache_lookup",
                ["target_locale", "text_kind", "source_text_hash", "schema_version"],
            ),
        ],
        "network_performance_tiles": [
            (
                "idx_public_network_tiles_country_type_period",
                ["country", "service_type", "period"],
            ),
            (
                "idx_public_network_tiles_country_type_class",
                ["country", "service_type", "performance_class"],
            ),
        ],
    }
    available_columns = {
        table: {str(column["name"]) for column in columns}
        for table, columns in table_columns.items()
    }
    for table, specs in index_specs.items():
        if table not in table_columns:
            continue
        for index_name, columns in specs:
            if not set(columns).issubset(available_columns[table]):
                continue
            quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
            handle.write(
                f"CREATE INDEX IF NOT EXISTS {_quote_identifier(index_name)} "
                f"ON public.{_quote_identifier(table)} ({quoted_columns});\n"
            )
    handle.write("\n")


PUBLIC_API_GENERATED_SCHEMAS = {
    "public_api_property_index": [
        ("property_id", "TEXT", True),
        ("scan_run_id", "TEXT", True),
        ("property_name", "TEXT", True),
        ("aliases", "JSONB", True),
        ("search_text_normalized", "TEXT", True),
        ("country", "TEXT", True),
        ("city", "TEXT", True),
        ("city_id", "TEXT", False),
        ("city_assignment", "JSONB", False),
        ("scene_type", "TEXT", True),
        ("longitude", "DOUBLE PRECISION", False),
        ("latitude", "DOUBLE PRECISION", False),
        ("google_maps_link", "TEXT", False),
        ("geocode_precision", "TEXT", False),
        ("map_source", "TEXT", False),
        ("coordinate_status", "TEXT", False),
        ("evidence_status", "TEXT", True),
        ("value_class", "TEXT", True),
        ("action_class", "TEXT", True),
        ("recommended_solution", "TEXT", True),
        ("annual_visits_est", "DOUBLE PRECISION", False),
        ("annual_visits_p10", "DOUBLE PRECISION", False),
        ("annual_visits_p50", "DOUBLE PRECISION", False),
        ("annual_visits_p90", "DOUBLE PRECISION", False),
        ("traffic_model_version", "TEXT", False),
        ("proxy_level", "TEXT", True),
        ("busy_hour_traffic_gb", "DOUBLE PRECISION", False),
        ("complaint_pressure", "TEXT", False),
        ("network_validation_priority", "TEXT", False),
        ("network_data_freshness", "TEXT", False),
        ("feature_flags", "JSONB", True),
        ("indoor_system_presence", "TEXT", True),
        ("indoor_rat", "TEXT", True),
        ("candidate_quality_status", "TEXT", True),
        ("main_table_ready", "BOOLEAN", True),
        ("map_ready", "BOOLEAN", True),
        ("export_ready", "BOOLEAN", True),
        ("map_coordinate_ready", "BOOLEAN", True),
        ("has_review_issue", "BOOLEAN", True),
        ("review_count", "INTEGER", True),
        ("source_count", "INTEGER", True),
        ("source_urls", "JSONB", True),
        ("main_metric_text", "TEXT", True),
        ("visibility", "JSONB", True),
        ("quality_issues", "JSONB", True),
        ("sort_order", "INTEGER", True),
    ],
    "public_api_property_packets": [
        ("id", "TEXT", True),
        ("locale", "TEXT", True),
        ("property_id", "TEXT", True),
        ("scan_run_id", "TEXT", True),
        ("country", "TEXT", True),
        ("city", "TEXT", True),
        ("city_id", "TEXT", False),
        ("scene_type", "TEXT", True),
        ("evidence_status", "TEXT", True),
        ("value_class", "TEXT", True),
        ("action_class", "TEXT", True),
        ("recommended_solution", "TEXT", True),
        ("indoor_system_presence", "TEXT", True),
        ("indoor_rat", "TEXT", True),
        ("proxy_level", "TEXT", True),
        ("candidate_quality_status", "TEXT", True),
        ("main_table_ready", "BOOLEAN", True),
        ("map_ready", "BOOLEAN", True),
        ("export_ready", "BOOLEAN", True),
        ("has_review_issue", "BOOLEAN", True),
        ("sort_order", "INTEGER", True),
        ("packet_json", "JSONB", True),
    ],
    "public_api_map_features": [
        ("id", "TEXT", True),
        ("locale", "TEXT", True),
        ("property_id", "TEXT", True),
        ("scan_run_id", "TEXT", True),
        ("country", "TEXT", True),
        ("city", "TEXT", True),
        ("city_id", "TEXT", False),
        ("scene_type", "TEXT", True),
        ("evidence_status", "TEXT", True),
        ("value_class", "TEXT", True),
        ("action_class", "TEXT", True),
        ("recommended_solution", "TEXT", True),
        ("indoor_system_presence", "TEXT", True),
        ("indoor_rat", "TEXT", True),
        ("proxy_level", "TEXT", True),
        ("candidate_quality_status", "TEXT", True),
        ("map_ready", "BOOLEAN", True),
        ("map_coordinate_ready", "BOOLEAN", True),
        ("has_review_issue", "BOOLEAN", True),
        ("sort_order", "INTEGER", True),
        ("feature_json", "JSONB", True),
    ],
    "public_api_review_queue_rows": [
        ("id", "TEXT", True),
        ("review_id", "TEXT", True),
        ("locale", "TEXT", True),
        ("property_id", "TEXT", True),
        ("scan_run_id", "TEXT", True),
        ("country", "TEXT", True),
        ("city", "TEXT", True),
        ("city_id", "TEXT", False),
        ("scene_type", "TEXT", True),
        ("evidence_status", "TEXT", True),
        ("value_class", "TEXT", True),
        ("action_class", "TEXT", True),
        ("indoor_system_presence", "TEXT", True),
        ("candidate_quality_status", "TEXT", True),
        ("status", "TEXT", True),
        ("sort_order", "INTEGER", True),
        ("row_json", "JSONB", True),
    ],
}

PUBLIC_API_PRIMARY_KEYS = {
    "public_api_property_index": ["property_id"],
    "public_api_property_packets": ["id"],
    "public_api_map_features": ["id"],
    "public_api_review_queue_rows": ["id"],
}

PUBLIC_API_INDEXES = {
    "public_api_property_index": [
        ("idx_public_api_property_index_country_scene", ["country", "scene_type"]),
        ("idx_public_api_property_index_city_scene", ["country", "city", "scene_type"]),
        (
            "idx_public_api_property_index_city_id_scene",
            ["country", "city_id", "scene_type"],
        ),
        (
            "idx_public_api_property_index_status",
            ["candidate_quality_status", "main_table_ready", "map_ready"],
        ),
        ("idx_public_api_property_index_filters", ["evidence_status", "value_class"]),
        ("idx_public_api_property_index_search", ["search_text_normalized"]),
    ],
    "public_api_property_packets": [
        ("idx_public_api_property_packets_property", ["property_id"]),
        ("idx_public_api_property_packets_locale_property", ["locale", "property_id"]),
        (
            "idx_public_api_property_packets_locale_country_scene",
            ["locale", "country", "scene_type"],
        ),
        (
            "idx_public_api_property_packets_locale_city_scene",
            ["locale", "country", "city", "scene_type"],
        ),
        (
            "idx_public_api_property_packets_locale_city_id_scene",
            ["locale", "country", "city_id", "scene_type"],
        ),
        (
            "idx_public_api_property_packets_locale_status",
            ["locale", "candidate_quality_status", "main_table_ready"],
        ),
    ],
    "public_api_map_features": [
        ("idx_public_api_map_features_property", ["property_id"]),
        (
            "idx_public_api_map_features_locale_country_scene",
            ["locale", "country", "scene_type"],
        ),
        (
            "idx_public_api_map_features_locale_city_id_scene",
            ["locale", "country", "city_id", "scene_type"],
        ),
        (
            "idx_public_api_map_features_locale_status",
            ["locale", "candidate_quality_status", "map_ready", "map_coordinate_ready"],
        ),
    ],
    "public_api_review_queue_rows": [
        ("idx_public_api_review_rows_property", ["property_id"]),
        ("idx_public_api_review_rows_locale_status", ["locale", "status"]),
        (
            "idx_public_api_review_rows_locale_country_scene",
            ["locale", "country", "scene_type"],
        ),
        (
            "idx_public_api_review_rows_locale_city_id_scene",
            ["locale", "country", "city_id", "scene_type"],
        ),
        (
            "idx_public_api_review_rows_locale_filters",
            ["locale", "status", "country", "scene_type"],
        ),
    ],
}


def _append_public_api_generated_schema(schema_sql: Path) -> None:
    with schema_sql.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        _write_public_api_generated_schema(handle)
        _write_public_api_generated_indexes(handle)


def _write_public_api_generated_schema(handle) -> None:
    for table in PUBLIC_API_TABLES:
        definitions = []
        for name, column_type, not_null in PUBLIC_API_GENERATED_SCHEMAS[table]:
            definition = f"{_quote_identifier(name)} {column_type}"
            if not_null:
                definition += " NOT NULL"
            definitions.append(definition)
        primary_key = ", ".join(
            _quote_identifier(column) for column in PUBLIC_API_PRIMARY_KEYS[table]
        )
        definitions.append(f"PRIMARY KEY ({primary_key})")
        handle.write(f"CREATE TABLE IF NOT EXISTS public.{_quote_identifier(table)} (\n")
        handle.write(",\n".join(f"  {definition}" for definition in definitions))
        handle.write("\n);\n\n")


def _write_public_api_generated_indexes(handle) -> None:
    for table, specs in PUBLIC_API_INDEXES.items():
        for index_name, columns in specs:
            quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
            handle.write(
                f"CREATE INDEX IF NOT EXISTS {_quote_identifier(index_name)} "
                f"ON public.{_quote_identifier(table)} ({quoted_columns});\n"
            )
    handle.write("\n")


def _append_public_api_generated_data(
    data_sql: Path,
    public_api: PublicApiPreaggregation,
) -> None:
    with data_sql.open("a", encoding="utf-8") as handle:
        handle.write("\nBEGIN;\n")
        _write_public_api_generated_data(handle, public_api)
        handle.write("COMMIT;\n")


def _write_public_api_generated_data(handle, public_api: PublicApiPreaggregation) -> None:
    for table in PUBLIC_API_TABLES:
        columns = PUBLIC_API_GENERATED_SCHEMAS[table]
        column_names = [name for name, _column_type, _not_null in columns]
        quoted_columns = ", ".join(_quote_identifier(column) for column in column_names)
        for row in public_api.rows.get(table, []):
            values = ", ".join(
                _postgres_literal(_public_api_literal_value(row.get(name), column_type))
                for name, column_type, _not_null in columns
            )
            handle.write(
                f"INSERT INTO public.{_quote_identifier(table)} ({quoted_columns}) "
                f"VALUES ({values});\n"
            )


def _public_api_literal_value(value, column_type: str):
    if column_type == "JSONB":
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    return value


def _write_sqlite_public_data(
    data_sql: Path,
    connection: sqlite3.Connection,
    table_columns: dict[str, list[sqlite3.Row]],
    public_api: PublicApiPreaggregation,
) -> None:
    with data_sql.open("w", encoding="utf-8") as handle:
        handle.write("BEGIN;\n")
        for table in DISPLAY_TABLES:
            _require_identifier(table)
            column_names = [str(column["name"]) for column in table_columns[table]]
            quoted_columns = ", ".join(_quote_identifier(column) for column in column_names)
            select_columns = ", ".join(_quote_identifier(column) for column in column_names)
            cursor = connection.execute(
                f"SELECT {select_columns} FROM {_quote_identifier(table)}"  # noqa: S608
            )
            for row in cursor:
                values = ", ".join(_postgres_literal(row[column]) for column in column_names)
                handle.write(
                    f"INSERT INTO public.{_quote_identifier(table)} ({quoted_columns}) "
                    f"VALUES ({values});\n"
                )
        _write_public_api_generated_data(handle, public_api)
        handle.write("COMMIT;\n")


def _sqlite_table_columns(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    _require_identifier(table)
    columns = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    if not columns:
        raise SystemExit(f"Missing required source table: {table}")
    return columns


def _sqlite_type_to_postgres(sqlite_type: str | None) -> str:
    normalized = (sqlite_type or "").upper()
    if "JSON" in normalized:
        return "JSONB"
    if "INT" in normalized:
        return "INTEGER"
    if any(token in normalized for token in ["REAL", "FLOAT", "DOUBLE"]):
        return "DOUBLE PRECISION"
    if "NUMERIC" in normalized or "DECIMAL" in normalized:
        return "NUMERIC"
    if "BOOL" in normalized:
        return "BOOLEAN"
    if "DATE" in normalized or "TIME" in normalized:
        return "TIMESTAMPTZ"
    return "TEXT"


def _quote_identifier(value: str) -> str:
    _require_identifier(value)
    return f'"{value}"'


def _postgres_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else "NULL"
    text = str(value).replace("\x00", "")
    return "'" + text.replace("'", "''") + "'"


def build_remote_restore_script(
    *,
    remote_dir: str,
    remote_compose_file: str,
    remote_env_file: str | None = None,
    db_service: str,
    web_service: str,
    db_user: str,
    target_db: str,
    stage_db: str,
    release_name: str,
    expected_counts: dict[str, int],
) -> str:
    for value in [db_service, web_service, db_user, target_db, stage_db]:
        _require_identifier(value)
    release_dir = f"{remote_dir.rstrip('/')}/releases/{release_name}"
    previous_db = f"{target_db}_previous"
    _require_identifier(previous_db)

    count_checks = []
    for table, expected in expected_counts.items():
        _require_identifier(table)
        count_sql = shlex.quote(f"SELECT COUNT(*) FROM public.{table};")
        expected_count = shlex.quote(str(expected))
        count_checks.append(
            "\n".join(
                [
                    'actual=$("${COMPOSE[@]}" exec -T "$DB_SERVICE" '
                    f'psql -U "$DB_USER" -d "$STAGE_DB" -tAc {count_sql} '
                    "</dev/null)",
                    f'if [ "$actual" != {expected_count} ]; then',
                    f"  echo 'count mismatch for {table}: expected {expected}, "
                    'got \'"$actual" >&2',
                    "  exit 1",
                    "fi",
                ]
            )
        )

    psql_stage = (
        '"${COMPOSE[@]}" exec -T "$DB_SERVICE" psql -q -v ON_ERROR_STOP=1 '
        '-U "$DB_USER" -d "$STAGE_DB"'
    )
    psql_admin = (
        '"${COMPOSE[@]}" exec -T "$DB_SERVICE" psql -q -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres'
    )
    db_exists = (
        'db_exists() { "${COMPOSE[@]}" exec -T "$DB_SERVICE" psql '
        '-U "$DB_USER" -d postgres -tAc '
        "\"SELECT 1 FROM pg_database WHERE datname = '$1'\" </dev/null | grep -q 1; }"
    )
    terminate_connections = (
        f"{psql_admin} -c "
        '"SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
        "WHERE datname IN ('$TARGET_DB', '$STAGE_DB', '$PREVIOUS_DB') "
        'AND pid <> pg_backend_pid();" </dev/null'
    )
    rename_target_to_previous = (
        f'{psql_admin} -c "ALTER DATABASE $TARGET_DB RENAME TO $PREVIOUS_DB;" </dev/null'
    )
    rename_stage_to_target = (
        f'{psql_admin} -c "ALTER DATABASE $STAGE_DB RENAME TO $TARGET_DB;" </dev/null'
    )

    return "\n".join(
        [
            "set -euo pipefail",
            f"REMOTE_DIR={shlex.quote(remote_dir)}",
            f"COMPOSE_FILE={shlex.quote(remote_compose_file)}",
            f"ENV_FILE={shlex.quote(remote_env_file or '')}",
            f"RELEASE_DIR={shlex.quote(release_dir)}",
            f"DB_SERVICE={shlex.quote(db_service)}",
            f"WEB_SERVICE={shlex.quote(web_service)}",
            f"DB_USER={shlex.quote(db_user)}",
            f"TARGET_DB={shlex.quote(target_db)}",
            f"STAGE_DB={shlex.quote(stage_db)}",
            f"PREVIOUS_DB={shlex.quote(previous_db)}",
            'cd "$REMOTE_DIR"',
            "disk_used_percent=$(df -P \"$REMOTE_DIR\" | awk 'NR==2 {gsub(\"%\", \"\", $5); print $5}')",
            'if [ -z "$disk_used_percent" ] || [ "$disk_used_percent" -ge 85 ]; then',
            '  echo "publish blocked: remote disk usage is ${disk_used_percent:-unknown}% (limit 85%)" >&2',
            "  exit 1",
            "fi",
            'if [ -e /var/lib/isite2-disk-guard/block-heavy-jobs ]; then',
            '  echo "publish blocked: production disk guard has blocked heavy jobs" >&2',
            "  exit 1",
            "fi",
            "COMPOSE=(docker compose)",
            'if [ -n "$ENV_FILE" ]; then',
            '  COMPOSE+=(--env-file "$ENV_FILE")',
            "fi",
            'COMPOSE+=(-f "$COMPOSE_FILE")',
            '"${COMPOSE[@]}" up -d "$DB_SERVICE"',
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" dropdb --if-exists '
            '-U "$DB_USER" "$STAGE_DB" </dev/null',
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" createdb -U "$DB_USER" "$STAGE_DB" </dev/null',
            f'gzip -dc "$RELEASE_DIR/schema.sql.gz" | {psql_stage}',
            f'gzip -dc "$RELEASE_DIR/data.sql.gz" | {psql_stage}',
            *count_checks,
            db_exists,
            '"${COMPOSE[@]}" stop "$WEB_SERVICE" || true',
            terminate_connections,
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" dropdb --if-exists '
            '-U "$DB_USER" "$PREVIOUS_DB" </dev/null',
            'if db_exists "$TARGET_DB"; then',
            f"  {rename_target_to_previous}",
            "fi",
            rename_stage_to_target,
            '"${COMPOSE[@]}" up -d "$WEB_SERVICE"',
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" dropdb --if-exists '
            '-U "$DB_USER" "$PREVIOUS_DB" </dev/null || true',
            "python3 - <<'PY' \"$RELEASE_DIR/manifest.json\"",
            "import json, sys",
            "from datetime import datetime, timezone",
            "path = sys.argv[1]",
            "with open(path, 'r', encoding='utf-8') as handle:",
            "    manifest = json.load(handle)",
            "manifest['restore_result'] = 'completed'",
            "manifest['restored_at'] = datetime.now(timezone.utc).isoformat()",
            "with open(path, 'w', encoding='utf-8') as handle:",
            "    json.dump(manifest, handle, ensure_ascii=False, indent=2)",
            "    handle.write('\\n')",
            "PY",
        ]
    )


def normalize_pg_dsn(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg://")
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg2://")
    return url


def is_sqlite_dsn(url: str) -> bool:
    return url.startswith(SQLITE_URL_PREFIXES)


def sqlite_path_from_dsn(url: str) -> Path:
    for prefix in SQLITE_URL_PREFIXES:
        if url.startswith(prefix):
            raw_path = unquote(url.removeprefix(prefix).split("?", 1)[0])
            if raw_path == ":memory:":
                raise SystemExit("In-memory SQLite databases cannot be published")
            path = Path(raw_path)
            return path if path.is_absolute() else ROOT / path
    raise ValueError(f"not a SQLite URL: {url!r}")


def load_table_counts(source_url: str, tables: list[str]) -> dict[str, int]:
    from sqlalchemy import create_engine, text

    engine = create_engine(source_url, future=True)
    with engine.connect() as connection:
        return {
            table: int(connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
            for table in tables
        }


def _gzip_file(source: Path, target: Path) -> None:
    with source.open("rb") as source_handle, gzip.open(target, "wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact_dsn(url: str) -> str:
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url)


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_restore_counts(manifest: dict) -> dict[str, int]:
    counts = dict(manifest.get("display_table_counts") or {})
    counts.update(manifest.get("public_api_table_counts") or {})
    return counts


def _country_report_state_hashes(source_url: str) -> dict[str, str]:
    repository = SQLAlchemyScanRunRepository.from_url(
        source_url,
        storage_mode="postgis" if source_url.startswith("postgresql") else "sqlite",
        create_schema=False,
    )
    rows = repository.latest_candidate_summary_rows(include_blocked_quality=True)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        export_ready = bool((row.get("visibility") or {}).get("export_ready"))
        if not export_ready:
            continue
        grouped.setdefault(str(row["country"]), []).append(
            {
                "property_id": row["property_id"],
                "scan_run_id": row["scan_run_id"],
                "export_ready": True,
            }
        )
    return {
        country: country_export_state_hash(country_rows)
        for country, country_rows in grouped.items()
    }


def _require_commands(commands: list[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise SystemExit(f"Missing required command(s): {', '.join(missing)}")


def _require_identifier(value: str) -> None:
    if not IDENTIFIER_RE.match(value):
        raise ValueError(f"unsafe identifier: {value!r}")


def _run(command: list[str], input_text: str | None = None) -> None:
    subprocess.run(command, input=input_text, text=True, check=True)  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
