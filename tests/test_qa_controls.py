from pathlib import Path

from isite2.rules.config_loader import (
    load_qa_controls,
    validate_qa_controls_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def test_active_qa_controls_have_executable_ownership() -> None:
    controls = load_qa_controls()

    assert validate_qa_controls_contract(controls) == []
    automated = [row for row in controls["controls"] if row["enforcement"] == "automated"]
    manual = [row for row in controls["controls"] if row["enforcement"] == "manual"]
    assert len(automated) >= 15
    assert manual
    assert all(row["rule_refs"] and row["test_refs"] for row in automated)
    assert all(row["artifact_required"] is True for row in manual)


def test_compact_guide_and_control_registry_are_bidirectionally_linked() -> None:
    controls = load_qa_controls()
    guide = (ROOT / controls["active_guide"]).read_text(encoding="utf-8")

    for row in controls["controls"]:
        assert row["id"] in guide
    mentioned_ids = {
        token.strip("`.,:;()[]")
        for token in guide.replace("/", " ").split()
        if token.startswith("QA-")
    }
    configured_ids = {row["id"] for row in controls["controls"]}
    assert mentioned_ids <= configured_ids


def test_full_incident_history_is_archived_not_discarded() -> None:
    controls = load_qa_controls()
    archive = (ROOT / controls["history_archive"]).read_text(encoding="utf-8")

    assert archive.count("\n| 2026-") >= 250
    assert "交通线路数指标映射" in archive
    assert "图片单字段更新与证据去重" in archive


def test_text_only_active_control_fails_contract() -> None:
    controls = {
        "history_archive": "docs/qa_archive/2026_history_through_2026-08-10.md",
        "active_guide": "docs/15_qa_lessons_learned.md",
        "controls": [
            {
                "id": "QA-BROKEN-001",
                "title": "Text only",
                "domain": "qa",
                "severity": "high",
                "enforcement": "automated",
            }
        ],
    }

    issues = validate_qa_controls_contract(controls)
    assert "QA-BROKEN-001: automated control requires rule_refs" in issues
    assert "QA-BROKEN-001: automated control requires test_refs" in issues
