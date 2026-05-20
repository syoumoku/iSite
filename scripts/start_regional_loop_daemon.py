from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = ROOT / "outputs" / "regional_scan_loop"
DEFAULT_PID_PATH = DEFAULT_RUN_DIR / "continuous_runner.pid"
DEFAULT_LOG_PATH = DEFAULT_RUN_DIR / "continuous_runner.log"


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the iSite2 regional loop daemon.")
    parser.add_argument("--pid-path", type=Path, default=DEFAULT_PID_PATH)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--batch-min", type=int, default=30)
    parser.add_argument("--batch-max", type=int, default=50)
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--max-searches-per-cycle", type=int, default=20)
    parser.add_argument("--max-fetches-per-cycle", type=int, default=40)
    args = parser.parse_args()

    if _pid_is_running(args.pid_path):
        print(f"Regional loop daemon already running: pid={args.pid_path.read_text().strip()}")
        return 0

    args.pid_path.parent.mkdir(parents=True, exist_ok=True)
    args.log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_continuous_regional_loop.py"),
        "--batch-min",
        str(args.batch_min),
        "--batch-max",
        str(args.batch_max),
        "--no-stop-on-underfilled-cycle",
        "--no-stop-on-static-cycle",
        "--no-stop-on-exhausted",
        "--poll-seconds",
        str(args.poll_seconds),
        "--max-searches-per-cycle",
        str(args.max_searches_per_cycle),
        "--max-fetches-per-cycle",
        str(args.max_fetches_per_cycle),
    ]
    if args.max_rounds:
        command.extend(["--max-rounds", str(args.max_rounds)])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    with args.log_path.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    args.pid_path.write_text(str(process.pid) + "\n", encoding="utf-8")
    print(f"Started regional loop daemon: pid={process.pid}")
    print(f"Log: {args.log_path}")
    return 0


def _pid_is_running(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        # In sandboxed environments, signaling may be blocked even when the process exists.
        # Treat a persisted PID as "running" to avoid starting duplicate daemons.
        return True
    except OSError:
        return False
    return _same_process_group_alive(pid)


def _same_process_group_alive(pid: int) -> bool:
    try:
        os.killpg(os.getpgid(pid), 0)
    except OSError:
        return False
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    raise SystemExit(main())
