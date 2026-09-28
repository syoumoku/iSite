from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import select

from isite2.db.models import PropertyCityAssignmentDB, PropertyDB
from isite2.growth.global_city_registry import build_unlocode_registry
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository
from isite2.rules.config_loader import load_yaml


def main() -> int:
    args = _parse_args()
    load_dotenv()
    database_url = args.database_url or os.getenv(
        "ISITE2_DATABASE_URL",
        os.getenv("DATABASE_URL", "sqlite+pysqlite:///outputs/isite2_dev.db"),
    )
    repository = SQLAlchemyScanRunRepository.from_url(database_url)
    curated = load_yaml("config/city_admin_sources.yaml")
    excluded = set((curated.get("countries") or {}).keys())

    with repository.session_factory() as session:
        existing_assignments = {
            str(row.property_id): row
            for row in session.scalars(select(PropertyCityAssignmentDB)).all()
        }
        properties = [
            {
                "id": str(row.id),
                "canonical_name": row.canonical_name,
                "country": row.country,
                "city": row.city,
                "scene_type": row.scene_type,
                "source_city": (
                    existing_assignments[str(row.id)].source_city
                    if str(row.id) in existing_assignments
                    else row.city
                ),
                "latitude": row.latitude,
                "longitude": row.longitude,
            }
            for row in session.scalars(
                select(PropertyDB).order_by(PropertyDB.country, PropertyDB.city)
            ).all()
        ]

    registry, audit = build_unlocode_registry(
        properties,
        locodes_dir=args.unlocode_root / "locodes",
        country_codes_path=args.unlocode_root / "iso-3166" / "CountryCodes.csv",
        subdivisions_path=args.unlocode_root / "vocab" / "unlocode-subdivisions.jsonld",
        excluded_countries=excluded,
        max_distance_km=args.max_distance_km,
        gns_root=args.gns_root,
        gns_country_files=_load_json_object(args.gns_country_map),
        gns_source_manifest=_load_json_object(args.gns_manifest),
    )
    generated_at = datetime.now(UTC).isoformat()
    registry["generated_at"] = generated_at
    registry["excluded_curated_countries"] = sorted(excluded)
    audit["generated_at"] = generated_at
    audit["database_url"] = _redacted_database_url(database_url)
    audit["excluded_curated_countries"] = sorted(excluded)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(
            registry,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    args.audit_output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "generated_country_count": audit["generated_country_count"],
                "ready_country_count": audit["ready_country_count"],
                "verified_property_count": audit["verified_property_count"],
                "unresolved_property_count": audit["unresolved_property_count"],
                "match_method_counts": audit["match_method_counts"],
                "unresolved_reason_counts": audit["unresolved_reason_counts"],
                "registry": str(args.output),
                "audit": str(args.audit_output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a compact global city registry from official UN/LOCODE names "
            "and coordinate-consistent iSite properties."
        )
    )
    parser.add_argument("--database-url")
    parser.add_argument("--unlocode-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/city_admin_sources.global.yaml"),
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path("outputs/city_normalization/global_registry_audit.json"),
    )
    parser.add_argument("--max-distance-km", type=float, default=75.0)
    parser.add_argument(
        "--gns-root",
        type=Path,
        help="Optional directory of official NGA GNS country ZIP files.",
    )
    parser.add_argument(
        "--gns-country-map",
        type=Path,
        help="JSON object mapping iSite country names to GNS ZIP filenames.",
    )
    parser.add_argument(
        "--gns-manifest",
        type=Path,
        help="Optional download manifest carrying archived URL and checksum metadata.",
    )
    return parser.parse_args()


def _load_json_object(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected JSON object in {path}")
    return payload


def _redacted_database_url(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    return f"{database_url.split('://', 1)[0]}://***@{database_url.rsplit('@', 1)[1]}"


if __name__ == "__main__":
    raise SystemExit(main())
