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

from isite2.growth.complaint_refresh import (  # noqa: E402
    CodexOAuthComplaintClassifier,
    FileCachedComplaintClassifier,
    refresh_property_complaints,
)
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest a retained complaint search/scrape artifact. Web acquisition must "
            "use scripts/run_firecrawl_cli.py and retain its manifest before this step."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("ISITE2_DATABASE_URL"),
    )
    parser.add_argument(
        "--source-policy",
        type=Path,
        default=ROOT / "config" / "complaint_sources.yaml",
    )
    parser.add_argument(
        "--provider",
        choices=["rule", "codex-oauth"],
        default=os.getenv("ISITE2_COMPLAINT_PROVIDER", "codex-oauth"),
        help="GPT is called only for records marked ambiguous_network_candidate=true.",
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    if not args.manifest.exists():
        raise SystemExit("A retained search/scrape manifest is required")

    records = _load_records(args.input)
    repository = SQLAlchemyScanRunRepository.from_url(args.database_url)
    ambiguous_classifier = (
        FileCachedComplaintClassifier(CodexOAuthComplaintClassifier())
        if args.provider == "codex-oauth"
        else None
    )
    summary = refresh_property_complaints(
        repository.engine,
        records,
        source_policy_path=args.source_policy,
        source_manifest_path=str(args.manifest),
        ambiguous_classifier=ambiguous_classifier,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _load_records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.casefold() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    return list(payload.get("records") or payload.get("results") or [])


if __name__ == "__main__":
    raise SystemExit(main())
