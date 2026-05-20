from isite2.rules.config_loader import (
    load_discovery_sources,
    load_firecrawl_search_strategy,
    load_free_structured_sources,
    load_gate_rules,
    load_localized_search_strategy,
    load_output_template,
    load_scene_rules,
    load_source_registry,
    load_status_enums,
    validate_output_template_contract,
)
from isite2.growth.firecrawl_search_strategy import (
    build_firecrawl_queries,
    build_gap_closure_queries,
)
from isite2.rules.demand import demand_params_from_scene_rule
from isite2.rules.reason import make_google_maps_link
from isite2.rules.validation import display_slice, full_scan_preserved, is_concrete_review_action


def test_scene_rules_and_output_template_load() -> None:
    scene_rules = load_scene_rules()
    output_template = load_output_template()

    assert "airport_terminal" in scene_rules["scenes"]
    assert output_template["excel"]["main_columns"][-1] == "Google地图链接"
    assert "sorting_rules" in output_template["excel"]
    assert "年访问量" in output_template["excel"]["sorting_rules"]["main_table"]
    assert validate_output_template_contract(output_template) == []


def test_status_and_gate_rules_load() -> None:
    status_enums = load_status_enums()
    gates = load_gate_rules()

    assert "Verified" in status_enums["evidence_status"]
    assert "build_status_gate" in gates["gates"]


def test_source_registry_loads_two_country_seed_pool() -> None:
    registry = load_source_registry()

    assert set(registry["countries"]) >= {"Algeria", "Egypt"}
    assert len(registry["countries"]["Algeria"]["candidates"]) >= 4
    assert len(registry["countries"]["Egypt"]["candidates"]) >= 4
    assert registry["countries"]["Egypt"]["bbox"]["min_longitude"] < 31.4
    candidates = [
        candidate
        for country in ("Algeria", "Egypt")
        for candidate in registry["countries"][country]["candidates"]
    ]
    assert all(candidate["hero_image"]["url"].startswith("https://") for candidate in candidates)
    assert all(
        candidate["hero_image"]["source_url"].startswith("https://")
        for candidate in candidates
    )
    assert set(registry["countries"]) >= {"Sri Lanka", "Cambodia", "Maldives"}
    assert registry["countries"]["Sri Lanka"]["bbox"]["min_longitude"] < 80.0
    assert registry["countries"]["Cambodia"]["bbox"]["max_longitude"] > 107.0
    assert registry["countries"]["Maldives"]["bbox"]["min_latitude"] < 0.0


def test_discovery_sources_include_firecrawl_batch_templates() -> None:
    discovery_sources = load_discovery_sources()

    scenes = discovery_sources["scenes"]
    assert any(
        "StadiumDB" in template
        for template in scenes["stadium"]["query_templates"]["firecrawl_search"]
    )
    assert any(
        "gross leasable area" in template
        for template in scenes["mall_mixed_use"]["query_templates"]["firecrawl_search"]
    )
    assert any(
        "ministry statistics" in template
        for template in scenes["university"]["query_templates"]["firecrawl_search"]
    )


def test_discovery_sources_prioritize_landmark_scenes_before_supplemental() -> None:
    scenes = load_discovery_sources()["scenes"]

    assert scenes["airport_terminal"]["scan_priority"] < scenes["hospital"]["scan_priority"]
    assert scenes["transport_hub"]["scan_priority"] < scenes["university"]["scan_priority"]
    assert scenes["convention_center"]["priority_band"] == "primary_landmark"
    assert scenes["luxury_hotel_mice"]["priority_band"] == "primary_landmark"
    assert scenes["stadium"]["priority_band"] == "primary_landmark"
    assert scenes["office_government"]["priority_band"] == "primary_landmark"
    assert scenes["hospital"]["priority_band"] == "supplemental"
    assert scenes["university"]["priority_band"] == "supplemental"


def test_firecrawl_search_strategy_codifies_evidence_strengthening() -> None:
    strategy = load_firecrawl_search_strategy()

    assert strategy["defaults"]["limit"] == 3
    assert strategy["defaults"]["scrape"] is False
    assert strategy["defaults"]["firecrawl_role"] == "last_resort_gap_fill"
    assert "second_source_strengthening" in strategy["phases"]
    assert "Keep high-value property evidence separate" in " ".join(
        strategy["evidence_acceptance_rules"]
    )
    assert (
        strategy["scene_strategies"]["mall_mixed_use"]["proxy_handling"][
            "total_development_area_indicator"
        ]
        == "mixed_use_area"
    )
    assert "build_status_queries" in strategy["scene_strategies"]["stadium"]
    assert (
        strategy["scene_strategies"]["transport_hub"]["scan_priority"]
        < strategy["scene_strategies"]["hospital"]["scan_priority"]
    )
    assert strategy["scene_strategies"]["university"]["priority_band"] == "supplemental"


def test_firecrawl_query_builder_expands_structured_queries() -> None:
    queries = build_firecrawl_queries(
        country="Egypt",
        city="Alexandria",
        property_name="Borg El Arab Stadium",
        scene_type="stadium",
        phases=["second_source_strengthening"],
    )

    assert queries
    assert all(query.phase == "second_source_strengthening" for query in queries)
    assert all(query.limit == 3 and not query.scrape for query in queries)
    assert all(query.priority_band == "primary_landmark" for query in queries)
    assert all("{" not in query.query and "}" not in query.query for query in queries)
    assert any("Borg El Arab Stadium" in query.query for query in queries)
    assert any("StadiumDB" in query.fallback_channels for query in queries)


def test_gap_closure_queries_keep_build_status_separate() -> None:
    queries = build_gap_closure_queries(
        country="Egypt",
        property_name="Mall of Egypt",
        scene_type="mall_mixed_use",
        weak_single_tier3=True,
        proxy_only=True,
        missing_build_status=True,
    )

    phases = {query.phase for query in queries}
    assert "second_source_strengthening" in phases
    assert "build_status_search" in phases
    assert any("indoor 5G" in query.query for query in queries)


def test_localized_and_free_structured_configs_load() -> None:
    localized = load_localized_search_strategy()
    free_sources = load_free_structured_sources()

    assert localized["country_profiles"]["Brazil"]["languages"][0] == "pt"
    assert "passageiros" in " ".join(
        localized["language_profiles"]["pt"]["metric_terms"]["airport_terminal"]
    )
    assert "wikipedia_mediawiki_api" in free_sources["sources"]
    assert "dbpedia_sparql" in free_sources["sources"]
    assert "ourairports_csv" in free_sources["sources"]
    assert free_sources["execution_order"][-1] == "firecrawl_gap_fill"
    assert localized["country_profiles"]["Sri Lanka"]["languages"] == ["en", "si", "ta"]
    assert localized["country_profiles"]["Cambodia"]["languages"] == ["km", "en"]
    assert localized["country_profiles"]["Maldives"]["languages"] == ["en", "dv"]


def test_scene_demand_params_use_midpoint() -> None:
    airport = load_scene_rules()["scenes"]["airport_terminal"]
    params = demand_params_from_scene_rule(airport)

    assert params.attach_rate == 0.8
    assert round(params.indoor_capture, 2) == 0.9
    assert params.busy_hour_factor == 0.1
    assert params.gb_per_user_busy_hour == 0.55


def test_google_maps_link_format() -> None:
    assert (
        make_google_maps_link(35, 139)
        == "https://www.google.com/maps/search/?api=1&query=35.000000,139.000000"
    )
    assert (
        make_google_maps_link(
            6.9271,
            79.8612,
            property_name="Tintagel Colombo",
            city="Colombo",
            country="Sri Lanka",
        )
        == "https://www.google.com/maps/search/?api=1&query=Tintagel%20Colombo%2C%20Colombo%2C%20Sri%20Lanka"
    )


def test_display_slice_does_not_mutate_full_candidate_pool() -> None:
    candidates = [1, 2, 3, 4]

    assert display_slice(candidates, top_n=2) == [1, 2]
    assert candidates == [1, 2, 3, 4]
    assert full_scan_preserved(candidate_count=4, stored_count=len(candidates))


def test_review_action_must_be_concrete() -> None:
    assert is_concrete_review_action("补查运营商室分公告，并记录来源链接和日期。")
    assert not is_concrete_review_action("待研究")
