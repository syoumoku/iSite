#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists() and str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from isite2.growth.localization_refresh import (
    DEFAULT_LOCALIZATION_TEXT_KINDS,
    refresh_localization_cache_for_packets,
)
from isite2.repositories import get_default_repository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-warm cached UI/export localizations without changing raw data."
    )
    parser.add_argument("--locale", action="append", help="Target locale. Repeatable.")
    parser.add_argument("--country", help="Limit to one country.")
    parser.add_argument("--scan-run-id", help="Limit to one scan run.")
    parser.add_argument("--property-id", action="append", default=[], help="Limit to property id.")
    parser.add_argument("--surface", default="all", choices=["all", "ui", "excel", "ppt"])
    parser.add_argument(
        "--text-kind",
        action="append",
        help=(
            "Free-text kind to refresh. Repeatable. Defaults to conclusion/review "
            f"texts: {', '.join(DEFAULT_LOCALIZATION_TEXT_KINDS)}"
        ),
    )
    parser.add_argument(
        "--provider",
        default="codex-oauth",
        choices=["codex-oauth", "auto", "noop"],
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum text candidates to process.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Maximum text candidates per GPT localization batch. Defaults to env or 20.",
    )
    parser.add_argument(
        "--batch-concurrency",
        type=int,
        default=1,
        help="Number of independent localization batches translated concurrently.",
    )
    parser.add_argument(
        "--skip-single-fallback",
        action="store_true",
        help="Retain batch failures for a later scoped retry instead of retrying items serially.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh matching cached rows instead of treating them as cache hits.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repository = get_default_repository()
    engine = getattr(repository, "engine", None)
    if engine is None:
        raise SystemExit("default repository does not expose a SQLAlchemy engine")

    filters = {}
    if args.country:
        filters["country"] = args.country
    if args.scan_run_id:
        filters["scan_run_id"] = args.scan_run_id
    packets = repository.list_properties(filters)
    if args.property_id:
        wanted = {str(value) for value in args.property_id}
        packets = [packet for packet in packets if str(packet.entity.property_id) in wanted]

    summary = refresh_localization_cache_for_packets(
        packets,
        engine,
        locales=args.locale,
        text_kinds=args.text_kind,
        provider_mode=args.provider,
        batch_size=args.batch_size,
        batch_concurrency=args.batch_concurrency,
        single_fallback=not args.skip_single_fallback,
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
    )
    summary["surface"] = args.surface
    summary["property_count"] = len(packets)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
