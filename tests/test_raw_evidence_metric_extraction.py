from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import CandidateDraft, load_registry_overlay
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.raw_evidence_metric_extraction import RawEvidenceMetricCleaner


class FakeRawMetricProvider:
    provider_name = "fake_raw_metric_gpt"

    def __init__(self, decision: dict) -> None:
        self.decision = decision
        self.calls = 0

    def extract(self, packet: dict) -> dict:
        self.calls += 1
        self.last_packet = packet
        return dict(self.decision)


def test_explicit_p81_line_count_uses_rules_without_gpt_call(tmp_path) -> None:
    provider = FakeRawMetricProvider(_valid_line_count_decision())
    cleaner = RawEvidenceMetricCleaner(provider=provider, cache_dir=tmp_path / "cache")
    draft = _transport_draft(
        field_value="2 rail/metro lines connected per Wikidata P81 statements",
        content_text=(
            "Baquedano Station has 2 rail/metro lines connected per "
            "Wikidata P81 statements."
        ),
    )

    result = cleaner.clean(_raw(draft), draft)

    assert result.status == "rules_accepted"
    assert result.draft.field_value == draft.field_value
    assert provider.calls == 0


def test_ambiguous_p81_metric_calls_gpt_and_keeps_trace(tmp_path) -> None:
    provider = FakeRawMetricProvider(_valid_line_count_decision())
    cleaner = RawEvidenceMetricCleaner(provider=provider, cache_dir=tmp_path / "cache")
    draft = _transport_draft(
        field_value="Wikidata statement P81",
        content_text="Baquedano Station source text says 2 rail/metro lines are connected.",
    )

    result = cleaner.clean(_raw(draft), draft)

    assert result.status == "gpt_applied"
    assert result.draft.field_group == "line_count"
    assert result.draft.field_value == "2 rail/metro lines connected per source text"
    assert "GPT raw metric extraction" in (result.draft.assumption_note or "")
    assert provider.calls == 1

    cached = cleaner.clean(_raw(draft), draft)
    assert cached.status == "gpt_applied"
    assert cached.cache_status == "hit"
    assert provider.calls == 1


def test_gpt_output_is_rejected_when_local_parser_sees_only_p81(tmp_path) -> None:
    provider = FakeRawMetricProvider(
        {
            **_valid_line_count_decision(),
            "field_value": "Wikidata P81 statement",
            "numeric_value": 81,
            "evidence_quote": "Wikidata P81 statement",
        }
    )
    cleaner = RawEvidenceMetricCleaner(provider=provider, cache_dir=tmp_path / "cache")
    draft = _transport_draft(
        field_value="Wikidata statement P81",
        content_text="Baquedano Station has a Wikidata P81 statement.",
    )

    result = cleaner.clean(_raw(draft), draft)

    assert result.status == "gpt_rejected"
    assert result.draft.field_value == "Wikidata statement P81"
    assert "local numeric parser rejected" in result.issues[0]


def test_curation_applies_gpt_cleaned_metric_before_overlay_sync(tmp_path) -> None:
    provider = FakeRawMetricProvider(_valid_line_count_decision())
    cleaner = RawEvidenceMetricCleaner(provider=provider, cache_dir=tmp_path / "cache")
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"
    draft = _transport_draft(
        field_value="Wikidata statement P81",
        content_text="Baquedano Station source text says 2 rail/metro lines are connected.",
    )

    store.upsert_candidate_evidence(draft, content_text=draft.content_text)
    result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
        metric_cleaner=cleaner,
    )

    assert result is not None
    assert result.accepted_count == 1
    assert result.metric_cleaning_summary["status_counts"] == {"gpt_applied": 1}

    overlay = load_registry_overlay(overlay_path)
    evidence = overlay["countries"]["Chile"]["candidates"][0]["evidence"][0]
    assert evidence["field_group"] == "line_count"
    assert evidence["field_value"] == "2 rail/metro lines connected per source text"
    assert "GPT raw metric extraction" in evidence["assumption_note"]

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["metric_cleaning"]["enabled"] is True
    assert summary["metric_cleaning"]["samples"][0]["status"] == "gpt_applied"


def _valid_line_count_decision() -> dict:
    return {
        "extracted": True,
        "field_group": "line_count",
        "indicator_name": "line_count",
        "field_value": "2 rail/metro lines connected per source text",
        "numeric_value": 2,
        "unit": "lines",
        "is_scene_primary_metric": True,
        "confidence": 0.88,
        "evidence_quote": "2 rail/metro lines are connected",
        "explanation": "The source explicitly counts connected rail/metro lines.",
        "review_reason": None,
        "next_action": "补充第二独立来源核验线路数量。",
    }


def _raw(draft: CandidateDraft) -> SimpleNamespace:
    return SimpleNamespace(
        id="raw-1",
        source_url=draft.source_url,
        source_name=draft.source_name,
        source_date=draft.source_date,
        field_value=draft.field_value,
        payload={"content_text": draft.content_text},
    )


def _transport_draft(*, field_value: str, content_text: str) -> CandidateDraft:
    return replace(
        CandidateDraft(
            region="Latin America",
            country="Chile",
            city="Santiago",
            property_name="Baquedano Station",
            scene_type="transport_hub",
            annual_visits=None,
            latitude=-33.4374,
            longitude=-70.6348,
            geocode_precision="metro station centroid",
            map_source="Wikidata coordinate statement",
            map_source_date="2026-05-25",
            field_group="line_count",
            indicator_name="line_count",
            field_value=field_value,
            source_name="Wikidata/Wikipedia",
            source_tier="Tier 3",
            source_url="https://en.wikipedia.org/wiki/Baquedano_metro_station",
            source_date="2026-05-25",
            bbox={
                "min_latitude": -56.0,
                "max_latitude": -17.0,
                "min_longitude": -76.0,
                "max_longitude": -66.0,
            },
        ),
        content_text=content_text,
    )
