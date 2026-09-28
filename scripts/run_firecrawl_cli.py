from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.connectors.firecrawl import (
    CLOUD_FIRECRAWL_BASE_URL,
    LOCAL_FIRECRAWL_BASE_URL,
    PROXY_ENV_VARS,
    firecrawl_subprocess_env,
)

DEFAULT_SEARXNG_URL = "http://127.0.0.1:8080/search"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Firecrawl through the iSite2 project entrypoint. Local Docker "
            "Firecrawl is the default and consumes no cloud credits. Use "
            "--deployment cloud explicitly for the authenticated cloud CLI."
        )
    )
    parser.add_argument(
        "--deployment",
        choices=["local", "cloud"],
        default=os.getenv("ISITE2_FIRECRAWL_DEPLOYMENT", "local"),
        help="Firecrawl deployment. Project default: local.",
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
        print(
            json.dumps(
                _env_diagnostics(env, executable, args.deployment),
                ensure_ascii=False,
                indent=2,
            )
        )
        if not firecrawl_args:
            return 0

    if not firecrawl_args:
        parser.error("missing Firecrawl CLI arguments; example: -- search 'Kenya airport' --limit 3 --json")

    if args.deployment == "local":
        return _run_local(firecrawl_args, env)

    completed = subprocess.run([executable, *firecrawl_args], env=env, cwd=ROOT)
    return int(completed.returncode)


def _env_diagnostics(
    env: dict[str, str],
    executable: str,
    deployment: str,
) -> dict[str, object]:
    return {
        "deployment": deployment,
        "base_url": (
            env.get("FIRECRAWL_BASE_URL")
            or (
                LOCAL_FIRECRAWL_BASE_URL
                if deployment == "local"
                else CLOUD_FIRECRAWL_BASE_URL
            )
        ),
        "cloud_credits_expected": 0 if deployment == "local" else "metered",
        "firecrawl_executable": executable,
        "trust_env_proxy": os.getenv("FIRECRAWL_TRUST_ENV_PROXY", "false"),
        "proxy_vars_present_after_sanitize": {
            key: key in env for key in PROXY_ENV_VARS
        },
        "has_firecrawl_api_key_env": bool(env.get("FIRECRAWL_API_KEY")),
        "has_firecrawl_cli_credentials": _has_cli_credentials(env),
    }


def _run_local(args: list[str], env: dict[str, str]) -> int:
    command = args[0]
    command_args = args[1:]
    if command == "credit-usage":
        return _emit_local_payload(
            {
                "success": True,
                "data": {
                    "deployment": "local",
                    "remainingCredits": 2_147_483_647,
                    "creditsUsed": 0,
                },
            },
            _output_path(command_args),
        )
    if command not in {"search", "scrape", "batch-image-search"}:
        print(
            f"unsupported local Firecrawl command: {command}; use search, scrape, batch-image-search, or credit-usage",
            file=sys.stderr,
        )
        return 2

    positional = _first_positional(command_args)
    if not positional:
        print(f"missing {command} input", file=sys.stderr)
        return 2
    output_path = _output_path(command_args)
    base_url = env.get("FIRECRAWL_BASE_URL", LOCAL_FIRECRAWL_BASE_URL).rstrip("/")
    timeout = float(env.get("FIRECRAWL_TIMEOUT_MS", "30000")) / 1000
    if command == "batch-image-search":
        summary = _run_local_image_search_batch(
            Path(positional),
            limit=int(_option_value(command_args, "--limit") or 5),
            timeout=timeout,
            max_workers=int(_option_value(command_args, "--max-workers") or 4),
        )
        return _emit_local_payload(summary, output_path)
    payload: dict[str, Any]
    if command == "search":
        sources = _option_value(command_args, "--sources")
        if sources and "images" in {
            item.strip().casefold() for item in sources.split(",") if item.strip()
        }:
            return _run_local_image_search(
                positional,
                limit=int(_option_value(command_args, "--limit") or 5),
                output_path=output_path,
                timeout=timeout,
            )
        payload = {
            "query": positional,
            "limit": int(_option_value(command_args, "--limit") or 5),
        }
        if sources:
            payload["sources"] = [item.strip() for item in sources.split(",") if item.strip()]
        if "--scrape" in command_args:
            payload["scrapeOptions"] = {
                "formats": ["markdown"],
                "onlyMainContent": True,
                "removeBase64Images": True,
                "blockAds": True,
            }
    else:
        payload = {
            "url": positional,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "removeBase64Images": True,
            "blockAds": True,
        }

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            response = client.post(f"{base_url}/{command}", json=payload)
            response.raise_for_status()
            response_payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        response_payload = {
            "success": False,
            "deployment": "local",
            "cloudCreditsUsed": 0,
            "command": command,
            "input": positional,
            "error": str(exc),
        }
        _emit_local_payload(response_payload, output_path)
        return 1

    response_payload.setdefault("deployment", "local")
    response_payload["cloudCreditsUsed"] = 0
    return _emit_local_payload(response_payload, output_path)


def _run_local_image_search(
    query: str,
    *,
    limit: int,
    output_path: Path | None,
    timeout: float,
) -> int:
    return _emit_local_payload(
        _local_image_search_payload(query, limit=limit, timeout=timeout),
        output_path,
    )


def _local_image_search_payload(query: str, *, limit: int, timeout: float) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            response = client.get(
                DEFAULT_SEARXNG_URL,
                params={"q": query, "categories": "images", "format": "json"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "success": False,
            "deployment": "local",
            "cloudCreditsUsed": 0,
            "command": "search",
            "input": query,
            "error": str(exc),
        }
    return {
            "success": True,
            "deployment": "local",
            "cloudCreditsUsed": 0,
            "data": {"images": _normalize_searxng_image_results(payload, limit=limit)},
        }


def _run_local_image_search_batch(
    manifest_path: Path,
    *,
    limit: int,
    timeout: float,
    max_workers: int,
) -> dict[str, Any]:
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))

    def fetch(row: dict[str, Any]) -> dict[str, Any]:
        query = str(row["query"])
        output_path = Path(str(row["output_path"]))
        payload = _local_image_search_payload(query, limit=limit, timeout=timeout)
        _emit_local_payload(payload, output_path)
        return {
            "query": query,
            "output_path": str(output_path),
            "success": bool(payload.get("success")),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        results = list(executor.map(fetch, rows))
    return {
        "success": all(item["success"] for item in results),
        "deployment": "local",
        "cloudCreditsUsed": 0,
        "manifest_path": str(manifest_path),
        "query_count": len(rows),
        "completed_count": sum(1 for item in results if item["success"]),
        "failed_count": sum(1 for item in results if not item["success"]),
        "results": results,
    }


def _normalize_searxng_image_results(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    images: list[dict[str, Any]] = []
    for row in payload.get("results", []) or []:
        image_url = str(row.get("img_src") or "").strip()
        if not image_url.startswith(("http://", "https://")):
            continue
        width, height = _image_dimensions(str(row.get("resolution") or ""))
        images.append(
            {
                "imageUrl": image_url,
                "url": str(row.get("url") or "").strip(),
                "title": str(row.get("title") or "").strip(),
                "imageWidth": width,
                "imageHeight": height,
            }
        )
        if len(images) >= limit:
            break
    return images


def _image_dimensions(value: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*[x×]\s*(\d+)", value, re.IGNORECASE)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _first_positional(args: list[str]) -> str | None:
    options_with_values = {"--limit", "--sources", "-o", "--output"}
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def _option_value(args: list[str], name: str) -> str | None:
    try:
        index = args.index(name)
    except ValueError:
        return None
    return args[index + 1] if index + 1 < len(args) else None


def _output_path(args: list[str]) -> Path | None:
    value = _option_value(args, "-o") or _option_value(args, "--output")
    return Path(value) if value else None


def _emit_local_payload(payload: dict[str, Any], output_path: Path | None) -> int:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if output_path is None:
        print(rendered)
        return 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.casefold() == ".json":
        output_path.write_text(rendered + "\n", encoding="utf-8")
        return 0
    data = payload.get("data") if isinstance(payload, dict) else None
    markdown = data.get("markdown") if isinstance(data, dict) else None
    output_path.write_text(str(markdown or rendered) + "\n", encoding="utf-8")
    return 0


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
