import json
from pathlib import Path

import pytest

from isite2.public_reports import (
    CountryReportUnavailable,
    country_export_state_hash,
    file_sha256,
    generate_public_report_stage,
    public_report_activation_script,
    resolve_country_excel_report,
)


def test_country_export_state_hash_is_order_independent_and_export_scoped() -> None:
    rows = [
        {"property_id": "b", "scan_run_id": "run-2", "export_ready": True},
        {"property_id": "ignored", "scan_run_id": "run-9", "export_ready": False},
        {"property_id": "a", "scan_run_id": "run-1", "export_ready": True},
    ]

    assert country_export_state_hash(rows) == country_export_state_hash(
        list(reversed(rows))
    )
    assert country_export_state_hash(rows) != country_export_state_hash(
        [{"property_id": "a", "scan_run_id": "run-3", "export_ready": True}]
    )


def test_resolve_country_report_rejects_path_escape_and_checksum_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "reports"
    root.mkdir()
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"PK fake")
    index = {
        "version": 1,
        "reports": {
            "Algeria": {
                "audit_status": "pass",
                "country_state_hash": "state",
                "locales": {
                    "en": {
                        "path": "../outside.xlsx",
                        "filename": "outside.xlsx",
                        "sha256": file_sha256(outside),
                    }
                },
            }
        },
    }
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(CountryReportUnavailable, match="outside"):
        resolve_country_excel_report(
            country="Algeria",
            locale="en",
            expected_state_hash="state",
            root=root,
        )

    report_dir = root / "releases" / "content"
    report_dir.mkdir(parents=True)
    report = report_dir / "report.xlsx"
    report.write_bytes(b"PK report")
    index["reports"]["Algeria"]["locales"]["en"].update(
        {"path": "releases/content/report.xlsx", "sha256": "wrong"}
    )
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(CountryReportUnavailable, match="checksum"):
        resolve_country_excel_report(
            country="Algeria",
            locale="en",
            expected_state_hash="state",
            root=root,
        )


def test_public_report_activation_script_is_atomic_and_checksum_guarded() -> None:
    script = public_report_activation_script("/opt/isite2/public_reports", "/tmp/incoming")

    assert "os.replace(temporary, index_path)" in script
    assert "report checksum mismatch" in script
    assert "index.setdefault('reports', {}).update" in script
    assert "os.makedirs(target, exist_ok=True)" in script
    assert "shutil.copy2" in script


def test_public_report_generation_uses_excel_only_mode(tmp_path: Path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        observed["command"] = command
        return type("Result", (), {"returncode": 1, "stderr": "stop", "stdout": ""})()

    monkeypatch.setattr("isite2.public_reports.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="stop"):
        generate_public_report_stage(
            source_url="postgresql://example",
            countries=["Russia"],
            country_state_hashes={"Russia": "state"},
            release_dir=tmp_path,
            timestamp="20260811T000000",
        )

    command = observed["command"]
    mode_index = command.index("--artifact-mode")
    assert command[mode_index + 1] == "excel-only"
