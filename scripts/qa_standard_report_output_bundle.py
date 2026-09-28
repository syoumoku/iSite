from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from qa_standard_report_ppt_metrics import (
    _excel_recommendations,
    _ppt_recommendation_rows,
    validate_excel_metrics,
    validate_ppt_metrics,
)


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from isite2.rules.config_loader import load_output_template


def resolve_qa_lessons_path(root: Path = ROOT) -> Path:
    canonical = root / "docs" / "15_qa_lessons_learned.md"
    if canonical.exists():
        return canonical
    archived = sorted((root / "docs" / "qa_archive").glob("*.md"))
    return archived[-1] if archived else canonical


DEFAULT_QA_LESSONS = resolve_qa_lessons_path()
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_REASONING_EFFORT = "low"
SUPPORTED_LOCALES = ("zh", "en")
ARTIFACT_MODES = ("all", "excel-only")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit a standard iSite2 report directory after Excel/PPT generation. "
            "The audit first runs deterministic PPT-vs-Excel checks, then runs a "
            "Codex OAuth GPT audit unless explicitly disabled."
        )
    )
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--locale", action="append", choices=SUPPORTED_LOCALES)
    parser.add_argument("--artifact-mode", choices=ARTIFACT_MODES, default="all")
    parser.add_argument(
        "--gpt-provider",
        choices=["codex-oauth", "off"],
        default="codex-oauth",
        help="Default is codex-oauth because standard report delivery requires GPT audit.",
    )
    parser.add_argument("--codex-bin", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--qa-lessons", type=Path, default=DEFAULT_QA_LESSONS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = audit_report_dir(
        args.report_dir,
        country=args.country,
        locales=tuple(args.locale or SUPPORTED_LOCALES),
        gpt_provider=args.gpt_provider,
        codex_bin=args.codex_bin,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        qa_lessons=args.qa_lessons,
        artifact_mode=args.artifact_mode,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["status"] == "pass" else 1


def audit_report_dir(
    report_dir: Path,
    *,
    country: str,
    locales: tuple[str, ...] = SUPPORTED_LOCALES,
    gpt_provider: str = "codex-oauth",
    codex_bin: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    qa_lessons: Path = DEFAULT_QA_LESSONS,
    artifact_mode: str = "all",
) -> dict[str, Any]:
    batch = audit_report_dirs(
        {country: report_dir},
        locales=locales,
        gpt_provider=gpt_provider,
        codex_bin=codex_bin,
        model=model,
        reasoning_effort=reasoning_effort,
        qa_lessons=qa_lessons,
        artifact_mode=artifact_mode,
    )
    country_result = batch["countries"][country]
    return {
        "country": country,
        "report_dir": str(report_dir.resolve()),
        "status": country_result["status"],
        "rule_audits": country_result["rule_audits"],
        "gpt_audit": batch["gpt_audit"],
    }


def audit_report_dirs(
    report_dirs: dict[str, Path],
    *,
    locales: tuple[str, ...] = SUPPORTED_LOCALES,
    gpt_provider: str = "codex-oauth",
    codex_bin: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    qa_lessons: Path = DEFAULT_QA_LESSONS,
    artifact_mode: str = "all",
) -> dict[str, Any]:
    """Audit a batch with one deterministic pass and one shared GPT call."""
    country_results: dict[str, dict[str, Any]] = {}
    batch_gpt_inputs: dict[str, Any] = {}
    deterministic_failures: list[dict[str, Any]] = []
    for country, raw_report_dir in report_dirs.items():
        report_dir = raw_report_dir.resolve()
        rule_audits, gpt_inputs = _rule_audit_inputs(
            report_dir,
            locales,
            country=country,
            artifact_mode=artifact_mode,
        )
        country_failures = [
            {"country": country, "locale": locale, **failure}
            for locale, audit in rule_audits.items()
            for failure in audit.get("failures", [])
        ]
        deterministic_failures.extend(country_failures)
        country_results[country] = {
            "report_dir": str(report_dir),
            "status": "fail" if country_failures else "pass",
            "rule_audits": rule_audits,
        }
        batch_gpt_inputs[country] = {
            "report_dir": str(report_dir),
            "artifact_mode": artifact_mode,
            "locale_outputs": gpt_inputs,
        }

    gpt_audit = None
    gpt_blocking_issues: list[dict[str, Any]] = []
    if gpt_provider == "codex-oauth":
        gpt_audit = _run_codex_gpt_audit(
            {
                "countries": batch_gpt_inputs,
                "locales": list(locales),
                "artifact_mode": artifact_mode,
                "deterministic_failures": deterministic_failures,
                "qa_lessons_excerpt": _qa_lessons_excerpt(qa_lessons),
                "configured_recommendation_gates": (
                    load_output_template()
                    .get("excel", {})
                    .get("recommendation_rules", {})
                    .get("default_scene_gates", {})
                ),
            },
            codex_bin=codex_bin,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        gpt_blocking_issues = list(gpt_audit.get("blocking_issues") or [])

    status = "pass"
    if deterministic_failures or gpt_blocking_issues:
        status = "fail"
    if gpt_audit and str(gpt_audit.get("verdict") or "").casefold() != "pass":
        status = "fail"
    if status == "fail":
        for result in country_results.values():
            if result["status"] == "pass" and gpt_blocking_issues:
                result["status"] = "fail"
    return {
        "status": status,
        "countries": country_results,
        "deterministic_failures": deterministic_failures,
        "gpt_audit": gpt_audit,
    }


def _rule_audit_inputs(
    report_dir: Path,
    locales: tuple[str, ...],
    *,
    country: str,
    artifact_mode: str = "all",
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    rule_audits: dict[str, dict[str, Any]] = {}
    gpt_inputs: dict[str, Any] = {}

    for locale in locales:
        xlsx = _find_required(report_dir, f"*standard_report_{locale}_*.xlsx")
        if artifact_mode == "excel-only":
            rule_result = validate_excel_metrics(
                xlsx,
                locale,
                expected_country=country,
            )
            pptx = None
            displayed_recommendations: list[dict[str, Any]] = []
        else:
            pptx = _find_required(report_dir, f"*standard_ppt_{locale}_*.pptx")
            rule_result = validate_ppt_metrics(pptx, xlsx, locale)
            displayed_recommendations = _ppt_recommendation_rows(pptx, locale)
        rule_audits[locale] = rule_result
        gpt_inputs[locale] = {
            "pptx": str(pptx) if pptx else None,
            "xlsx": str(xlsx),
            "rule_result": rule_result,
            "ppt_displayed_recommendations": displayed_recommendations,
            "excel_recommendations": list(_excel_recommendations(xlsx, locale).values()),
        }
    return rule_audits, gpt_inputs


def _find_required(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected exactly one file matching {pattern!r} in {directory}, found {len(matches)}"
        )
    return matches[0]


def _run_codex_gpt_audit(
    request: dict[str, Any],
    *,
    codex_bin: str | None,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    resolved_codex = (
        codex_bin
        or os.getenv("ISITE2_CODEX_BIN")
        or shutil.which("codex")
        or "/Applications/Codex.app/Contents/Resources/codex"
    )
    if not Path(resolved_codex).exists() and shutil.which(resolved_codex) is None:
        raise RuntimeError(f"codex executable not found: {resolved_codex}")
    if not (Path.home() / ".codex" / "auth.json").exists():
        raise RuntimeError("Codex OAuth auth not found; standard report GPT audit cannot run")

    with tempfile.TemporaryDirectory(prefix="isite2_report_output_audit_") as directory:
        temp_dir = Path(directory)
        schema_path = temp_dir / "schema.json"
        output_path = temp_dir / "report_output_audit.json"
        schema_path.write_text(json.dumps(_GPT_AUDIT_SCHEMA, indent=2), encoding="utf-8")
        prompt = "\n\n".join(
            [
                _GPT_AUDIT_SYSTEM_PROMPT,
                "Return only a JSON object matching the provided schema.",
                json.dumps(request, ensure_ascii=False, sort_keys=True),
            ]
        )
        command = [
            resolved_codex,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-rules",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "-m",
            model
            or os.getenv("ISITE2_REPORT_QA_CODEX_MODEL")
            or os.getenv("ISITE2_CODEX_OAUTH_MODEL")
            or DEFAULT_MODEL,
            "-c",
            f"model_reasoning_effort='{reasoning_effort or os.getenv('ISITE2_REPORT_QA_REASONING_EFFORT') or DEFAULT_REASONING_EFFORT}'",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-C",
            str(temp_dir),
            "-",
        ]
        process = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=float(os.getenv("ISITE2_REPORT_QA_TIMEOUT_SECONDS", "240")),
            check=False,
        )
        if process.returncode != 0:
            stderr = "\n".join(line for line in process.stderr.splitlines()[-12:] if line.strip())
            raise RuntimeError(f"Codex OAuth report output audit failed: {stderr}")
        if not output_path.exists():
            raise RuntimeError("Codex OAuth report output audit produced no JSON")
        return json.loads(output_path.read_text(encoding="utf-8"))


def _qa_lessons_excerpt(path: Path, *, limit: int = 12000) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    selected: list[str] = []
    in_quick_checklist = False
    for line in lines:
        if line.startswith("## QA 快速前置清单"):
            in_quick_checklist = True
        elif line.startswith("## ") and in_quick_checklist:
            in_quick_checklist = False
        if in_quick_checklist:
            selected.append(line)
    keywords = ("PPT", "Excel", "主指标", "推荐", "数值", "输出件")
    selected.extend(line for line in lines if any(keyword in line for keyword in keywords))
    text = "\n".join(dict.fromkeys(selected))
    return text[:limit]


_GPT_AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "blocking_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "locale": {"type": "string"},
                    "artifact": {"type": "string"},
                    "issue": {"type": "string"},
                    "evidence": {"type": "string"},
                    "required_action": {"type": "string"},
                },
                "required": ["locale", "artifact", "issue", "evidence", "required_action"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "checked_items": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["verdict", "blocking_issues", "warnings", "checked_items", "summary"],
}


_GPT_AUDIT_SYSTEM_PROMPT = """You are the iSite2 standard report QA auditor.

Audit the generated artifact set described by artifact_mode. In excel-only mode, audit
the Excel workbook and do not require a PPT or any image. In all mode, audit both Excel
and PPT. Your job is to catch low-level delivery errors before publication or delivery.

Hard checks:
- In all mode, the standard PPT may use the current one-page scene-card template or a legacy two-page
  recommendation list. Any displayed qualified/recommended property metric must match Excel.
- A one-page scene card shows one representative per scene, while a legacy slide-2 list shows
  at most 10 recommendations. Do not block only because Excel has more recommendation rows than
  the displayed representatives. Block if a displayed recommendation is absent from Excel, a
  displayed metric mismatches Excel, or visible summary/card counts conflict with Excel.
- Airport, hotel, mall, stadium, convention center, office, hospital, transport hub, and
  university values must use scene-specific primary metrics, not the largest number in an
  evidence sentence.
- Treat the configured_recommendation_gates in the request as authoritative. In particular,
  airport terminal_capacity and passenger_capacity are accepted annual-capacity metrics;
  do not reject them merely because annual_passenger_throughput is preferred when available.
- Flag implausible scene values, especially hotel rooms in the thousands/millions, airport
  annual passengers inflated from area units, GLA/area confused with room count, years used
  as metrics, and recommendation counts inconsistent between PPT and Excel.
- Deterministic rule failures are blocking. Do not pass the report if any are present.
- Keep issues actionable and concise.
"""


if __name__ == "__main__":
    sys.exit(main())
