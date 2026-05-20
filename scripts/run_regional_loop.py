from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.growth.evidence_intake import DEFAULT_DRAFT_PATH, DEFAULT_OVERLAY_PATH  # noqa: E402
from isite2.growth.regional_loop import (  # noqa: E402
    DEFAULT_BATCH_MAX,
    DEFAULT_BATCH_MIN,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REGIONS,
    DEFAULT_STATE_PATH,
    run_regional_scan_round,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one Africa + Latin America high-value building scan batch."
    )
    parser.add_argument("--state-path", type=Path, default=ROOT / DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=ROOT / DEFAULT_OUTPUT_DIR)
    parser.add_argument("--regions", nargs="+", default=DEFAULT_REGIONS)
    parser.add_argument("--countries", nargs="+", default=None)
    parser.add_argument("--batch-min", type=int, default=DEFAULT_BATCH_MIN)
    parser.add_argument("--batch-max", type=int, default=DEFAULT_BATCH_MAX)
    parser.add_argument("--overlay-path", type=Path, default=ROOT / DEFAULT_OVERLAY_PATH)
    parser.add_argument("--draft-path", type=Path, default=ROOT / DEFAULT_DRAFT_PATH)
    parser.add_argument("--max-searches-per-cycle", type=int, default=20)
    parser.add_argument("--max-fetches-per-cycle", type=int, default=40)
    parser.add_argument(
        "--force-scan-existing-pool",
        action="store_true",
        help=(
            "Create a scan from the existing registry-backed pool even when this intake pass "
            "finds no new or changed evidence."
        ),
    )
    args = parser.parse_args()

    round_result = run_regional_scan_round(
        state_path=args.state_path,
        output_dir=args.output_dir,
        regions=args.regions,
        batch_min=args.batch_min,
        batch_max=args.batch_max,
        overlay_path=args.overlay_path,
        draft_path=args.draft_path,
        target_countries=args.countries,
        max_searches_per_cycle=args.max_searches_per_cycle,
        max_fetches_per_cycle=args.max_fetches_per_cycle,
        force_scan_existing_pool=args.force_scan_existing_pool,
    )
    print(f"Round: {round_result.round_number}")
    print(f"Mode: {round_result.mode}")
    print(f"Regions: {', '.join(round_result.regions)}")
    print(f"Countries: {', '.join(round_result.countries) or 'None'}")
    print(f"Run ID: {round_result.run_id}")
    print(f"Candidates: {round_result.candidate_count}")
    print(f"Review items: {round_result.review_count}")
    print(f"Storage mode: {round_result.storage_mode}")
    print(f"Report: {round_result.report_path}")
    print(f"Summary: {round_result.summary_path}")
    print(f"Excel: {round_result.excel_path}")
    print(f"UI map path: {round_result.ui_paths['map']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
