from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.connectors.firecrawl import PROXY_ENV_VARS, firecrawl_subprocess_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Firecrawl CLI with the iSite2 automation-safe environment. "
            "By default this strips inherited local proxy variables such as "
            "HTTP_PROXY=http://127.0.0.1:7890 unless FIRECRAWL_TRUST_ENV_PROXY=1."
        )
    )
    parser.add_argument(
        "--print-env",
        action="store_true",
        help="Print sanitized Firecrawl environment diagnostics and exit if no CLI args follow.",
    )
    parser.add_argument(
        "firecrawl_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to firecrawl. Use '--' before Firecrawl options.",
    )
    args = parser.parse_args(argv)

    firecrawl_args = list(args.firecrawl_args)
    if firecrawl_args and firecrawl_args[0] == "--":
        firecrawl_args = firecrawl_args[1:]

    env = firecrawl_subprocess_env()
    executable = env.get("FIRECRAWL_BIN") or "firecrawl"

    if args.print_env:
        print(json.dumps(_env_diagnostics(env, executable), ensure_ascii=False, indent=2))
        if not firecrawl_args:
            return 0

    if not firecrawl_args:
        parser.error("missing Firecrawl CLI arguments; example: -- search 'Kenya airport' --limit 3 --json")

    completed = subprocess.run([executable, *firecrawl_args], env=env, cwd=ROOT)
    return int(completed.returncode)


def _env_diagnostics(env: dict[str, str], executable: str) -> dict[str, object]:
    return {
        "firecrawl_executable": executable,
        "trust_env_proxy": os.getenv("FIRECRAWL_TRUST_ENV_PROXY", "false"),
        "proxy_vars_present_after_sanitize": {
            key: key in env for key in PROXY_ENV_VARS
        },
        "has_firecrawl_api_key_env": bool(env.get("FIRECRAWL_API_KEY")),
        "has_firecrawl_cli_credentials": _has_cli_credentials(env),
    }


def _has_cli_credentials(env: dict[str, str]) -> bool:
    credentials_path = env.get("FIRECRAWL_CLI_CREDENTIALS_PATH")
    path = (
        Path(credentials_path)
        if credentials_path
        else Path.home() / "Library" / "Application Support" / "firecrawl-cli" / "credentials.json"
    )
    return path.exists()


if __name__ == "__main__":
    raise SystemExit(main())
