from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.growth.africa_loop import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DEFAULT_STATE_PATH,
    run_africa_scan_round,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Africa high-value building scan round.")
    parser.add_argument("--state-path", type=Path, default=ROOT / DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=ROOT / DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    round_result = run_africa_scan_round(
        state_path=args.state_path,
        output_dir=args.output_dir,
    )
    print(f"Round: {round_result.round_number}")
    print(f"Country: {round_result.country}")
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
