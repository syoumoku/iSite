from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
]
PUBLIC_EMPTY_TABLES = [
    "source_cache",
    "raw_evidence_items",
    "candidate_drafts",
]
DEFAULT_RELEASE_ROOT = ROOT / "outputs" / "public_snapshots"
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    args = parser.parse_args()

    if not args.source_url:
        raise SystemExit("DATABASE_URL or --source-url is required")
    if not args.dry_run and not args.ssh_target:
        raise SystemExit("ISITE2_PUBLIC_SSH_TARGET or --ssh-target is required")

    _require_commands(["pg_dump"])
    release_name = datetime.now(UTC).strftime("public_%Y%m%dT%H%M%SZ")
    release_dir = args.release_root / release_name
    release_dir.mkdir(parents=True, exist_ok=False)
    snapshot = build_snapshot(args.source_url, release_name, release_dir)

    remote_script = build_remote_restore_script(
        remote_dir=args.remote_dir,
        remote_compose_file=args.remote_compose_file,
        db_service=args.db_service,
        web_service=args.web_service,
        db_user=args.db_user,
        target_db=args.target_db,
        stage_db=args.stage_db,
        release_name=release_name,
        expected_counts=_load_manifest(snapshot.manifest)["display_table_counts"],
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


def build_snapshot(source_url: str, release_name: str, release_dir: Path) -> SnapshotPaths:
    dump_url = normalize_pg_dsn(source_url)
    schema_sql = release_dir / "schema.sql"
    data_sql = release_dir / "data.sql"
    schema_gz = release_dir / "schema.sql.gz"
    data_gz = release_dir / "data.sql.gz"
    manifest_path = release_dir / "manifest.json"

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

    _gzip_file(schema_sql, schema_gz)
    _gzip_file(data_sql, data_gz)
    schema_sql.unlink()
    data_sql.unlink()

    manifest = {
        "release_name": release_name,
        "published_at": datetime.now(UTC).isoformat(),
        "source_url": _redact_dsn(source_url),
        "schema_version": "0.1",
        "display_tables": DISPLAY_TABLES,
        "public_empty_tables": PUBLIC_EMPTY_TABLES,
        "display_table_counts": load_table_counts(source_url, DISPLAY_TABLES),
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


def publish_snapshot(
    ssh_target: str,
    remote_dir: str,
    snapshot: SnapshotPaths,
    remote_script: str,
) -> None:
    remote_release_dir = f"{remote_dir.rstrip('/')}/releases/{snapshot.release_name}"
    _run(["ssh", ssh_target, "mkdir", "-p", remote_release_dir])
    _run(
        [
            "scp",
            str(snapshot.schema_gz),
            str(snapshot.data_gz),
            str(snapshot.manifest),
            f"{ssh_target}:{remote_release_dir}/",
        ]
    )
    _run(["ssh", ssh_target, "bash", "-se"], input_text=remote_script)


def build_remote_restore_script(
    *,
    remote_dir: str,
    remote_compose_file: str,
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
                    "actual=$(\"${COMPOSE[@]}\" exec -T \"$DB_SERVICE\" "
                    f"psql -U \"$DB_USER\" -d \"$STAGE_DB\" -tAc {count_sql})",
                    f"if [ \"$actual\" != {expected_count} ]; then",
                    f"  echo 'count mismatch for {table}: expected {expected}, "
                    "got '\"$actual\" >&2",
                    "  exit 1",
                    "fi",
                ]
            )
        )

    psql_stage = (
        '"${COMPOSE[@]}" exec -T "$DB_SERVICE" psql -v ON_ERROR_STOP=1 '
        '-U "$DB_USER" -d "$STAGE_DB"'
    )
    psql_admin = (
        '"${COMPOSE[@]}" exec -T "$DB_SERVICE" psql -v ON_ERROR_STOP=1 '
        '-U "$DB_USER" -d postgres'
    )
    db_exists = (
        'db_exists() { "${COMPOSE[@]}" exec -T "$DB_SERVICE" psql '
        '-U "$DB_USER" -d postgres -tAc '
        '"SELECT 1 FROM pg_database WHERE datname = \'$1\'" | grep -q 1; }'
    )
    terminate_connections = (
        f"{psql_admin} -c "
        "\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname IN ('$TARGET_DB', '$STAGE_DB', '$PREVIOUS_DB') "
        "AND pid <> pg_backend_pid();\""
    )
    rename_target_to_previous = (
        f"{psql_admin} -c "
        "\"ALTER DATABASE $TARGET_DB RENAME TO $PREVIOUS_DB;\""
    )
    rename_stage_to_target = (
        f"{psql_admin} -c "
        "\"ALTER DATABASE $STAGE_DB RENAME TO $TARGET_DB;\""
    )

    return "\n".join(
        [
            "set -euo pipefail",
            f"REMOTE_DIR={shlex.quote(remote_dir)}",
            f"COMPOSE_FILE={shlex.quote(remote_compose_file)}",
            f"RELEASE_DIR={shlex.quote(release_dir)}",
            f"DB_SERVICE={shlex.quote(db_service)}",
            f"WEB_SERVICE={shlex.quote(web_service)}",
            f"DB_USER={shlex.quote(db_user)}",
            f"TARGET_DB={shlex.quote(target_db)}",
            f"STAGE_DB={shlex.quote(stage_db)}",
            f"PREVIOUS_DB={shlex.quote(previous_db)}",
            'cd "$REMOTE_DIR"',
            'COMPOSE=(docker compose -f "$COMPOSE_FILE")',
            '"${COMPOSE[@]}" up -d "$DB_SERVICE"',
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" dropdb --if-exists -U "$DB_USER" "$STAGE_DB"',
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" createdb -U "$DB_USER" "$STAGE_DB"',
            f'gzip -dc "$RELEASE_DIR/schema.sql.gz" | {psql_stage}',
            f'gzip -dc "$RELEASE_DIR/data.sql.gz" | {psql_stage}',
            *count_checks,
            db_exists,
            '"${COMPOSE[@]}" stop "$WEB_SERVICE" || true',
            terminate_connections,
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" dropdb --if-exists -U "$DB_USER" "$PREVIOUS_DB"',
            'if db_exists "$TARGET_DB"; then',
            f"  {rename_target_to_previous}",
            "fi",
            rename_stage_to_target,
            '"${COMPOSE[@]}" up -d "$WEB_SERVICE"',
            '"${COMPOSE[@]}" exec -T "$DB_SERVICE" dropdb --if-exists '
            '-U "$DB_USER" "$PREVIOUS_DB" || true',
            'python3 - <<\'PY\' "$RELEASE_DIR/manifest.json"',
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
