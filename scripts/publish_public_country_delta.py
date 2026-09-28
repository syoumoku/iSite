#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.publish_public_snapshot import (  # noqa: E402
    DISPLAY_TABLES,
    PUBLIC_API_GENERATED_SCHEMAS,
    PUBLIC_API_PRIMARY_KEYS,
    _postgres_literal,
    _public_api_literal_value,
    _quote_identifier,
    _sha256,
    _sqlite_table_columns,
    is_sqlite_dsn,
    sqlite_path_from_dsn,
)

from isite2.growth.localization_refresh import (  # noqa: E402
    DEFAULT_LOCALIZATION_TEXT_KINDS,
    refresh_localization_cache_for_packets,
    text_candidates_from_packets,
)
from isite2.localization import (  # noqa: E402
    detect_source_locale,
    localization_schema_version,
    resolve_locale,
    source_text_hash,
    supported_locales,
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

DEFAULT_RELEASE_ROOT = ROOT / "outputs" / "public_deltas"
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PENDING_TEXT = ("Localization pending", "本地化待刷新")
TRUNCATE_STATEMENT_RE = re.compile(r"(?im)^\s*TRUNCATE\b")
PROPERTY_BACKUP_SCHEMA = "isite2_delta_backup"
PROPERTY_CHILD_TABLES = [
    "property_aliases",
    "scan_candidates",
    "evidence_items",
    "scene_model_results",
    "build_statuses",
    "demand_estimates",
    "inference_records",
    "conclusions",
    "review_queue",
]

@dataclass(frozen=True)
class DeltaArtifacts:
    release_name: str
    release_dir: Path
    sql_path: Path
    rollback_path: Path
    manifest_path: Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Publish an additive, country-scoped public data delta without rebuilding "
            "or replacing the complete public database."
        )
    )
    parser.add_argument(
        "--source-url",
        default=os.getenv("DATABASE_URL") or os.getenv("ISITE2_DATABASE_URL"),
    )
    parser.add_argument("--country", action="append", required=True)
    parser.add_argument(
        "--ssh-target",
        default=os.getenv("ISITE2_PUBLIC_SSH_TARGET"),
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
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument(
        "--allow-existing-target",
        action="store_true",
        help=(
            "Allow replacing countries already present remotely. The generated rollback "
            "only removes the published target rows, so this is disabled by default."
        ),
    )
    parser.add_argument(
        "--property-id",
        action="append",
        dest="property_ids",
        help=(
            "Limit the delta to one property id. Repeatable. Existing remote rows are "
            "backed up for an exact property-scoped rollback."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    countries = normalize_countries(args.country)
    property_ids = normalize_property_ids(args.property_ids)
    if not args.source_url or not is_sqlite_dsn(args.source_url):
        raise SystemExit("A SQLite DATABASE_URL or --source-url is required")
    if not args.ssh_target:
        raise SystemExit("ISITE2_PUBLIC_SSH_TARGET or --ssh-target is required")
    _require_identifiers(args.db_service, args.web_service, args.db_user, args.target_db)

    release_name = datetime.now(UTC).strftime("country_delta_%Y%m%dT%H%M%SZ")
    release_dir = args.release_root / release_name
    release_dir.mkdir(parents=True, exist_ok=False)
    artifacts = DeltaArtifacts(
        release_name=release_name,
        release_dir=release_dir,
        sql_path=release_dir / "country_delta.sql",
        rollback_path=release_dir / "country_delta_rollback.sql",
        manifest_path=release_dir / "manifest.json",
    )

    remote = inspect_remote(
        ssh_target=args.ssh_target,
        remote_dir=args.remote_dir,
        compose_file=args.remote_compose_file,
        env_file=args.remote_env_file,
        db_service=args.db_service,
        db_user=args.db_user,
        target_db=args.target_db,
        countries=countries,
        property_ids=property_ids,
    )
    if remote["disk_used_percent"] >= 85:
        raise RuntimeError(
            f"Remote disk gate blocked publish at {remote['disk_used_percent']}% used"
        )
    if (
        not property_ids
        and remote["target_property_count"]
        and not args.allow_existing_target
    ):
        raise RuntimeError(
            "Target countries already exist remotely; refusing additive delta without "
            "--allow-existing-target"
        )

    with tempfile.TemporaryDirectory(
        prefix="isite2-country-delta-",
        dir=ROOT / ".tmp",
    ) as temporary_dir:
        target_db_path = Path(temporary_dir) / "target.db"
        target_url = f"sqlite+pysqlite:///{target_db_path}"
        scope = build_target_database(
            args.source_url,
            countries=countries,
            target_path=target_db_path,
            property_ids=property_ids,
        )
        city_assignment_gate = strict_city_assignment_gate(
            target_db_path,
            expected_property_count=len(scope["property_ids"]),
        )
        localization_gate, packets = strict_localization_gate(target_url)
        public_api = build_public_api_preaggregation(target_url)
        validate_public_preaggregation(
            public_api,
            expected_property_count=len(scope["property_ids"]),
        )
        display_rows = load_scoped_display_rows(
            target_db_path,
            packets=packets,
        )

    compatible_columns, omitted_columns = compatible_remote_columns(
        display_rows=display_rows,
        public_api=public_api,
        remote_columns=remote["columns"],
    )
    expected_counts = {
        table: len(rows) for table, rows in display_rows.items()
    }
    expected_counts.update(public_api.table_counts)
    scoped_country_counts = _country_counts(public_api)
    source_report_state = _country_report_state(args.source_url, countries)
    if property_ids:
        remote_selected = remote["selected_properties"]
        for property_id, remote_country in remote_selected.items():
            local_country = scope["property_countries"].get(property_id)
            if local_country != remote_country:
                raise RuntimeError(
                    "Remote/local country mismatch for selected property "
                    f"{property_id}: remote={remote_country}, local={local_country}"
                )
        expected_country_counts = dict(remote["target_country_counts"])
        for country, _count in scoped_country_counts.items():
            new_count = sum(
                1
                for property_id, property_country in scope["property_countries"].items()
                if property_country == country and property_id not in remote_selected
            )
            expected_country_counts[country] = (
                expected_country_counts.get(country, 0) + new_count
            )
        country_state_hashes = validate_property_delta_report_state(
            countries=countries,
            selected_property_ids=property_ids,
            selected_index_rows=public_api.rows.get("public_api_property_index", []),
            remote_state=remote["target_export_state"],
            source_state=source_report_state,
        )
    else:
        expected_country_counts = scoped_country_counts
        country_state_hashes = _state_hashes(source_report_state)
    sql = build_delta_sql(
        countries=countries,
        display_rows=display_rows,
        public_api=public_api,
        compatible_columns=compatible_columns,
        expected_country_counts=expected_country_counts,
        require_target_absent=not args.allow_existing_target,
        property_ids=property_ids,
        release_name=release_name,
    )
    rollback_sql = (
        build_property_rollback_sql(property_ids, release_name=release_name)
        if property_ids
        else build_additive_rollback_sql(countries)
    )
    artifacts.sql_path.write_text(sql, encoding="utf-8")
    artifacts.rollback_path.write_text(rollback_sql, encoding="utf-8")

    report_patch = generate_public_report_stage(
        source_url=args.source_url,
        countries=countries,
        country_state_hashes=country_state_hashes,
        release_dir=release_dir,
        timestamp=release_name,
    )
    manifest = {
        "mode": "audited_country_scoped_delta_publish",
        "release_name": release_name,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "countries": countries,
        "delta_scope": "property_ids" if property_ids else "countries",
        "requested_property_ids": property_ids,
        "source_url": _redact_source_url(args.source_url),
        "property_ids": scope["property_ids"],
        "scan_run_ids": scope["scan_run_ids"],
        "row_counts": expected_counts,
        "country_property_counts": expected_country_counts,
        "country_report_state_hashes": country_state_hashes,
        "city_assignment_gate": city_assignment_gate,
        "localization_gate": localization_gate,
        "public_api_metadata": public_api.metadata,
        "remote_preflight": {
            "country_count": remote["country_count"],
            "target_property_count": remote["target_property_count"],
            "selected_property_count": len(remote["selected_properties"]),
            "target_country_counts": remote["target_country_counts"],
            "disk_used_percent": remote["disk_used_percent"],
            "omitted_local_columns": omitted_columns,
        },
        "artifacts": {
            "sql": {
                "file": artifacts.sql_path.name,
                "sha256": _sha256(artifacts.sql_path),
                "bytes": artifacts.sql_path.stat().st_size,
            },
            "rollback": {
                "file": artifacts.rollback_path.name,
                "sha256": _sha256(artifacts.rollback_path),
                "bytes": artifacts.rollback_path.stat().st_size,
            },
            "country_reports": {
                "countries": sorted(report_patch["reports"]),
                "index_patch": "public_reports/index_patch.json",
            },
        },
        "publish_result": "dry_run" if args.dry_run else "not_started",
    }
    _write_manifest(artifacts.manifest_path, manifest)

    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    publish_delta(
        artifacts,
        ssh_target=args.ssh_target,
        remote_dir=args.remote_dir,
        compose_file=args.remote_compose_file,
        env_file=args.remote_env_file,
        db_service=args.db_service,
        web_service=args.web_service,
        db_user=args.db_user,
        target_db=args.target_db,
    )
    verification = inspect_remote(
        ssh_target=args.ssh_target,
        remote_dir=args.remote_dir,
        compose_file=args.remote_compose_file,
        env_file=args.remote_env_file,
        db_service=args.db_service,
        db_user=args.db_user,
        target_db=args.target_db,
        countries=countries,
        property_ids=property_ids,
    )
    expected_total_countries = remote["country_count"] + sum(
        1
        for country in countries
        if remote["target_country_counts"].get(country, 0) == 0
        and expected_country_counts.get(country, 0) > 0
    )
    if verification["country_count"] != expected_total_countries:
        raise RuntimeError(
            "Remote country count mismatch after publish: "
            f"expected {expected_total_countries}, got {verification['country_count']}"
        )
    if verification["target_country_counts"] != expected_country_counts:
        raise RuntimeError(
            "Remote target country counts mismatch after publish: "
            f"expected {expected_country_counts}, "
            f"got {verification['target_country_counts']}"
        )
    if property_ids and set(verification["selected_properties"]) != set(property_ids):
        raise RuntimeError("Remote selected property ids mismatch after publish")

    manifest["publish_result"] = "completed"
    manifest["published_at_utc"] = datetime.now(UTC).isoformat()
    manifest["remote_verification"] = {
        "country_count": verification["country_count"],
        "target_property_count": verification["target_property_count"],
        "selected_property_count": len(verification["selected_properties"]),
        "target_country_counts": verification["target_country_counts"],
        "disk_used_percent": verification["disk_used_percent"],
    }
    activate_public_reports(
        artifacts,
        ssh_target=args.ssh_target,
        remote_dir=args.remote_dir,
    )
    manifest["country_reports_activated"] = sorted(report_patch["reports"])
    _write_manifest(artifacts.manifest_path, manifest)
    copy_manifest(
        artifacts,
        ssh_target=args.ssh_target,
        remote_dir=args.remote_dir,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def normalize_countries(values: list[str]) -> list[str]:
    countries = sorted({str(value).strip() for value in values if str(value).strip()})
    if not countries:
        raise ValueError("At least one non-empty country is required")
    return countries


def normalize_property_ids(values: list[str] | None) -> list[str]:
    return sorted({str(value).strip() for value in values or [] if str(value).strip()})


def build_target_database(
    source_url: str,
    *,
    countries: list[str],
    target_path: Path,
    property_ids: list[str] | None = None,
) -> dict[str, Any]:
    source_path = sqlite_path_from_dsn(source_url)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
        source.backup(target)

    target_url = f"sqlite+pysqlite:///{target_path}"
    repository = SQLAlchemyScanRunRepository.from_url(
        target_url,
        storage_mode="sqlite",
        create_schema=False,
    )
    country_set = set(countries)
    property_id_set = set(property_ids or [])
    summary_rows = [
        row
        for row in repository.latest_candidate_summary_rows(
            include_blocked_quality=True
        )
        if str(row.get("country") or "") in country_set
        and bool((row.get("visibility") or {}).get("export_ready"))
        and (
            not property_id_set
            or str(row.get("property_id") or "") in property_id_set
        )
    ]
    if not summary_rows:
        raise RuntimeError(f"No latest properties found for countries: {countries}")
    property_ids = sorted({str(row["property_id"]) for row in summary_rows})
    if property_id_set:
        missing_property_ids = sorted(property_id_set - set(property_ids))
        if missing_property_ids:
            raise RuntimeError(
                "Selected property ids are absent from the latest target-country view: "
                f"{missing_property_ids}"
            )
    scan_run_ids = sorted({str(row["scan_run_id"]) for row in summary_rows})
    country_counts = {
        country: sum(1 for row in summary_rows if str(row["country"]) == country)
        for country in countries
    }
    empty_countries = [country for country, count in country_counts.items() if count == 0]
    if empty_countries:
        raise RuntimeError(f"Countries have no latest properties: {empty_countries}")

    with sqlite3.connect(target_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "CREATE TEMP TABLE delta_target_pairs "
            "(property_id TEXT NOT NULL, scan_run_id TEXT NOT NULL, "
            "PRIMARY KEY (property_id, scan_run_id))"
        )
        connection.executemany(
            "INSERT INTO delta_target_pairs (property_id, scan_run_id) VALUES (?, ?)",
            [
                (str(row["property_id"]), str(row["scan_run_id"]))
                for row in summary_rows
            ],
        )
        table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in table_names:
            if table in PUBLIC_API_TABLES:
                connection.execute(f"DELETE FROM {_quote_identifier(table)}")
                continue
            columns = {
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({_quote_identifier(table)})"
                )
            }
            if table == "scan_runs":
                placeholders = ",".join("?" for _ in scan_run_ids)
                connection.execute(
                    f"DELETE FROM {_quote_identifier(table)} "  # noqa: S608
                    f"WHERE id NOT IN ({placeholders})",
                    scan_run_ids,
                )
            elif table == "properties":
                connection.execute(
                    "DELETE FROM properties WHERE id NOT IN "
                    "(SELECT property_id FROM delta_target_pairs)"
                )
            elif table == "discovery_progress" and property_id_set:
                connection.execute("DELETE FROM discovery_progress")
            elif table == "discovery_progress":
                placeholders = ",".join("?" for _ in countries)
                connection.execute(
                    f"DELETE FROM discovery_progress "  # noqa: S608
                    f"WHERE country NOT IN ({placeholders})",
                    countries,
                )
            elif "property_id" in columns and "scan_run_id" in columns:
                connection.execute(
                    f"DELETE FROM {_quote_identifier(table)} "  # noqa: S608
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM delta_target_pairs target "
                    f"WHERE target.property_id = {_quote_identifier(table)}.property_id "
                    f"AND target.scan_run_id = {_quote_identifier(table)}.scan_run_id)"
                )
            elif "property_id" in columns:
                connection.execute(
                    f"DELETE FROM {_quote_identifier(table)} "  # noqa: S608
                    "WHERE property_id NOT IN "
                    "(SELECT property_id FROM delta_target_pairs)"
                )
        connection.commit()

    return {
        "property_ids": property_ids,
        "scan_run_ids": scan_run_ids,
        "property_countries": {
            str(row["property_id"]): str(row["country"]) for row in summary_rows
        },
    }


def strict_localization_gate(source_url: str) -> tuple[dict[str, Any], list[Any]]:
    repository = SQLAlchemyScanRunRepository.from_url(
        source_url,
        storage_mode="sqlite",
        create_schema=False,
    )
    packets = repository.list_properties()
    summary = refresh_localization_cache_for_packets(
        packets,
        repository.engine,
        locales=supported_locales(),
        text_kinds=DEFAULT_LOCALIZATION_TEXT_KINDS,
        provider_mode="noop",
        dry_run=True,
    )
    counts = summary.get("counts") or {}
    blockers = {
        "provider_request_candidate_count": int(
            summary.get("provider_request_candidate_count") or 0
        ),
        "dry_run_missing_count": int(counts.get("dry_run_missing_count") or 0),
        "invalid_cache_entry_count": int(counts.get("invalid_cache_entry_count") or 0),
        "error_count": int(summary.get("error_count") or 0),
    }
    if any(blockers.values()):
        raise RuntimeError(f"Scoped localization gate failed: {blockers}")
    summary["status"] = "passed"
    summary["property_count"] = len(packets)
    return summary, packets


def strict_city_assignment_gate(
    database_path: Path,
    *,
    expected_property_count: int,
) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        property_count = int(
            connection.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
        )
        missing_city_id_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM properties "
                "WHERE city_id IS NULL OR trim(city_id) = ''"
            ).fetchone()[0]
        )
        missing_assignment_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM properties p "
                "LEFT JOIN property_city_assignments a ON a.property_id = p.id "
                "WHERE a.property_id IS NULL"
            ).fetchone()[0]
        )
        invalid_assignment_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM properties p "
                "JOIN property_city_assignments a ON a.property_id = p.id "
                "WHERE a.mapping_status <> 'verified' "
                "OR a.city_id IS NULL OR trim(a.city_id) = '' "
                "OR a.city_id <> p.city_id "
                "OR a.canonical_city <> p.city"
            ).fetchone()[0]
        )
        identity_collision_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT country, scene_type, property_identity_key "
                "FROM properties GROUP BY country, scene_type, property_identity_key "
                "HAVING COUNT(*) > 1)"
            ).fetchone()[0]
        )
    blockers = {
        "property_count_mismatch": int(property_count != expected_property_count),
        "missing_city_id_count": missing_city_id_count,
        "missing_assignment_count": missing_assignment_count,
        "invalid_assignment_count": invalid_assignment_count,
        "identity_collision_count": identity_collision_count,
    }
    if any(blockers.values()):
        raise RuntimeError(f"Scoped city assignment gate failed: {blockers}")
    return {
        "status": "passed",
        "property_count": property_count,
        **blockers,
    }


def validate_public_preaggregation(
    public_api: PublicApiPreaggregation,
    *,
    expected_property_count: int,
) -> None:
    metadata = public_api.metadata
    blockers = {
        "status": metadata.get("status"),
        "localization_cache_miss_count": int(
            metadata.get("localization_cache_miss_count") or 0
        ),
        "localization_pending_text_count": int(
            metadata.get("localization_pending_text_count") or 0
        ),
        "property_index_count": len(
            public_api.rows.get("public_api_property_index", [])
        ),
    }
    if blockers["status"] != "generated":
        raise RuntimeError(f"Public preaggregation was not generated: {blockers}")
    if blockers["localization_cache_miss_count"]:
        raise RuntimeError(f"Public preaggregation has cache misses: {blockers}")
    if blockers["localization_pending_text_count"]:
        raise RuntimeError(f"Public preaggregation has pending text: {blockers}")
    if blockers["property_index_count"] != expected_property_count:
        raise RuntimeError(f"Public preaggregation property count mismatch: {blockers}")
    if any(
        token in json.dumps(public_api.rows, ensure_ascii=False)
        for token in PENDING_TEXT
    ):
        raise RuntimeError("Public preaggregation contains localization pending text")


def load_scoped_display_rows(
    database_path: Path,
    *,
    packets: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows: dict[str, list[dict[str, Any]]] = {}
        for table in DISPLAY_TABLES:
            columns = _sqlite_table_columns(connection, table)
            column_names = [str(column["name"]) for column in columns]
            if table == "localized_text_cache":
                rows[table] = _required_localization_rows(
                    connection,
                    packets=packets,
                    column_names=column_names,
                )
                continue
            quoted_columns = ", ".join(
                _quote_identifier(column) for column in column_names
            )
            values = connection.execute(
                f"SELECT {quoted_columns} FROM {_quote_identifier(table)}"  # noqa: S608
            ).fetchall()
            rows[table] = [dict(value) for value in values]
        return rows


def _required_localization_rows(
    connection: sqlite3.Connection,
    *,
    packets: list[Any],
    column_names: list[str],
) -> list[dict[str, Any]]:
    required: set[tuple[str, str, str]] = set()
    locales = [resolve_locale(locale) for locale in supported_locales()]
    for candidate in text_candidates_from_packets(
        packets,
        text_kinds=DEFAULT_LOCALIZATION_TEXT_KINDS,
    ):
        source_locale = detect_source_locale(candidate.source_text)
        for locale in locales:
            if source_locale == locale:
                continue
            required.add(
                (
                    source_text_hash(candidate.source_text),
                    candidate.text_kind,
                    locale,
                )
            )
    quoted_columns = ", ".join(_quote_identifier(column) for column in column_names)
    values = connection.execute(
        f"SELECT {quoted_columns} FROM localized_text_cache"  # noqa: S608
    ).fetchall()
    selected = []
    found: set[tuple[str, str, str]] = set()
    for value in values:
        row = dict(value)
        key = (
            source_text_hash(str(row["source_text"])),
            str(row["text_kind"]),
            str(row["target_locale"]),
        )
        if (
            key in required
            and str(row["schema_version"]) == localization_schema_version()
        ):
            selected.append(row)
            found.add(key)
    missing = required - found
    if missing:
        raise RuntimeError(
            f"Strict gate passed but {len(missing)} required cache rows were not selected"
        )
    return selected


def inspect_remote(
    *,
    ssh_target: str,
    remote_dir: str,
    compose_file: str,
    env_file: str,
    db_service: str,
    db_user: str,
    target_db: str,
    countries: list[str],
    property_ids: list[str] | None = None,
) -> dict[str, Any]:
    compose = _remote_compose_command(
        remote_dir=remote_dir,
        compose_file=compose_file,
        env_file=env_file,
    )
    psql = (
        f"{compose} exec -T {shlex.quote(db_service)} "
        f"psql -U {shlex.quote(db_user)} -d {shlex.quote(target_db)} -At -F '|'"
    )
    country_literals = ", ".join(_postgres_literal(country) for country in countries)
    query_lines = [
        "SELECT 'country_count', COUNT(DISTINCT country) FROM public.properties;",
        "SELECT 'target_property_count', COUNT(*) FROM public.properties "
        f"WHERE country IN ({country_literals});",
        "SELECT 'target_country_count', country, COUNT(*) FROM public.properties "
        f"WHERE country IN ({country_literals}) GROUP BY country ORDER BY country;",
        "SELECT 'target_export_state', country, property_id, scan_run_id "
        "FROM public.public_api_property_index "
        f"WHERE country IN ({country_literals}) AND export_ready IS TRUE "
        "ORDER BY country, property_id;",
    ]
    if property_ids:
        property_literals = ", ".join(
            _postgres_literal(property_id) for property_id in property_ids
        )
        query_lines.append(
            "SELECT 'selected_property', id, country FROM public.properties "
            f"WHERE id IN ({property_literals}) ORDER BY id;"
        )
    query_lines.append(
        "SELECT 'column', table_name, column_name "
        "FROM information_schema.columns "
        "WHERE table_schema='public' ORDER BY table_name, ordinal_position;"
    )
    query = "\n".join(query_lines)
    output = _ssh_input(ssh_target, psql, query)
    columns: dict[str, list[str]] = {}
    country_count = 0
    target_property_count = 0
    target_country_counts: dict[str, int] = {country: 0 for country in countries}
    selected_properties: dict[str, str] = {}
    target_export_state: dict[str, dict[str, str]] = {
        country: {} for country in countries
    }
    for line in output.splitlines():
        parts = line.split("|")
        if not parts:
            continue
        if parts[0] == "country_count" and len(parts) == 2:
            country_count = int(parts[1])
        elif parts[0] == "target_property_count" and len(parts) == 2:
            target_property_count = int(parts[1])
        elif parts[0] == "target_country_count" and len(parts) == 3:
            target_country_counts[parts[1]] = int(parts[2])
        elif parts[0] == "selected_property" and len(parts) == 3:
            selected_properties[parts[1]] = parts[2]
        elif parts[0] == "target_export_state" and len(parts) == 4:
            target_export_state.setdefault(parts[1], {})[parts[2]] = parts[3]
        elif parts[0] == "column" and len(parts) == 3:
            columns.setdefault(parts[1], []).append(parts[2])
    required_tables = set(DISPLAY_TABLES) | set(PUBLIC_API_TABLES)
    missing_tables = sorted(required_tables - set(columns))
    if missing_tables:
        raise RuntimeError(f"Remote public DB is missing required tables: {missing_tables}")
    disk_output = _run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            ssh_target,
            f"df -P {shlex.quote(remote_dir)} | tail -1 | awk '{{print $5}}'",
        ]
    ).strip()
    return {
        "country_count": country_count,
        "target_property_count": target_property_count,
        "target_country_counts": target_country_counts,
        "selected_properties": selected_properties,
        "target_export_state": target_export_state,
        "columns": columns,
        "disk_used_percent": int(disk_output.rstrip("%")),
    }


def compatible_remote_columns(
    *,
    display_rows: dict[str, list[dict[str, Any]]],
    public_api: PublicApiPreaggregation,
    remote_columns: dict[str, list[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    compatible: dict[str, list[str]] = {}
    omitted: dict[str, list[str]] = {}
    for table in DISPLAY_TABLES:
        local_columns = list(display_rows[table][0]) if display_rows[table] else []
        if not local_columns:
            local_columns = list(remote_columns[table])
        remote_set = set(remote_columns[table])
        compatible[table] = [column for column in local_columns if column in remote_set]
        omitted[table] = [column for column in local_columns if column not in remote_set]
    for table in PUBLIC_API_TABLES:
        local_columns = [
            name for name, _column_type, _not_null in PUBLIC_API_GENERATED_SCHEMAS[table]
        ]
        remote_set = set(remote_columns[table])
        compatible[table] = [column for column in local_columns if column in remote_set]
        omitted[table] = [column for column in local_columns if column not in remote_set]
        primary_key = PUBLIC_API_PRIMARY_KEYS[table]
        if any(column not in compatible[table] for column in primary_key):
            raise RuntimeError(f"Remote {table} is missing its required primary key")
    for table, columns in compatible.items():
        if (display_rows.get(table) or public_api.rows.get(table)) and not columns:
            raise RuntimeError(f"No compatible remote columns for {table}")
    return compatible, {table: values for table, values in omitted.items() if values}


def build_delta_sql(
    *,
    countries: list[str],
    display_rows: dict[str, list[dict[str, Any]]],
    public_api: PublicApiPreaggregation,
    compatible_columns: dict[str, list[str]],
    expected_country_counts: dict[str, int],
    require_target_absent: bool,
    property_ids: list[str] | None = None,
    release_name: str | None = None,
) -> str:
    if property_ids:
        if not release_name:
            raise ValueError("release_name is required for a property-scoped delta")
        return _build_property_delta_sql(
            property_ids=property_ids,
            release_name=release_name,
            display_rows=display_rows,
            public_api=public_api,
            compatible_columns=compatible_columns,
            expected_country_counts=expected_country_counts,
        )
    country_literals = ", ".join(_postgres_literal(country) for country in countries)
    lines = [
        r"\set ON_ERROR_STOP on",
        "BEGIN;",
        "SELECT pg_advisory_xact_lock(hashtext('isite2_public_country_delta'));",
        "CREATE TEMP TABLE _isite2_delta_baseline AS SELECT",
        "  (SELECT COUNT(*) FROM public.properties "
        f"WHERE country NOT IN ({country_literals})) AS non_target_properties,",
        "  (SELECT COUNT(*) FROM public.public_api_property_index "
        f"WHERE country NOT IN ({country_literals})) AS non_target_index,",
        "  (SELECT COUNT(*) FROM public.public_api_property_packets "
        f"WHERE country NOT IN ({country_literals})) AS non_target_packets,",
        "  (SELECT COUNT(*) FROM public.public_api_map_features "
        f"WHERE country NOT IN ({country_literals})) AS non_target_map_features,",
        "  (SELECT COUNT(*) FROM public.public_api_review_queue_rows "
        f"WHERE country NOT IN ({country_literals})) AS non_target_review_rows,",
        "  (SELECT COUNT(DISTINCT country) FROM public.properties) AS country_count,",
        "  (SELECT COUNT(*) FROM public.properties "
        f"WHERE country IN ({country_literals})) AS target_properties_before;",
    ]
    if require_target_absent:
        lines.extend(
            [
                "DO $$ BEGIN",
                "  IF (SELECT target_properties_before FROM _isite2_delta_baseline) <> 0 THEN",
                "    RAISE EXCEPTION 'target countries are not absent before additive delta';",
                "  END IF;",
                "END $$;",
            ]
        )
    expected_values = ",\n".join(
        f"  ({_postgres_literal(country)}, {count})"
        for country, count in sorted(expected_country_counts.items())
    )
    lines.extend(
        [
            "CREATE TEMP TABLE _isite2_delta_expected_country "
            "(country TEXT PRIMARY KEY, property_count INTEGER NOT NULL);",
            "INSERT INTO _isite2_delta_expected_country (country, property_count) VALUES",
            expected_values + ";",
        ]
    )
    lines.extend(_delete_target_sql(country_literals))

    for table in DISPLAY_TABLES:
        rows = display_rows.get(table, [])
        lines.extend(
            _insert_rows_sql(
                table,
                rows,
                compatible_columns[table],
                json_columns=set(),
                conflict_columns=["id"],
                do_nothing=table == "scan_runs",
            )
        )
    for table in PUBLIC_API_TABLES:
        type_by_column = {
            name: column_type
            for name, column_type, _not_null in PUBLIC_API_GENERATED_SCHEMAS[table]
        }
        json_columns = {
            name for name, column_type in type_by_column.items() if column_type == "JSONB"
        }
        lines.extend(
            _insert_rows_sql(
                table,
                public_api.rows.get(table, []),
                compatible_columns[table],
                json_columns=json_columns,
                conflict_columns=PUBLIC_API_PRIMARY_KEYS[table],
            )
        )

    expected_counts = {
        table: len(rows) for table, rows in public_api.rows.items()
    }
    expected_property_count = len(display_rows.get("properties", []))
    lines.extend(
        [
            "DO $$ BEGIN",
            "  IF (SELECT COUNT(*) FROM public.properties "
            f"WHERE country IN ({country_literals})) <> {expected_property_count} THEN",
            "    RAISE EXCEPTION 'target properties count mismatch';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_property_index "
            f"WHERE country IN ({country_literals})) <> "
            f"{expected_counts['public_api_property_index']} THEN",
            "    RAISE EXCEPTION 'target public index count mismatch';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_property_packets "
            f"WHERE country IN ({country_literals})) <> "
            f"{expected_counts['public_api_property_packets']} THEN",
            "    RAISE EXCEPTION 'target packet count mismatch';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_map_features "
            f"WHERE country IN ({country_literals})) <> "
            f"{expected_counts['public_api_map_features']} THEN",
            "    RAISE EXCEPTION 'target map feature count mismatch';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_review_queue_rows "
            f"WHERE country IN ({country_literals})) <> "
            f"{expected_counts['public_api_review_queue_rows']} THEN",
            "    RAISE EXCEPTION 'target review row count mismatch';",
            "  END IF;",
            "  IF EXISTS (",
            "    SELECT 1 FROM _isite2_delta_expected_country expected",
            "    LEFT JOIN (SELECT country, COUNT(*) AS property_count "
            "      FROM public.properties GROUP BY country) actual USING (country)",
            "    WHERE COALESCE(actual.property_count, 0) <> expected.property_count",
            "  ) THEN",
            "    RAISE EXCEPTION 'one or more target country counts mismatch';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.properties "
            f"WHERE country NOT IN ({country_literals})) <> "
            "(SELECT non_target_properties FROM _isite2_delta_baseline) THEN",
            "    RAISE EXCEPTION 'non-target properties changed';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_property_index "
            f"WHERE country NOT IN ({country_literals})) <> "
            "(SELECT non_target_index FROM _isite2_delta_baseline) THEN",
            "    RAISE EXCEPTION 'non-target public index changed';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_property_packets "
            f"WHERE country NOT IN ({country_literals})) <> "
            "(SELECT non_target_packets FROM _isite2_delta_baseline) THEN",
            "    RAISE EXCEPTION 'non-target packets changed';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_map_features "
            f"WHERE country NOT IN ({country_literals})) <> "
            "(SELECT non_target_map_features FROM _isite2_delta_baseline) THEN",
            "    RAISE EXCEPTION 'non-target map features changed';",
            "  END IF;",
            "  IF (SELECT COUNT(*) FROM public.public_api_review_queue_rows "
            f"WHERE country NOT IN ({country_literals})) <> "
            "(SELECT non_target_review_rows FROM _isite2_delta_baseline) THEN",
            "    RAISE EXCEPTION 'non-target review rows changed';",
            "  END IF;",
            "END $$;",
            "COMMIT;",
            "",
        ]
    )
    sql = "\n".join(lines)
    if TRUNCATE_STATEMENT_RE.search(sql):
        raise RuntimeError("Country delta must never contain TRUNCATE")
    return sql


def _build_property_delta_sql(
    *,
    property_ids: list[str],
    release_name: str,
    display_rows: dict[str, list[dict[str, Any]]],
    public_api: PublicApiPreaggregation,
    compatible_columns: dict[str, list[str]],
    expected_country_counts: dict[str, int],
) -> str:
    target_ids = normalize_property_ids(property_ids)
    property_literals = ", ".join(
        _postgres_literal(property_id) for property_id in target_ids
    )
    scoped_properties = display_rows.get("properties", [])
    scoped_ids = {str(row["id"]) for row in scoped_properties}
    if scoped_ids != set(target_ids):
        raise RuntimeError(
            "Property delta rows do not match requested ids: "
            f"requested={target_ids}, scoped={sorted(scoped_ids)}"
        )

    mutation_tables = ["properties", *PROPERTY_CHILD_TABLES, *PUBLIC_API_TABLES]
    baseline_columns = {
        table: f"non_target_{index}" for index, table in enumerate(mutation_tables)
    }
    baseline_selects = []
    for table in mutation_tables:
        key = "id" if table == "properties" else "property_id"
        baseline_selects.append(
            f"  (SELECT COUNT(*) FROM public.{_quote_identifier(table)} "
            f"WHERE {_quote_identifier(key)} NOT IN ({property_literals})) "
            f"AS {_quote_identifier(baseline_columns[table])}"
        )
    baseline_selects.extend(
        [
            "  (SELECT COUNT(DISTINCT country) FROM public.properties) AS country_count",
            "  (SELECT COUNT(*) FROM public.properties "
            f"WHERE id IN ({property_literals})) AS selected_properties_before",
        ]
    )
    lines = [
        r"\set ON_ERROR_STOP on",
        "BEGIN;",
        "SELECT pg_advisory_xact_lock(hashtext('isite2_public_property_delta'));",
        "CREATE TEMP TABLE _isite2_delta_baseline AS SELECT",
        ",\n".join(baseline_selects) + ";",
        f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(PROPERTY_BACKUP_SCHEMA)};",
    ]
    for table in mutation_tables:
        key = "id" if table == "properties" else "property_id"
        backup_table = _backup_table_name(release_name, table)
        lines.append(
            f"CREATE TABLE {_quote_identifier(PROPERTY_BACKUP_SCHEMA)}."
            f"{_quote_identifier(backup_table)} AS SELECT * FROM "
            f"public.{_quote_identifier(table)} WHERE {_quote_identifier(key)} "
            f"IN ({property_literals});"
        )

    expected_values = ",\n".join(
        f"  ({_postgres_literal(country)}, {count})"
        for country, count in sorted(expected_country_counts.items())
    )
    lines.extend(
        [
            "CREATE TEMP TABLE _isite2_delta_expected_country "
            "(country TEXT PRIMARY KEY, property_count INTEGER NOT NULL);",
            "INSERT INTO _isite2_delta_expected_country (country, property_count) VALUES",
            expected_values + ";",
            *_delete_property_target_sql(property_literals),
        ]
    )

    for table in DISPLAY_TABLES:
        if table == "discovery_progress":
            continue
        lines.extend(
            _insert_rows_sql(
                table,
                display_rows.get(table, []),
                compatible_columns[table],
                json_columns=set(),
                conflict_columns=["id"],
                do_nothing=table == "scan_runs",
            )
        )
    for table in PUBLIC_API_TABLES:
        type_by_column = {
            name: column_type
            for name, column_type, _not_null in PUBLIC_API_GENERATED_SCHEMAS[table]
        }
        json_columns = {
            name for name, column_type in type_by_column.items() if column_type == "JSONB"
        }
        lines.extend(
            _insert_rows_sql(
                table,
                public_api.rows.get(table, []),
                compatible_columns[table],
                json_columns=json_columns,
                conflict_columns=PUBLIC_API_PRIMARY_KEYS[table],
            )
        )

    expected_target_counts = {
        "properties": len(display_rows.get("properties", [])),
        **{
            table: len(display_rows.get(table, [])) for table in PROPERTY_CHILD_TABLES
        },
        **{table: len(public_api.rows.get(table, [])) for table in PUBLIC_API_TABLES},
    }
    checks = []
    for table in mutation_tables:
        key = "id" if table == "properties" else "property_id"
        checks.extend(
            [
                f"  IF (SELECT COUNT(*) FROM public.{_quote_identifier(table)} "
                f"WHERE {_quote_identifier(key)} IN ({property_literals})) <> "
                f"{expected_target_counts[table]} THEN",
                f"    RAISE EXCEPTION 'target {table} count mismatch';",
                "  END IF;",
                f"  IF (SELECT COUNT(*) FROM public.{_quote_identifier(table)} "
                f"WHERE {_quote_identifier(key)} NOT IN ({property_literals})) <> "
                f"(SELECT {_quote_identifier(baseline_columns[table])} "
                "FROM _isite2_delta_baseline) THEN",
                f"    RAISE EXCEPTION 'non-target {table} rows changed';",
                "  END IF;",
            ]
        )
    checks.extend(
        [
            "  IF EXISTS (",
            "    SELECT 1 FROM _isite2_delta_expected_country expected",
            "    LEFT JOIN (SELECT country, COUNT(*) AS property_count "
            "      FROM public.properties GROUP BY country) actual USING (country)",
            "    WHERE COALESCE(actual.property_count, 0) <> expected.property_count",
            "  ) THEN",
            "    RAISE EXCEPTION 'one or more target country counts mismatch';",
            "  END IF;",
        ]
    )
    lines.extend(["DO $$ BEGIN", *checks, "END $$;", "COMMIT;", ""])
    sql = "\n".join(lines)
    if TRUNCATE_STATEMENT_RE.search(sql):
        raise RuntimeError("Property delta must never contain TRUNCATE")
    return sql


def _delete_target_sql(country_literals: str) -> list[str]:
    lines = []
    for table in reversed(PUBLIC_API_TABLES):
        lines.append(
            f"DELETE FROM public.{_quote_identifier(table)} "
            f"WHERE country IN ({country_literals});"
        )
    property_children = [
        "review_queue",
        "inference_records",
        "demand_estimates",
        "build_statuses",
        "scene_model_results",
        "evidence_items",
        "scan_candidates",
        "property_aliases",
    ]
    for table in property_children:
        lines.append(
            f"DELETE FROM public.{_quote_identifier(table)} WHERE property_id IN "
            "(SELECT id FROM public.properties "
            f"WHERE country IN ({country_literals}));"
        )
    lines.extend(
        [
            "DELETE FROM public.properties "
            f"WHERE country IN ({country_literals});",
            "DELETE FROM public.discovery_progress "
            f"WHERE country IN ({country_literals});",
        ]
    )
    return lines


def _delete_property_target_sql(property_literals: str) -> list[str]:
    lines = []
    for table in reversed(PUBLIC_API_TABLES):
        lines.append(
            f"DELETE FROM public.{_quote_identifier(table)} "
            f"WHERE property_id IN ({property_literals});"
        )
    for table in reversed(PROPERTY_CHILD_TABLES):
        lines.append(
            f"DELETE FROM public.{_quote_identifier(table)} "
            f"WHERE property_id IN ({property_literals});"
        )
    lines.append(
        "DELETE FROM public.properties "
        f"WHERE id IN ({property_literals});"
    )
    return lines


def _backup_table_name(release_name: str, table: str) -> str:
    safe_release = re.sub(r"[^a-zA-Z0-9_]+", "_", release_name).strip("_").lower()
    value = f"{safe_release}__{table}"
    if not IDENTIFIER_RE.fullmatch(value) or len(value) > 63:
        raise ValueError(f"Unsafe property-delta backup table name: {value!r}")
    return value


def _insert_rows_sql(
    table: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    json_columns: set[str],
    conflict_columns: list[str],
    do_nothing: bool = False,
) -> list[str]:
    if not rows:
        return []
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    output = []
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if column in json_columns:
                value = _public_api_literal_value(value, "JSONB")
            values.append(_postgres_literal(value))
        conflict = ", ".join(_quote_identifier(column) for column in conflict_columns)
        if do_nothing:
            action = f"ON CONFLICT ({conflict}) DO NOTHING"
        else:
            updates = [
                f"{_quote_identifier(column)}=EXCLUDED.{_quote_identifier(column)}"
                for column in columns
                if column not in conflict_columns
            ]
            action = (
                f"ON CONFLICT ({conflict}) DO UPDATE SET " + ", ".join(updates)
                if updates
                else f"ON CONFLICT ({conflict}) DO NOTHING"
            )
        output.append(
            f"INSERT INTO public.{_quote_identifier(table)} ({quoted_columns}) "
            f"VALUES ({', '.join(values)}) {action};"
        )
    return output


def build_additive_rollback_sql(countries: list[str]) -> str:
    country_literals = ", ".join(_postgres_literal(country) for country in countries)
    return "\n".join(
        [
            r"\set ON_ERROR_STOP on",
            "BEGIN;",
            "SELECT pg_advisory_xact_lock(hashtext('isite2_public_country_delta'));",
            *_delete_target_sql(country_literals),
            "COMMIT;",
            "",
        ]
    )


def build_property_rollback_sql(
    property_ids: list[str],
    *,
    release_name: str,
) -> str:
    target_ids = normalize_property_ids(property_ids)
    if not target_ids:
        raise ValueError("At least one property id is required for property rollback")
    property_literals = ", ".join(
        _postgres_literal(property_id) for property_id in target_ids
    )
    restore_tables = ["properties", *PROPERTY_CHILD_TABLES, *PUBLIC_API_TABLES]
    lines = [
        r"\set ON_ERROR_STOP on",
        "BEGIN;",
        "SELECT pg_advisory_xact_lock(hashtext('isite2_public_property_delta'));",
        *_delete_property_target_sql(property_literals),
    ]
    for table in restore_tables:
        backup_table = _backup_table_name(release_name, table)
        lines.append(
            f"INSERT INTO public.{_quote_identifier(table)} SELECT * FROM "
            f"{_quote_identifier(PROPERTY_BACKUP_SCHEMA)}."
            f"{_quote_identifier(backup_table)};"
        )
    lines.extend(["COMMIT;", ""])
    sql = "\n".join(lines)
    if TRUNCATE_STATEMENT_RE.search(sql):
        raise RuntimeError("Property rollback must never contain TRUNCATE")
    return sql


def publish_delta(
    artifacts: DeltaArtifacts,
    *,
    ssh_target: str,
    remote_dir: str,
    compose_file: str,
    env_file: str,
    db_service: str,
    web_service: str,
    db_user: str,
    target_db: str,
) -> None:
    remote_release = f"{remote_dir.rstrip('/')}/releases/{artifacts.release_name}"
    remote_report_incoming = (
        f"{remote_dir.rstrip('/')}/public_reports/incoming/{artifacts.release_name}"
    )
    _run(["ssh", "-o", "BatchMode=yes", ssh_target, f"mkdir -p {shlex.quote(remote_release)}"])
    _run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            ssh_target,
            f"mkdir -p {shlex.quote(remote_report_incoming)}",
        ]
    )
    _run(
        [
            "scp",
            str(artifacts.sql_path),
            str(artifacts.rollback_path),
            str(artifacts.manifest_path),
            f"{ssh_target}:{remote_release}/",
        ]
    )
    _run(
        [
            "scp",
            "-r",
            str(artifacts.release_dir / "public_reports" / "index_patch.json"),
            str(artifacts.release_dir / "public_reports" / "releases"),
            f"{ssh_target}:{remote_report_incoming}/",
        ]
    )
    compose = _remote_compose_command(
        remote_dir=remote_dir,
        compose_file=compose_file,
        env_file=env_file,
    )
    remote_sql = f"{remote_release}/{artifacts.sql_path.name}"
    apply_command = (
        f"test ! -e /var/lib/isite2-disk-guard/block-heavy-jobs && "
        f"{compose} exec -T {shlex.quote(db_service)} "
        f"psql -U {shlex.quote(db_user)} -d {shlex.quote(target_db)} "
        f"< {shlex.quote(remote_sql)}"
    )
    _run(["ssh", "-o", "BatchMode=yes", ssh_target, apply_command])
    _run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            ssh_target,
            f"{compose} up -d {shlex.quote(web_service)}",
        ]
    )


def copy_manifest(
    artifacts: DeltaArtifacts,
    *,
    ssh_target: str,
    remote_dir: str,
) -> None:
    remote_release = f"{remote_dir.rstrip('/')}/releases/{artifacts.release_name}"
    _run(
        [
            "scp",
            str(artifacts.manifest_path),
            f"{ssh_target}:{remote_release}/{artifacts.manifest_path.name}",
        ]
    )


def activate_public_reports(
    artifacts: DeltaArtifacts,
    *,
    ssh_target: str,
    remote_dir: str,
) -> None:
    remote_report_root = f"{remote_dir.rstrip('/')}/public_reports"
    incoming = f"{remote_report_root}/incoming/{artifacts.release_name}"
    script = public_report_activation_script(remote_report_root, incoming)
    _ssh_input(ssh_target, "bash -se", script)


def _remote_compose_command(
    *,
    remote_dir: str,
    compose_file: str,
    env_file: str,
) -> str:
    return (
        f"cd {shlex.quote(remote_dir)} && docker compose "
        f"--env-file {shlex.quote(env_file)} -f {shlex.quote(compose_file)}"
    )


def _ssh_input(ssh_target: str, remote_command: str, input_text: str) -> str:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            ssh_target,
            remote_command,
        ],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Remote command failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _run(command: list[str]) -> str:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): "
            f"{shlex.join(command)}\n{result.stderr.strip()}"
        )
    return result.stdout


def _country_counts(public_api: PublicApiPreaggregation) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in public_api.rows.get("public_api_property_index", []):
        country = str(row["country"])
        counts[country] = counts.get(country, 0) + 1
    return counts


def _country_report_state(
    source_url: str,
    countries: list[str],
) -> dict[str, dict[str, str]]:
    repository = SQLAlchemyScanRunRepository.from_url(
        source_url,
        storage_mode="postgis" if source_url.startswith("postgresql") else "sqlite",
        create_schema=False,
    )
    state: dict[str, dict[str, str]] = {}
    for country in countries:
        rows = repository.latest_candidate_summary_rows(
            {"country": country},
            include_blocked_quality=True,
        )
        state[country] = {
            str(row["property_id"]): str(row["scan_run_id"])
            for row in rows
            if bool((row.get("visibility") or {}).get("export_ready"))
        }
    return state


def _state_hashes(state: dict[str, dict[str, str]]) -> dict[str, str]:
    return {
        country: country_export_state_hash(
            {
                "property_id": property_id,
                "scan_run_id": scan_run_id,
                "export_ready": True,
            }
            for property_id, scan_run_id in properties.items()
        )
        for country, properties in state.items()
    }


def validate_property_delta_report_state(
    *,
    countries: list[str],
    selected_property_ids: list[str],
    selected_index_rows: list[dict[str, Any]],
    remote_state: dict[str, dict[str, str]],
    source_state: dict[str, dict[str, str]],
) -> dict[str, str]:
    expected_state = {
        country: dict(remote_state.get(country, {})) for country in countries
    }
    selected_ids = set(selected_property_ids)
    for properties in expected_state.values():
        for property_id in selected_ids:
            properties.pop(property_id, None)
    for row in selected_index_rows:
        property_id = str(row.get("property_id") or "")
        country = str(row.get("country") or "")
        if (
            property_id in selected_ids
            and country in expected_state
            and bool(row.get("export_ready"))
        ):
            expected_state[country][property_id] = str(row.get("scan_run_id") or "")

    mismatches: dict[str, dict[str, int]] = {}
    for country in countries:
        expected = expected_state.get(country, {})
        source = source_state.get(country, {})
        if expected == source:
            continue
        mismatches[country] = {
            "expected_post_delta": len(expected),
            "local_full_country": len(source),
            "missing_from_local": len(set(expected) - set(source)),
            "extra_in_local": len(set(source) - set(expected)),
            "scan_run_mismatch": sum(
                1
                for property_id in set(expected) & set(source)
                if expected[property_id] != source[property_id]
            ),
        }
    if mismatches:
        raise RuntimeError(
            "Property-scoped delta cannot activate a full-country report because "
            "the local export-ready country state differs from the expected remote "
            f"post-delta state: {json.dumps(mismatches, sort_keys=True)}. "
            "Use a country delta or full snapshot so data and audited report activate together."
        )
    return _state_hashes(expected_state)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _redact_source_url(source_url: str) -> str:
    if "@" not in source_url:
        return source_url
    prefix, suffix = source_url.rsplit("@", 1)
    scheme = prefix.split(":", 1)[0]
    return f"{scheme}://***@{suffix}"


def _require_identifiers(*values: str) -> None:
    for value in values:
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"Unsafe identifier: {value!r}")


if __name__ == "__main__":
    raise SystemExit(main())
