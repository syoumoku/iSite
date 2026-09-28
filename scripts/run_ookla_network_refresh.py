from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.growth.ookla_refresh import (  # noqa: E402
    refresh_ookla_from_artifact,
    refresh_ookla_from_parquet,
    refresh_ookla_radius_tiles_from_arcgis_feature_layer,
    refresh_ookla_radius_tiles_from_parquet,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh internal Ookla z16 property proxies.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--artifact", type=Path)
    source.add_argument("--parquet-url")
    source.add_argument(
        "--arcgis-feature-layer-url",
        help="Public ArcGIS FeatureServer layer for Ookla mobile tiles.",
    )
    parser.add_argument("--period")
    parser.add_argument("--service-type", choices=["mobile", "fixed"])
    parser.add_argument("--country")
    parser.add_argument("--source-checksum")
    parser.add_argument(
        "--radius-tiles",
        action="store_true",
        help="Persist all z16 Ookla tiles within --radius-m of country properties.",
    )
    parser.add_argument("--radius-m", type=int, default=5000)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("ISITE2_DATABASE_URL"),
    )
    parser.add_argument(
        "--public-enabled",
        action="store_true",
        help="Requires a completed license/product-use review; default is internal only.",
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    repository = SQLAlchemyScanRunRepository.from_url(args.database_url)
    if args.artifact:
        if args.radius_tiles:
            raise SystemExit("--radius-tiles is only supported with --parquet-url")
        result = refresh_ookla_from_artifact(
            repository.engine,
            args.artifact,
            public_enabled=args.public_enabled,
        )
    elif args.parquet_url:
        if not args.period or not args.service_type:
            raise SystemExit("--period and --service-type are required with --parquet-url")
        if args.radius_tiles and not args.country:
            raise SystemExit("--country is required with --radius-tiles")
        if not args.source_checksum and not Path(args.parquet_url).is_file():
            raise SystemExit(
                "--source-checksum is required for remote Parquet "
                "(official checksum or object ETag)"
            )
        if args.radius_tiles:
            result = refresh_ookla_radius_tiles_from_parquet(
                repository.engine,
                period=args.period,
                service_type=args.service_type,
                parquet_url=args.parquet_url,
                country=args.country,
                radius_m=args.radius_m,
                source_checksum=args.source_checksum,
            )
        else:
            result = refresh_ookla_from_parquet(
                repository.engine,
                period=args.period,
                service_type=args.service_type,
                parquet_url=args.parquet_url,
                country=args.country,
                source_checksum=args.source_checksum,
            )
    else:
        if not args.radius_tiles:
            raise SystemExit("--arcgis-feature-layer-url currently requires --radius-tiles")
        if args.service_type and args.service_type != "mobile":
            raise SystemExit("--arcgis-feature-layer-url currently supports mobile only")
        if not args.country:
            raise SystemExit("--country is required with --arcgis-feature-layer-url")
        result = refresh_ookla_radius_tiles_from_arcgis_feature_layer(
            repository.engine,
            feature_layer_url=args.arcgis_feature_layer_url,
            country=args.country,
            radius_m=args.radius_m,
            period=args.period,
            source_checksum=args.source_checksum,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
