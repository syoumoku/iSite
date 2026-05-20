from isite2.growth.structured_first_backfill import (
    build_structured_first_plan,
    load_structured_source_plans,
)


def test_structured_source_order_keeps_firecrawl_last() -> None:
    plans = load_structured_source_plans()
    source_ids = [plan.source_id for plan in plans]

    assert "wikidata_sparql" in source_ids
    assert "wikipedia_mediawiki_api" in source_ids
    assert "dbpedia_sparql" in source_ids
    assert "ourairports_csv" in source_ids
    assert "openstreetmap_overpass" in source_ids
    assert source_ids[-1] == "firecrawl_gap_fill"


def test_structured_first_plan_has_no_paid_tasks_by_default() -> None:
    plan = build_structured_first_plan(
        countries=["Brazil"],
        scenes=["airport_terminal", "stadium"],
        target_properties=[
            {
                "country": "Brazil",
                "city": "Sao Paulo",
                "property_name": "Sao Paulo Guarulhos Airport",
                "scene_type": "airport_terminal",
            },
            {
                "country": "Brazil",
                "city": "Rio de Janeiro",
                "property_name": "Maracana Stadium",
                "scene_type": "stadium",
            },
        ],
    )

    assert plan.firecrawl_tasks == []
    assert all(not task.use_firecrawl for task in plan.structured_tasks)
    assert plan.source_counts["wikipedia_mediawiki_api"] >= 2
    assert plan.source_counts["ourairports_csv"] == 1
    assert plan.estimated_naive_firecrawl_requests == 6
    assert plan.estimated_firecrawl_requests_saved == 6
    assert any(query.language == "pt" for query in plan.localized_queries)


def test_firecrawl_gap_fill_must_be_requested_explicitly() -> None:
    plan = build_structured_first_plan(
        countries=["Brazil"],
        scenes=["airport_terminal"],
        target_properties=[
            {
                "country": "Brazil",
                "city": "Sao Paulo",
                "property_name": "Sao Paulo Guarulhos Airport",
                "scene_type": "airport_terminal",
            }
        ],
        include_firecrawl_gap_fill=True,
    )

    assert plan.firecrawl_tasks
    assert all(task.use_firecrawl for task in plan.firecrawl_tasks)
    assert all(not task.scrape for task in plan.firecrawl_tasks)
    assert plan.estimated_firecrawl_requests_saved < plan.estimated_naive_firecrawl_requests

