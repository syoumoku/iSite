from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.growth.evidence_curation import run_pending_evidence_curation  # noqa: E402
from isite2.growth.evidence_discovery import discover_public_evidence  # noqa: E402
from isite2.growth.evidence_intake import DEFAULT_DRAFT_PATH, DEFAULT_OVERLAY_PATH  # noqa: E402
from isite2.growth.evidence_store import EvidenceCurationStore  # noqa: E402
from isite2.growth.regional_loop import (  # noqa: E402
    DEFAULT_BATCH_MAX,
    DEFAULT_BATCH_MIN,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REGIONS,
    DEFAULT_STATE_PATH,
    run_regional_scan_round,
)
from isite2.growth.regional_targets import countries_for_regions  # noqa: E402
from isite2.repositories import get_default_repository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Continuously keep discovery, curation, and scan checks alive."
    )
    parser.add_argument("--state-path", type=Path, default=ROOT / DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=ROOT / DEFAULT_OUTPUT_DIR)
    parser.add_argument("--regions", nargs="+", default=DEFAULT_REGIONS)
    parser.add_argument("--batch-min", type=int, default=DEFAULT_BATCH_MIN)
    parser.add_argument("--batch-max", type=int, default=DEFAULT_BATCH_MAX)
    parser.add_argument("--overlay-path", type=Path, default=ROOT / DEFAULT_OVERLAY_PATH)
    parser.add_argument("--draft-path", type=Path, default=ROOT / DEFAULT_DRAFT_PATH)
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--max-searches-per-cycle", type=int, default=20)
    parser.add_argument("--max-fetches-per-cycle", type=int, default=40)
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=10.0,
        help=(
            "Lightweight daemon poll delay after each completed round. "
            "Business triggering still comes from new raw_evidence_items."
        ),
    )
    parser.add_argument(
        "--stop-on-underfilled-cycle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Stop after an underfilled full cycle over the evidence registry. "
            "Disable only when a discovery worker is expanding source_registry concurrently."
        ),
    )
    parser.add_argument(
        "--stop-on-static-cycle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Stop after one complete cycle over a static evidence registry. Rounds inside the "
            "cycle still run back-to-back with no delay."
        ),
    )
    parser.add_argument(
        "--stop-on-exhausted",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Stop when every target country/scene/source progress row is exhausted and no "
            "raw evidence is pending curation. Daemon launch disables this to keep monitoring."
        ),
    )
    args = parser.parse_args()

    completed = 0
    initial_cycles_completed = _cycles_completed(args.state_path)
    repository = get_default_repository()
    store = EvidenceCurationStore.from_repository(repository)
    while True:
        discovery_result = discover_public_evidence(
            regions=list(args.regions),
            store=store,
            provider=None,
            output_dir=args.output_dir,
            max_searches_per_cycle=args.max_searches_per_cycle,
            max_fetches_per_cycle=args.max_fetches_per_cycle,
        )

        curation_result = None
        progress_group_stats = {}
        if discovery_result.curation_needed or store.list_pending_evidence(limit=1):
            # Drain the pending evidence queue immediately.
            while True:
                next_curation_result = run_pending_evidence_curation(
                    store=store,
                    output_dir=args.output_dir,
                    overlay_path=args.overlay_path,
                    draft_path=args.draft_path,
                )
                if next_curation_result is None:
                    break
                curation_result = next_curation_result
                progress_group_stats.update(_progress_group_stats(curation_result))
        progress_settlement = store.settle_completed_progress_groups(progress_group_stats)

        # Only trigger scan batches when new/changed evidence produced accepted or updated
        # registry-backed candidates. This keeps scan triggering evidence-driven instead
        # of time-interval or backlog heuristics.
        should_scan = bool(
            curation_result
            and (curation_result.accepted_count > 0 or curation_result.updated_count > 0)
        )
        if should_scan:
            round_result = run_regional_scan_round(
                repository=repository,
                state_path=args.state_path,
                output_dir=args.output_dir,
                regions=list(args.regions),
                batch_min=args.batch_min,
                batch_max=args.batch_max,
                overlay_path=args.overlay_path,
                draft_path=args.draft_path,
                enable_intake=False,
            )
            completed += 1
            print(f"Round: {round_result.round_number}", flush=True)
            print(f"Mode: {round_result.mode}", flush=True)
            print(f"Regions: {', '.join(round_result.regions)}", flush=True)
            print(f"Countries: {', '.join(round_result.countries) or 'None'}", flush=True)
            print(f"Run ID: {round_result.run_id}", flush=True)
            print(f"Candidates: {round_result.candidate_count}", flush=True)
            print(f"Review items: {round_result.review_count}", flush=True)
            print(f"Report: {round_result.report_path}", flush=True)
            print(f"Summary: {round_result.summary_path}", flush=True)
            print(f"Excel: {round_result.excel_path}", flush=True)
            print(
                "Progress: "
                f"settled={len(progress_settlement.settled_groups)} "
                f"exhausted={len(progress_settlement.exhausted_groups)} "
                f"blocked={len(progress_settlement.blocked_groups)}",
                flush=True,
            )
        else:
            print("Round: (skipped)", flush=True)
            print("Mode: intake_only", flush=True)
            print(f"Regions: {', '.join(args.regions)}", flush=True)
            print(
                "Intake: "
                f"discovered={discovery_result.discovered_count} "
                f"new={discovery_result.new_count} "
                f"changed={discovery_result.changed_count} "
                f"duplicates_suppressed={discovery_result.duplicate_unchanged_count}",
                flush=True,
            )
            if curation_result:
                print(
                    "Curation: "
                    f"new_evidence={curation_result.new_evidence_count} "
                    f"accepted={curation_result.accepted_count} "
                    f"updated={curation_result.updated_count} "
                    f"rejected={curation_result.rejected_count}",
                    flush=True,
                )
                print(f"Curation report: {curation_result.report_path}", flush=True)
            print(
                "Progress: "
                f"settled={len(progress_settlement.settled_groups)} "
                f"exhausted={len(progress_settlement.exhausted_groups)} "
                f"blocked={len(progress_settlement.blocked_groups)}",
                flush=True,
            )

        if args.max_rounds and completed >= args.max_rounds:
            return 0
        if args.stop_on_underfilled_cycle and _underfilled_cycle(args.state_path, args.batch_min):
            print(
                "Stopping continuous runner: evidence registry completed a full underfilled "
                "cycle. Expand source_registry before restarting.",
                flush=True,
            )
            return 0
        if (
            args.stop_on_static_cycle
            and _cycles_completed(args.state_path) > initial_cycles_completed
        ):
            print(
                "Stopping continuous runner: completed one full pass over the current evidence "
                "registry with no per-round delay.",
                flush=True,
            )
            return 0
        if (
            args.stop_on_exhausted
            and store.all_target_progress_exhausted(countries_for_regions(list(args.regions)))
            and not store.list_pending_evidence(limit=1)
        ):
            print(
                "Stopping continuous runner: every target discovery progress group is exhausted "
                "and no raw evidence is pending curation.",
                flush=True,
            )
            return 0
        if args.poll_seconds > 0:
            time.sleep(args.poll_seconds)


def _underfilled_cycle(state_path: Path, batch_min: int) -> bool:
    import json

    state = json.loads(state_path.read_text(encoding="utf-8"))
    history = state.get("history", [])
    if int(state.get("cycles_completed", 0)) < 1 or not history:
        return False
    latest = history[-1]
    registry_total = int(state.get("registry_backed_candidate_count", 0))
    return registry_total < batch_min and int(latest.get("candidate_count", 0)) < batch_min


def _cycles_completed(state_path: Path) -> int:
    if not state_path.exists():
        return 0
    import json

    state = json.loads(state_path.read_text(encoding="utf-8"))
    return int(state.get("cycles_completed", 0))


def _progress_group_stats(curation_result) -> dict[tuple[str, str, str, str], dict[str, int]]:
    stats: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for group in curation_result.progress_groups:
        stats[
            (
                str(group.get("region", "")).casefold(),
                str(group.get("country", "")).casefold(),
                str(group.get("scene_type", "")).casefold(),
                str(group.get("source_type", "")).casefold(),
            )
        ] = {
            "accepted_new_count": int(group.get("accepted_new_count", 0)),
            "updated_count": int(group.get("updated_count", 0)),
            "draft_review_count": int(group.get("draft_review_count", 0)),
        }
    return stats


if __name__ == "__main__":
    raise SystemExit(main())
