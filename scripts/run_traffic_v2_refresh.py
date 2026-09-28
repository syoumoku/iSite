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

from isite2.growth.traffic_refresh import (  # noqa: E402
    refresh_traffic_estimates_v2,
    rollback_traffic_estimates_v2,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh iSite2 Traffic V2 estimates.")
    parser.add_argument("--scan-run-id")
    parser.add_argument("--country")
    parser.add_argument("--property-id", action="append", dest="property_ids")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--activation-mode",
        choices=("direct_only", "shadow", "all"),
        help="Control whether V2 replaces legacy P50 fields.",
    )
    parser.add_argument(
        "--rollback-v1",
        action="store_true",
        help="Restore legacy scene and demand fields from pre-V2 snapshots.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("ISITE2_DATABASE_URL"),
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    repository = SQLAlchemyScanRunRepository.from_url(args.database_url)
    if args.rollback_v1:
        summary = rollback_traffic_estimates_v2(
            repository.engine,
            scan_run_id=args.scan_run_id,
            country=args.country,
            property_ids=args.property_ids,
        )
    else:
        summary = refresh_traffic_estimates_v2(
            repository.engine,
            scan_run_id=args.scan_run_id,
            country=args.country,
            property_ids=args.property_ids,
            force=args.force,
            activation_mode=args.activation_mode,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
