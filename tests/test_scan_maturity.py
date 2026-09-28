from isite2.rules.scan_maturity import (
    DEEP_EXPANSION,
    NEAR_SATURATION,
    SEED_SCAN,
    add_scan_maturity_to_country_summaries,
    evaluate_scan_maturity,
)


def test_seed_scan_does_not_mistake_scene_breadth_for_depth() -> None:
    result = evaluate_scan_maturity(
        candidate_count=50,
        scene_count=7,
        source_count=50,
    )

    assert result["level"] == SEED_SCAN
    assert result["basis"] == "active_coverage"
    assert result["evidence_per_candidate"] == 1.0


def test_deep_expansion_can_be_inferred_from_large_multi_source_pool() -> None:
    result = evaluate_scan_maturity(
        candidate_count=159,
        scene_count=6,
        source_count=281,
    )

    assert result["level"] == DEEP_EXPANSION
    assert result["basis"] == "active_coverage"
    assert result["evidence_per_candidate"] == 1.77


def test_near_saturation_requires_broad_tracked_exhaustion() -> None:
    progress = [
        {
            "country": "Example",
            "scene_type": f"scene-{scene}",
            "source_type": f"source-{source}",
            "status": "exhausted",
            "cycle_number": 3,
        }
        for scene in range(6)
        for source in range(2)
    ]

    result = evaluate_scan_maturity(
        candidate_count=60,
        scene_count=6,
        source_count=90,
        progress_rows=progress,
    )

    assert result["level"] == NEAR_SATURATION
    assert result["basis"] == "tracked_exhaustion"
    assert result["progress_group_count"] == 12
    assert result["exhausted_group_count"] == 12


def test_partial_exhaustion_never_claims_near_saturation() -> None:
    progress = [
        {
            "country": "Example",
            "scene_type": f"scene-{scene}",
            "source_type": f"source-{source}",
            "status": "exhausted" if scene < 5 else "active",
            "cycle_number": 2,
        }
        for scene in range(6)
        for source in range(2)
    ]

    result = evaluate_scan_maturity(
        candidate_count=80,
        scene_count=6,
        source_count=120,
        progress_rows=progress,
    )

    assert result["level"] == DEEP_EXPANSION
    assert result["level"] != NEAR_SATURATION


def test_country_summary_enrichment_groups_progress_by_country() -> None:
    summaries = [
        {
            "country": "Germany",
            "candidate_count": 50,
            "source_count": 50,
            "scenes": {"airport_terminal": 13, "stadium": 8},
        },
        {
            "country": "Turkey",
            "candidate_count": 159,
            "source_count": 281,
            "scenes": {f"scene-{index}": 1 for index in range(6)},
        },
    ]

    enriched = add_scan_maturity_to_country_summaries(summaries)

    assert enriched[0]["scan_maturity"]["level"] == SEED_SCAN
    assert enriched[1]["scan_maturity"]["level"] == DEEP_EXPANSION
