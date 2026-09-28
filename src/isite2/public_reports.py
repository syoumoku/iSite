from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping


REPORT_INDEX_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CountryReportUnavailable(RuntimeError):
    pass


def public_report_root() -> Path:
    configured = os.getenv("ISITE2_PUBLIC_REPORT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "outputs" / "public_reports").resolve()


def country_export_state_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    entries = sorted(
        (str(row["property_id"]), str(row["scan_run_id"]))
        for row in rows
        if bool(row.get("export_ready", True))
    )
    digest = hashlib.sha256()
    for property_id, scan_run_id in entries:
        digest.update(property_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(scan_run_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_public_report_index(root: Path | None = None) -> dict[str, Any]:
    report_root = (root or public_report_root()).resolve()
    index_path = report_root / "index.json"
    if not index_path.is_file():
        return {"version": REPORT_INDEX_VERSION, "reports": {}}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CountryReportUnavailable("public report index is unreadable") from exc
    if payload.get("version") != REPORT_INDEX_VERSION or not isinstance(
        payload.get("reports"), dict
    ):
        raise CountryReportUnavailable("public report index has an unsupported schema")
    return payload


def resolve_country_excel_report(
    *,
    country: str,
    locale: str,
    expected_state_hash: str,
    root: Path | None = None,
) -> tuple[Path, str]:
    report_root = (root or public_report_root()).resolve()
    report = load_public_report_index(report_root).get("reports", {}).get(country)
    if not isinstance(report, dict):
        raise CountryReportUnavailable("country report has not been published")
    if report.get("audit_status") != "pass":
        raise CountryReportUnavailable("country report audit has not passed")
    if report.get("country_state_hash") != expected_state_hash:
        raise CountryReportUnavailable("country report is stale for the current data")
    artifact = (report.get("locales") or {}).get(locale)
    if not isinstance(artifact, dict):
        raise CountryReportUnavailable("country report locale is unavailable")
    relative_path = str(artifact.get("path") or "")
    candidate = (report_root / relative_path).resolve()
    try:
        candidate.relative_to(report_root)
    except ValueError as exc:
        raise CountryReportUnavailable("country report path is outside the report root") from exc
    if not candidate.is_file():
        raise CountryReportUnavailable("country report file is missing")
    expected_sha256 = str(artifact.get("sha256") or "")
    if not expected_sha256 or file_sha256(candidate) != expected_sha256:
        raise CountryReportUnavailable("country report checksum validation failed")
    filename = str(artifact.get("filename") or candidate.name)
    return candidate, filename


def generate_public_report_stage(
    *,
    source_url: str,
    countries: list[str],
    country_state_hashes: Mapping[str, str],
    release_dir: Path,
    timestamp: str,
) -> dict[str, Any]:
    ordered_countries = list(dict.fromkeys(country.strip() for country in countries if country.strip()))
    if not ordered_countries:
        raise ValueError("at least one country is required for public report generation")
    generation_root = release_dir / "country_report_generation"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "generate_standard_country_report.py"),
        "--output-root",
        str(generation_root),
        "--timestamp",
        timestamp,
        "--recommendation-metric-gpt",
        "0",
        "--artifact-mode",
        "excel-only",
    ]
    for country in ordered_countries:
        command.extend(["--country", country])
    env = os.environ.copy()
    env["DATABASE_URL"] = source_url
    env["ISITE2_DATABASE_URL"] = source_url
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    process = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
    if process.returncode:
        raise RuntimeError(
            "standard country report generation failed: "
            + (process.stderr.strip() or process.stdout.strip())[-4000:]
        )
    summary_path = generation_root / f"standard_report_batch_summary_{timestamp}.json"
    if not summary_path.is_file():
        raise RuntimeError("standard country report batch summary is missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("audit_status") != "pass":
        raise RuntimeError("standard country report audit did not pass")

    stage_root = release_dir / "public_reports"
    staged_releases = stage_root / "releases"
    report_entries: dict[str, Any] = {}
    for country_summary in summary.get("country_reports", []):
        country = str(country_summary.get("country") or "")
        if country not in ordered_countries or country_summary.get("audit_status") != "pass":
            raise RuntimeError(f"audited public report is unavailable for {country!r}")
        content_hash = str(country_summary.get("content_hash") or "")
        state_hash = country_state_hashes.get(country)
        if not content_hash or not state_hash:
            raise RuntimeError(f"public report hashes are missing for {country!r}")
        destination = staged_releases / content_hash
        destination.mkdir(parents=True, exist_ok=True)
        locale_artifacts: dict[str, Any] = {}
        for raw_path in country_summary.get("generated_files", []):
            source = Path(raw_path)
            if source.suffix.casefold() != ".xlsx":
                continue
            locale = _locale_from_report_filename(source.name)
            if locale is None:
                continue
            with zipfile.ZipFile(source) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError(f"invalid Excel archive: {source}")
            target = destination / source.name
            shutil.copy2(source, target)
            locale_artifacts[locale] = {
                "path": f"releases/{content_hash}/{target.name}",
                "filename": target.name,
                "sha256": file_sha256(target),
                "bytes": target.stat().st_size,
            }
        if set(locale_artifacts) != {"en", "zh"}:
            raise RuntimeError(f"both audited Excel locales are required for {country!r}")
        audit_source = Path(str(country_summary.get("audit_path") or ""))
        if not audit_source.is_file():
            raise RuntimeError(f"country audit artifact is missing for {country!r}")
        audit_target = destination / audit_source.name
        shutil.copy2(audit_source, audit_target)
        report_entries[country] = {
            "country": country,
            "country_state_hash": state_hash,
            "content_hash": content_hash,
            "audit_status": "pass",
            "audit_path": f"releases/{content_hash}/{audit_target.name}",
            "export_ready_packets": int(country_summary.get("export_ready_packets") or 0),
            "locales": locale_artifacts,
        }
    if set(report_entries) != set(ordered_countries):
        raise RuntimeError("country report batch is incomplete")
    patch_payload = {
        "version": REPORT_INDEX_VERSION,
        "reports": report_entries,
    }
    (stage_root / "index_patch.json").write_text(
        json.dumps(patch_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return patch_payload


def public_report_activation_script(remote_report_root: str, incoming_dir: str) -> str:
    return "\n".join(
        [
            "set -eu",
            "python3 - " + repr(remote_report_root) + " " + repr(incoming_dir) + " <<'PY'",
            "import json, os, shutil, sys, tempfile",
            "root, incoming = map(os.path.abspath, sys.argv[1:3])",
            "os.makedirs(os.path.join(root, 'releases'), exist_ok=True)",
            "patch_path = os.path.join(incoming, 'index_patch.json')",
            "with open(patch_path, encoding='utf-8') as handle: patch = json.load(handle)",
            "for name in os.listdir(os.path.join(incoming, 'releases')):",
            "    source = os.path.join(incoming, 'releases', name)",
            "    target = os.path.join(root, 'releases', name)",
            "    if os.path.isdir(source):",
            "        os.makedirs(target, exist_ok=True)",
            "        for dirpath, _dirnames, filenames in os.walk(source):",
            "            relative = os.path.relpath(dirpath, source)",
            "            target_dir = target if relative == '.' else os.path.join(target, relative)",
            "            os.makedirs(target_dir, exist_ok=True)",
            "            for filename in filenames:",
            "                shutil.copy2(os.path.join(dirpath, filename), os.path.join(target_dir, filename))",
            "    elif not os.path.exists(target): shutil.copy2(source, target)",
            "import hashlib",
            "for report in patch.get('reports', {}).values():",
            "    for artifact in report.get('locales', {}).values():",
            "        path = os.path.join(root, artifact['path'])",
            "        digest = hashlib.sha256()",
            "        with open(path, 'rb') as handle:",
            "            for chunk in iter(lambda: handle.read(1024 * 1024), b''): digest.update(chunk)",
            "        if digest.hexdigest() != artifact['sha256']: raise SystemExit('report checksum mismatch')",
            "index_path = os.path.join(root, 'index.json')",
            "if os.path.exists(index_path):",
            "    with open(index_path, encoding='utf-8') as handle: index = json.load(handle)",
            "else: index = {'version': 1, 'reports': {}}",
            "index.setdefault('reports', {}).update(patch.get('reports', {}))",
            "index['version'] = 1",
            "fd, temporary = tempfile.mkstemp(prefix='index.', suffix='.json', dir=root)",
            "with os.fdopen(fd, 'w', encoding='utf-8') as handle:",
            "    json.dump(index, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write('\\n')",
            "os.replace(temporary, index_path)",
            "shutil.rmtree(incoming, ignore_errors=True)",
            "PY",
        ]
    )


def _locale_from_report_filename(filename: str) -> str | None:
    for locale in ("en", "zh"):
        if f"_standard_report_{locale}_" in filename:
            return locale
    return None
