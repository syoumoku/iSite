from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from isite2.db.models import (
    CityCanonicalUnitDB,
    CityLocalityMappingDB,
    PropertyCityAssignmentDB,
    PropertyDB,
    ScanCandidateDB,
)
from isite2.growth.city_normalization import (
    apply_verified_city_assignments_to_overlay,
    build_city_normalization_plan,
)
from isite2.growth.evidence_intake import (
    DEFAULT_OVERLAY_PATH,
    load_registry_overlay,
    write_registry_overlay,
)
from isite2.growth.global_city_registry import haversine_km
from isite2.growth.property_identity import normalize_text
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository
from isite2.rules.config_loader import load_city_admin_sources


def main() -> int:
    args = _parse_args()
    load_dotenv()
    database_url = args.database_url or os.getenv(
        "ISITE2_DATABASE_URL",
        os.getenv("DATABASE_URL", "sqlite+pysqlite:///outputs/isite2_dev.db"),
    )
    repository = SQLAlchemyScanRunRepository.from_url(database_url)
    config = load_city_admin_sources()
    countries = _resolve_countries(args, config)
    reports = [
        _build_country_report(
            repository,
            country=country,
            config=config,
            database_url=database_url,
            mode=(
                "apply_verified"
                if args.apply_verified
                else "apply"
                if args.apply
                else "dry_run"
            ),
        )
        for country in countries
    ]
    output_path = args.output or _default_output_path(
        countries[0] if len(countries) == 1 else "global"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_payload = reports[0] if len(reports) == 1 else {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": reports[0]["mode"],
        "country_count": len(reports),
        "countries": reports,
    }
    _write_json(output_path, output_payload)

    if not args.apply and not args.apply_verified:
        if args.merge_exact_collisions:
            raise SystemExit(
                "--merge-exact-collisions requires --apply or --apply-verified"
            )
        print(json.dumps(_batch_summary(reports, output_path), ensure_ascii=False, indent=2))
        return 0
    if args.merge_exact_collisions and any(
        report["identity_collision_count"] for report in reports
    ):
        merge_audit_path = output_path.with_name(
            f"{output_path.stem}.identity_merge.json"
        )
        merge_audit = _merge_sqlite_exact_identity_collisions(
            database_url=database_url,
            reports=reports,
            audit_path=merge_audit_path,
        )
        reports = [
            _build_country_report(
                repository,
                country=country,
                config=config,
                database_url=database_url,
                mode=("apply_verified" if args.apply_verified else "apply"),
            )
            for country in countries
        ]
        for report in reports:
            report["identity_merge_audit"] = str(merge_audit_path)
            report["identity_merge_removed_count"] = merge_audit[
                "removed_property_count"
            ]
        output_payload = reports[0] if len(reports) == 1 else {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": reports[0]["mode"],
            "country_count": len(reports),
            "countries": reports,
        }
        _write_json(output_path, output_payload)
    if any(report["identity_collision_count"] for report in reports):
        raise SystemExit(
            "City normalization apply blocked: identity collisions remain"
        )
    if args.apply and any(report["unresolved_count"] for report in reports):
        raise SystemExit("Strict city normalization apply blocked: unresolved mappings remain")

    backup_path = _overlay_backup_path(
        args.overlay_path,
        countries[0] if len(countries) == 1 else "global_verified",
    )
    if args.overlay_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.overlay_path, backup_path)
    try:
        overlay = load_registry_overlay(args.overlay_path)
        for report in reports:
            country = report["country"]
            report["overlay_updates"] = apply_verified_city_assignments_to_overlay(
                overlay,
                country=country,
                config=config,
                verified_only=args.apply_verified,
            )
        write_registry_overlay(args.overlay_path, overlay)
        for report in reports:
            country = report["country"]
            country_config = (config.get("countries") or {})[country]
            report["database_updates"] = _apply_database_plan(
                repository,
                country=country,
                country_config=country_config,
                mapping_version=str(
                    country_config.get("mapping_version") or config.get("version") or ""
                ),
                plan=report,
                verified_only=args.apply_verified,
            )
    except Exception:
        if backup_path.exists():
            shutil.copy2(backup_path, args.overlay_path)
        raise

    for report in reports:
        report["applied"] = True
        concurrent_skips = (report.get("database_updates") or {}).get(
            "concurrent_properties_skipped", 0
        )
        report["requires_rerun"] = bool(concurrent_skips)
        report["publication_ready"] = not report["unresolved_count"] and not concurrent_skips
        report["overlay_backup"] = str(backup_path) if backup_path.exists() else None
    output_payload = reports[0] if len(reports) == 1 else {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": reports[0]["mode"],
        "country_count": len(reports),
        "countries": reports,
    }
    _write_json(output_path, output_payload)
    print(json.dumps(_batch_summary(reports, output_path), ensure_ascii=False, indent=2))
    return 0


def _resolve_countries(args: argparse.Namespace, config: dict) -> list[str]:
    configured = config.get("countries") or {}
    countries = sorted(configured) if args.all_countries else (args.country or ["Algeria"])
    missing = [country for country in countries if not isinstance(configured.get(country), dict)]
    if missing:
        raise SystemExit(f"No official city configuration exists for: {', '.join(missing)}")
    return countries


def _build_country_report(
    repository: SQLAlchemyScanRunRepository,
    *,
    country: str,
    config: dict,
    database_url: str,
    mode: str,
) -> dict:
    with repository.session_factory() as session:
        properties = session.scalars(
            select(PropertyDB)
            .where(PropertyDB.country == country)
            .order_by(PropertyDB.city, PropertyDB.canonical_name)
        ).all()
        existing_assignments = {
            str(row.property_id): row
            for row in session.scalars(
                select(PropertyCityAssignmentDB).where(
                    PropertyCityAssignmentDB.country == country
                )
            ).all()
        }
        property_payloads = [
            {
                "id": row.id,
                "canonical_name": row.canonical_name,
                "city": row.city,
                "source_city": (
                    existing_assignments[str(row.id)].source_city
                    if str(row.id) in existing_assignments
                    else row.city
                ),
                "scene_type": row.scene_type,
                "latitude": row.latitude,
                "longitude": row.longitude,
            }
            for row in properties
        ]
    plan = build_city_normalization_plan(
        property_payloads,
        country=country,
        config=config,
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "database_url": _redacted_database_url(database_url),
        "mapping_version": config.get("version"),
        **plan,
    }


def _apply_database_plan(
    repository: SQLAlchemyScanRunRepository,
    *,
    country: str,
    country_config: dict,
    mapping_version: str,
    plan: dict,
    verified_only: bool = False,
) -> dict[str, int]:
    planned_by_property = {
        row["property_id"]: row for row in plan["assignments"]
    }
    city_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    concurrent_property_ids: set[str] = set()
    with repository.session_factory() as session:
        for property_row in session.scalars(
            select(PropertyDB).where(PropertyDB.country == country)
        ).all():
            planned = planned_by_property.get(str(property_row.id))
            if planned is None:
                concurrent_property_ids.add(str(property_row.id))
                continue
            assignment = planned["city_assignment"]
            if assignment["mapping_status"] != "verified":
                continue
            city_id = str(assignment["city_id"])
            city_points[city_id].append((property_row.latitude, property_row.longitude))

        sources = country_config.get("sources") or []
        default_source = sources[0] if sources else country_config.get("source") or {}
        source_hash = plan["assignments"][0]["city_assignment"].get("source_hash")
        for city in country_config.get("cities") or []:
            source = city.get("source") or default_source
            city_id = str(city["city_id"])
            points = city_points.get(city_id, [])
            latitude = sum(point[0] for point in points) / len(points) if points else None
            longitude = sum(point[1] for point in points) / len(points) if points else None
            row = session.get(CityCanonicalUnitDB, city_id)
            if row is None:
                row = CityCanonicalUnitDB(city_id=city_id, country=country)
                session.add(row)
            row.country = country
            row.canonical_name = str(city["canonical_name"])
            row.official_name = city.get("official_name")
            row.aliases = list(city.get("aliases") or [])
            row.grouping_basis = str(city.get("grouping_basis") or "official_city")
            row.admin_area_1 = city.get("admin_area_1")
            row.admin_area_2 = city.get("admin_area_2")
            row.latitude = latitude
            row.longitude = longitude
            row.source_authority = source.get("authority")
            row.source_url = source.get("url")
            row.source_date = source.get("date")
            row.source_hash = source_hash
            row.mapping_version = mapping_version
            row.active = True
            row.updated_at = datetime.now(UTC)

        session.execute(
            delete(CityLocalityMappingDB).where(
                CityLocalityMappingDB.country == country
            )
        )
        seen_localities: set[str] = set()
        locality_count = 0
        for city in country_config.get("cities") or []:
            source = city.get("source") or default_source
            values = [
                (city.get("canonical_name"), "canonical_exact"),
                *((alias, "canonical_exact") for alias in city.get("aliases") or []),
                *((locality, "official_crosswalk") for locality in city.get("localities") or []),
            ]
            for value, method in values:
                normalized = normalize_text(str(value or ""))
                if not normalized or normalized in seen_localities:
                    continue
                seen_localities.add(normalized)
                session.add(
                    CityLocalityMappingDB(
                        id=str(uuid5(NAMESPACE_URL, f"{country}|{normalized}")),
                        country=country,
                        locality_name=str(value),
                        locality_normalized=normalized,
                        city_id=str(city["city_id"]),
                        mapping_method=method,
                        admin_area_1=city.get("admin_area_1"),
                        admin_area_2=city.get("admin_area_2"),
                        source_authority=source.get("authority"),
                        source_url=source.get("url"),
                        source_date=source.get("date"),
                        source_hash=source_hash,
                        mapping_version=mapping_version,
                    )
                )
                locality_count += 1

        properties_updated = 0
        assignments_audited = 0
        properties_skipped = 0
        candidates_updated = 0
        for property_row in session.scalars(
            select(PropertyDB).where(PropertyDB.country == country)
        ).all():
            planned = planned_by_property.get(str(property_row.id))
            if planned is None:
                concurrent_property_ids.add(str(property_row.id))
                continue
            assignment = planned["city_assignment"]
            is_verified = assignment["mapping_status"] == "verified"
            if not is_verified and verified_only:
                properties_skipped += 1
            elif not is_verified:
                raise ValueError(f"Unresolved city assignment for {property_row.id}")
            if is_verified:
                property_row.city = planned["after_city"]
                property_row.city_id = assignment["city_id"]
                property_row.property_identity_key = planned["property_identity_key"]
                property_row.updated_at = datetime.now(UTC)
            else:
                property_row.city = assignment.get("source_city") or property_row.city
                property_row.city_id = None
                property_row.property_identity_key = planned["property_identity_key"]
                property_row.updated_at = datetime.now(UTC)
            assignment_row = session.get(PropertyCityAssignmentDB, str(property_row.id))
            if assignment_row is None:
                assignment_row = PropertyCityAssignmentDB(
                    property_id=str(property_row.id),
                    country=country,
                )
                session.add(assignment_row)
            for field, value in assignment.items():
                setattr(assignment_row, field, value)
            assignment_row.country = country
            assignment_row.updated_at = datetime.now(UTC)
            assignments_audited += 1
            candidate_rows = session.scalars(
                select(ScanCandidateDB).where(
                    ScanCandidateDB.property_id == str(property_row.id)
                )
            ).all()
            for candidate_row in candidate_rows:
                candidate_row.city = (
                    planned["after_city"]
                    if is_verified
                    else assignment.get("source_city") or property_row.city
                )
                candidates_updated += 1
            if not is_verified:
                continue
            properties_updated += 1
        session.commit()
    return {
        "properties_updated": properties_updated,
        "properties_skipped": properties_skipped,
        "concurrent_properties_skipped": len(concurrent_property_ids),
        "assignments_audited": assignments_audited,
        "scan_candidates_updated": candidates_updated,
        "canonical_units_upserted": len(country_config.get("cities") or []),
        "locality_mappings_rebuilt": locality_count,
    }


def _apply_overlay_plan(
    path: Path,
    *,
    country: str,
    config: dict,
    verified_only: bool = False,
) -> dict[str, int]:
    overlay = load_registry_overlay(path)
    result = apply_verified_city_assignments_to_overlay(
        overlay,
        country=country,
        config=config,
        verified_only=verified_only,
    )
    write_registry_overlay(path, overlay)
    return result


def _merge_sqlite_exact_identity_collisions(
    *,
    database_url: str,
    reports: list[dict],
    audit_path: Path,
) -> dict:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database:
        raise ValueError("Exact city-identity merge currently requires a SQLite source")
    database_path = Path(url.database).resolve()
    collision_groups = [
        {"country": report["country"], **group}
        for report in reports
        for group in report["identity_collisions"]
    ]
    if not collision_groups:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "database": str(database_path),
            "collision_group_count": 0,
            "removed_property_count": 0,
            "groups": [],
        }

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_root = Path("outputs").resolve()
    backup_root = (
        Path("outputs/backups")
        if audit_path.resolve().is_relative_to(output_root)
        else audit_path.parent
    )
    backup_path = backup_root / (
        f"{database_path.stem}.before_city_identity_merge_{timestamp}{database_path.suffix}"
    )
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    backup_connection = sqlite3.connect(backup_path)
    try:
        connection.backup(backup_connection)
    finally:
        backup_connection.close()

    audit_groups: list[dict] = []
    seen_property_ids: set[str] = set()
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        baseline_foreign_key_errors = {
            tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()
        }
        connection.execute("BEGIN IMMEDIATE")
        property_tables = _sqlite_property_tables(connection)
        for group in collision_groups:
            planned = list(group["properties"])
            property_ids = [str(row["property_id"]) for row in planned]
            if seen_property_ids.intersection(property_ids):
                raise ValueError("A property appears in more than one collision group")
            seen_property_ids.update(property_ids)
            rows = _validate_exact_collision_group(
                connection,
                planned=planned,
                max_distance_km=75.0,
            )
            keeper_id = _select_collision_keeper(
                connection,
                rows=rows,
                planned=planned,
                property_tables=property_tables,
            )
            merged_ids = [row_id for row_id in property_ids if row_id != keeper_id]
            moved_rows: defaultdict[str, int] = defaultdict(int)
            deduplicated_rows: defaultdict[str, int] = defaultdict(int)
            core_updates: list[dict] = []
            for duplicate_id in merged_ids:
                core_updates.append(
                    _merge_property_core_fields(
                        connection,
                        keeper_id=keeper_id,
                        duplicate_id=duplicate_id,
                    )
                )
                _merge_property_child_rows(
                    connection,
                    keeper_id=keeper_id,
                    duplicate_id=duplicate_id,
                    property_tables=property_tables,
                    moved_rows=moved_rows,
                    deduplicated_rows=deduplicated_rows,
                )
                connection.execute(
                    "DELETE FROM properties WHERE id = ?", (duplicate_id,)
                )
            audit_groups.append(
                {
                    "country": group["country"],
                    "identity_key": group["property_identity_key"],
                    "keeper_property_id": keeper_id,
                    "merged_property_ids": merged_ids,
                    "moved_rows": dict(sorted(moved_rows.items())),
                    "deduplicated_child_rows": dict(
                        sorted(deduplicated_rows.items())
                    ),
                    "core_updates": core_updates,
                }
            )
        foreign_key_errors = {
            tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()
        }
        new_foreign_key_errors = foreign_key_errors - baseline_foreign_key_errors
        if new_foreign_key_errors:
            raise ValueError(
                "New foreign-key errors after merge: "
                f"{sorted(new_foreign_key_errors)[:5]}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    audit = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": str(database_path),
        "backup": str(backup_path),
        "collision_group_count": len(collision_groups),
        "baseline_foreign_key_error_count": len(baseline_foreign_key_errors),
        "remaining_foreign_key_error_count": len(foreign_key_errors),
        "new_foreign_key_error_count": len(new_foreign_key_errors),
        "removed_property_count": sum(
            len(group["merged_property_ids"]) for group in audit_groups
        ),
        "groups": audit_groups,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(audit_path, audit)
    return audit


def _sqlite_property_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT m.name
            FROM sqlite_master AS m, pragma_table_info(m.name) AS p
            WHERE m.type = 'table' AND p.name = 'property_id'
            ORDER BY m.name
            """
        ).fetchall()
    ]


def _validate_exact_collision_group(
    connection: sqlite3.Connection,
    *,
    planned: list[dict],
    max_distance_km: float,
) -> dict[str, sqlite3.Row]:
    if len(planned) < 2:
        raise ValueError("Collision group must contain at least two properties")
    property_ids = [str(row["property_id"]) for row in planned]
    placeholders = ",".join("?" for _ in property_ids)
    rows = {
        str(row["id"]): row
        for row in connection.execute(
            f"SELECT * FROM properties WHERE id IN ({placeholders})", property_ids
        ).fetchall()
    }
    if set(rows) != set(property_ids):
        raise ValueError("Collision group references a missing property")
    if len({normalize_text(row["canonical_name"]) for row in rows.values()}) != 1:
        raise ValueError("Collision merge blocked: property names differ")
    if len({str(row["scene_type"]) for row in rows.values()}) != 1:
        raise ValueError("Collision merge blocked: scene types differ")
    city_ids = {
        str(row["city_assignment"].get("city_id") or "") for row in planned
    }
    statuses = {
        str(row["city_assignment"].get("mapping_status") or "") for row in planned
    }
    if len(city_ids) != 1 or "" in city_ids or statuses != {"verified"}:
        raise ValueError("Collision merge blocked: verified city_id is not identical")
    coordinates = [
        (float(row["latitude"]), float(row["longitude"])) for row in rows.values()
    ]
    if any(
        haversine_km(*first, *second) > max_distance_km
        for index, first in enumerate(coordinates)
        for second in coordinates[index + 1 :]
    ):
        raise ValueError("Collision merge blocked: coordinates exceed 75 km")
    return rows


def _select_collision_keeper(
    connection: sqlite3.Connection,
    *,
    rows: dict[str, sqlite3.Row],
    planned: list[dict],
    property_tables: list[str],
) -> str:
    planned_city_ids = {
        str(row["property_id"]): str(row["city_assignment"]["city_id"])
        for row in planned
    }

    def score(item: tuple[str, sqlite3.Row]) -> tuple[int, int, int, str]:
        property_id, row = item
        child_count = sum(
            int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE property_id = ?',
                    (property_id,),
                ).fetchone()[0]
            )
            for table in property_tables
            if not table.startswith("public_api_")
        )
        coordinate_score = _coordinate_status_score(row["coordinate_status"])
        current_city_match = int(
            str(row["city_id"] or "") == planned_city_ids[property_id]
        )
        return child_count, coordinate_score, current_city_match, property_id

    return max(rows.items(), key=score)[0]


def _coordinate_status_score(value: object) -> int:
    normalized = normalize_text(value)
    if normalized == "verified":
        return 4
    if normalized == "cross checked":
        return 3
    if normalized in {"geocoded", "approximate"}:
        return 2
    return 1


def _merge_property_core_fields(
    connection: sqlite3.Connection,
    *,
    keeper_id: str,
    duplicate_id: str,
) -> dict:
    keeper = connection.execute(
        "SELECT * FROM properties WHERE id = ?", (keeper_id,)
    ).fetchone()
    duplicate = connection.execute(
        "SELECT * FROM properties WHERE id = ?", (duplicate_id,)
    ).fetchone()
    if keeper is None or duplicate is None:
        raise ValueError("Property disappeared during collision merge")
    updates: dict[str, object] = {}
    if _coordinate_status_score(duplicate["coordinate_status"]) > _coordinate_status_score(
        keeper["coordinate_status"]
    ):
        for field in (
            "latitude",
            "longitude",
            "geocode_precision",
            "map_source",
            "map_source_date",
            "google_maps_link",
            "coordinate_status",
        ):
            updates[field] = duplicate[field]
    for field in ("hero_image", "google_maps_link"):
        if not keeper[field] and duplicate[field]:
            updates[field] = duplicate[field]
    if str(duplicate["created_at"]) < str(keeper["created_at"]):
        updates["created_at"] = duplicate["created_at"]
    if str(duplicate["updated_at"]) > str(keeper["updated_at"]):
        updates["updated_at"] = duplicate["updated_at"]
    if updates:
        assignments = ", ".join(f'"{field}" = ?' for field in updates)
        connection.execute(
            f"UPDATE properties SET {assignments} WHERE id = ?",
            [*updates.values(), keeper_id],
        )
    return {
        "duplicate_property_id": duplicate_id,
        "updated_fields": sorted(updates),
    }


def _merge_property_child_rows(
    connection: sqlite3.Connection,
    *,
    keeper_id: str,
    duplicate_id: str,
    property_tables: list[str],
    moved_rows: defaultdict[str, int],
    deduplicated_rows: defaultdict[str, int],
) -> None:
    public_tables = {table for table in property_tables if table.startswith("public_api_")}
    for table in public_tables:
        deleted = connection.execute(
            f'DELETE FROM "{table}" WHERE property_id = ?', (duplicate_id,)
        ).rowcount
        deduplicated_rows[table] += max(0, deleted)

    rollup_table = "property_network_performance_rollups"
    if rollup_table in property_tables:
        moved, deduplicated = _merge_generic_property_table(
            connection,
            table=rollup_table,
            keeper_id=keeper_id,
            duplicate_id=duplicate_id,
        )
        moved_rows[rollup_table] += moved
        deduplicated_rows[rollup_table] += deduplicated

    observation_table = "network_performance_observations"
    if observation_table in property_tables:
        moved, deduplicated = _merge_network_observations(
            connection,
            keeper_id=keeper_id,
            duplicate_id=duplicate_id,
        )
        moved_rows[observation_table] += moved
        deduplicated_rows[observation_table] += deduplicated

    skipped = public_tables | {rollup_table, observation_table}
    for table in property_tables:
        if table in skipped:
            continue
        moved, deduplicated = _merge_generic_property_table(
            connection,
            table=table,
            keeper_id=keeper_id,
            duplicate_id=duplicate_id,
        )
        moved_rows[table] += moved
        deduplicated_rows[table] += deduplicated


def _merge_network_observations(
    connection: sqlite3.Connection,
    *,
    keeper_id: str,
    duplicate_id: str,
) -> tuple[int, int]:
    moved = 0
    deduplicated = 0
    rows = connection.execute(
        """
        SELECT rowid, * FROM network_performance_observations
        WHERE property_id = ?
        """,
        (duplicate_id,),
    ).fetchall()
    for row in rows:
        existing = connection.execute(
            """
            SELECT id FROM network_performance_observations
            WHERE property_id = ? AND service_type = ? AND period = ?
            """,
            (keeper_id, row["service_type"], row["period"]),
        ).fetchone()
        if existing is None:
            connection.execute(
                "UPDATE network_performance_observations SET property_id = ? WHERE rowid = ?",
                (keeper_id, row["rowid"]),
            )
            moved += 1
            continue
        connection.execute(
            """
            UPDATE property_network_performance_rollups
            SET observation_id = ? WHERE observation_id = ?
            """,
            (existing["id"], row["id"]),
        )
        connection.execute(
            "DELETE FROM network_performance_observations WHERE rowid = ?",
            (row["rowid"],),
        )
        deduplicated += 1
    return moved, deduplicated


def _merge_generic_property_table(
    connection: sqlite3.Connection,
    *,
    table: str,
    keeper_id: str,
    duplicate_id: str,
) -> tuple[int, int]:
    rows = connection.execute(
        f'SELECT rowid, * FROM "{table}" WHERE property_id = ?',
        (duplicate_id,),
    ).fetchall()
    unique_indexes = _sqlite_unique_indexes(connection, table)
    moved = 0
    deduplicated = 0
    for row in rows:
        conflict = False
        for columns in unique_indexes:
            if "property_id" not in columns:
                continue
            other_columns = [column for column in columns if column != "property_id"]
            conditions = " AND ".join(
                ["property_id = ?", *[f'"{column}" IS ?' for column in other_columns]]
            )
            values = [keeper_id, *[row[column] for column in other_columns]]
            if connection.execute(
                f'SELECT 1 FROM "{table}" WHERE {conditions} LIMIT 1', values
            ).fetchone():
                conflict = True
                break
        if conflict:
            connection.execute(
                f'DELETE FROM "{table}" WHERE rowid = ?', (row["rowid"],)
            )
            deduplicated += 1
        else:
            connection.execute(
                f'UPDATE "{table}" SET property_id = ? WHERE rowid = ?',
                (keeper_id, row["rowid"]),
            )
            moved += 1
    return moved, deduplicated


def _sqlite_unique_indexes(
    connection: sqlite3.Connection, table: str
) -> list[list[str]]:
    result: list[list[str]] = []
    for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
        if not int(row["unique"]):
            continue
        result.append(
            [
                str(index_row["name"])
                for index_row in connection.execute(
                    f'PRAGMA index_info("{row["name"]}")'
                ).fetchall()
            ]
        )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply official city normalization."
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--country", action="append")
    scope.add_argument("--all-countries", action="store_true")
    parser.add_argument("--database-url")
    parser.add_argument("--overlay-path", type=Path, default=DEFAULT_OVERLAY_PATH)
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument(
        "--apply-verified",
        action="store_true",
        help=(
            "Persist verified assignments and unresolved audit rows, while keeping "
            "countries with unresolved items blocked from publication."
        ),
    )
    parser.add_argument(
        "--merge-exact-collisions",
        action="store_true",
        help=(
            "After a database backup, merge only collision groups with the same "
            "normalized property name, scene, verified city_id, and coordinates "
            "within 75 km; then rebuild the plan before apply. SQLite source only."
        ),
    )
    return parser.parse_args()


def _default_output_path(country: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = normalize_text(country).replace(" ", "_")
    return Path("outputs/city_normalization") / f"{slug}_{timestamp}.json"


def _overlay_backup_path(path: Path, country: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = normalize_text(country).replace(" ", "_")
    return path.with_name(f"{path.name}.before_{slug}_{timestamp}")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _report_summary(report: dict, output_path: Path) -> dict:
    return {
        "country": report["country"],
        "mode": report["mode"],
        "property_count": report["property_count"],
        "before_city_count": report["before_city_count"],
        "after_city_count": report["after_city_count"],
        "mapping_status_counts": report["mapping_status_counts"],
        "unresolved_count": report["unresolved_count"],
        "identity_collision_count": report["identity_collision_count"],
        "database_updates": report.get("database_updates"),
        "overlay_updates": report.get("overlay_updates"),
        "output": str(output_path),
    }


def _batch_summary(reports: list[dict], output_path: Path) -> dict:
    if len(reports) == 1:
        return _report_summary(reports[0], output_path)
    return {
        "mode": reports[0]["mode"],
        "country_count": len(reports),
        "property_count": sum(row["property_count"] for row in reports),
        "verified_count": sum(
            row["mapping_status_counts"].get("verified", 0) for row in reports
        ),
        "unresolved_count": sum(row["unresolved_count"] for row in reports),
        "identity_collision_count": sum(
            row["identity_collision_count"] for row in reports
        ),
        "publication_ready_country_count": sum(
            1 for row in reports if not row["unresolved_count"]
        ),
        "properties_updated": sum(
            (row.get("database_updates") or {}).get("properties_updated", 0)
            for row in reports
        ),
        "properties_skipped": sum(
            (row.get("database_updates") or {}).get("properties_skipped", 0)
            for row in reports
        ),
        "concurrent_properties_skipped": sum(
            (row.get("database_updates") or {}).get(
                "concurrent_properties_skipped", 0
            )
            for row in reports
        ),
        "output": str(output_path),
    }


def _redacted_database_url(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    return f"{database_url.split('://', 1)[0]}://***@{database_url.rsplit('@', 1)[1]}"


if __name__ == "__main__":
    raise SystemExit(main())
