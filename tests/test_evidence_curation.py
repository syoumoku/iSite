import json
from dataclasses import replace

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import CandidateDraft, load_registry_overlay
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.db.models import RawEvidenceItemDB


def test_new_url_raw_evidence_triggers_curation(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"
    draft = _draft(source_url="https://example.org/new-evidence")

    write_result = store.upsert_candidate_evidence(draft)
    result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )

    assert write_result.is_new_evidence is True
    assert result is not None
    assert result.new_evidence_count == 1
    assert result.accepted_count == 1
    assert result.rejected_count == 0
    assert result.report_path.exists()
    assert store.raw_status_counts() == {"candidate_accepted": 1}
    overlay = load_registry_overlay(overlay_path)
    candidates = overlay["countries"]["Nigeria"]["candidates"]
    assert candidates[0]["property_name"] == "Test Evidence Mall"


def test_raw_evidence_curation_preserves_hero_image(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"
    draft = replace(
        _draft(source_url="https://example.org/new-evidence-with-image"),
        hero_image={
            "url": "https://example.org/mall.jpg",
            "alt_text": "Test Evidence Mall public image",
            "source_name": "Example public media",
            "source_url": "https://example.org/mall",
            "source_date": "2026-05-12",
            "license": "Public web image metadata",
        },
    )

    store.upsert_candidate_evidence(draft)
    result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )

    assert result is not None
    overlay = load_registry_overlay(overlay_path)
    candidate = overlay["countries"]["Nigeria"]["candidates"][0]
    assert candidate["hero_image"]["url"] == "https://example.org/mall.jpg"


def test_raw_evidence_curation_adds_default_hero_alt_text(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"
    draft = replace(
        _draft(source_url="https://example.org/new-evidence-with-image-no-alt"),
        hero_image={
            "url": "https://example.org/mall.jpg",
            "source_name": "Example public media",
            "source_url": "https://example.org/mall",
            "source_date": "2026-05-12",
            "license": "Public web image metadata",
        },
    )

    store.upsert_candidate_evidence(draft)
    result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )

    assert result is not None
    overlay = load_registry_overlay(overlay_path)
    candidate = overlay["countries"]["Nigeria"]["candidates"][0]
    assert candidate["hero_image"]["alt_text"] == "Test Evidence Mall public image"


def test_duplicate_url_with_unchanged_hash_does_not_trigger_second_curation(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"
    draft = _draft(source_url="https://example.org/duplicate")

    first = store.upsert_candidate_evidence(draft)
    duplicate = store.upsert_candidate_evidence(draft)
    result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )
    second_result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )

    assert first.is_new_evidence is True
    assert duplicate.duplicate_unchanged is True
    assert result is not None
    assert result.new_evidence_count == 1
    assert second_result is None


def test_retracted_evidence_can_be_recollected_with_same_content_hash(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    draft = _draft(source_url="https://example.org/retracted")

    first = store.upsert_candidate_evidence(draft)
    with store.session_factory.begin() as session:
        raw = session.get(RawEvidenceItemDB, first.raw_evidence_id)
        raw.status = "retracted_quality_guard"
        raw.curation_needed = False
    retry = store.upsert_candidate_evidence(draft)

    assert retry.is_new_evidence is True
    assert retry.duplicate_unchanged is False
    assert store.raw_status_counts() == {"retracted_quality_guard": 1, "new": 1}


def test_existing_url_with_changed_source_date_triggers_candidate_update(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"
    draft = _draft(source_url="https://example.org/changed")

    store.upsert_candidate_evidence(draft)
    first = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )
    changed = store.upsert_candidate_evidence(
        _draft(source_url="https://example.org/changed", source_date="2026-06-01")
    )
    second = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )

    assert first is not None
    assert first.accepted_count == 1
    assert changed.is_changed_evidence is True
    assert second is not None
    assert second.updated_count == 1


def test_identity_variant_updates_existing_candidate_without_new_overlay_row(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"

    store.upsert_candidate_evidence(_draft(source_url="https://example.org/mall-profile"))
    first = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )
    store.upsert_candidate_evidence(
        _draft(
            property_name="Test Evidence Mall Official Profile",
            source_url="https://example.org/mall-footfall",
            field_value="Annual public footfall is 1,000,000 visitors.",
        )
    )
    second = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )

    overlay = load_registry_overlay(overlay_path)
    candidates = overlay["countries"]["Nigeria"]["candidates"]

    assert first is not None
    assert first.accepted_count == 1
    assert second is not None
    assert second.accepted_count == 0
    assert second.updated_count == 1
    assert len(candidates) == 1
    assert len(candidates[0]["evidence"]) == 2


def test_incomplete_evidence_goes_to_review_not_candidate_pool(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    overlay_path = tmp_path / "overlay.yaml"
    draft_path = tmp_path / "drafts.json"
    incomplete = _draft(
        property_name="Incomplete Evidence Mall",
        source_url="https://example.org/incomplete",
        field_value="",
    )

    store.upsert_candidate_evidence(incomplete)
    result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=overlay_path,
        draft_path=draft_path,
    )

    assert result is not None
    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert store.raw_status_counts() == {"draft_review": 1}
    overlay = load_registry_overlay(overlay_path)
    assert overlay.get("countries", {}) == {}
    review_payload = json.loads(draft_path.read_text(encoding="utf-8"))
    assert review_payload["rejected_count"] == 1
    assert "field_value missing" in review_payload["drafts"][0]["issues"]


def _draft(
    *,
    source_url: str,
    source_date: str = "2026-05-08",
    property_name: str = "Test Evidence Mall",
    field_value: str = "Major public mall and mixed-use destination in Lagos.",
) -> CandidateDraft:
    return CandidateDraft(
        region="Africa",
        country="Nigeria",
        city="Lagos",
        property_name=property_name,
        scene_type="mall_mixed_use",
        annual_visits=1_000_000,
        latitude=6.45,
        longitude=3.4,
        geocode_precision="venue centroid",
        map_source="OpenStreetMap public coordinates",
        map_source_date="2026-05-08",
        field_group="mixed_use_role",
        indicator_name="public_destination_role",
        field_value=field_value,
        source_name="Example public source",
        source_tier="Tier 3",
        source_url=source_url,
        source_date=source_date,
        bbox={
            "min_latitude": 4.0,
            "max_latitude": 14.2,
            "min_longitude": 2.5,
            "max_longitude": 15.0,
        },
    )
