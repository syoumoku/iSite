from __future__ import annotations

import json
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

from isite2.connectors.models import FetchedPage
from isite2.domain.enums import SourceTier


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_full_evidence_backfill_firecrawl.py"
)
SPEC = importlib.util.spec_from_file_location("run_full_evidence_backfill_firecrawl", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
backfill = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backfill
SPEC.loader.exec_module(backfill)


def test_audit_log_flushes_search_results_immediately(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"
    audit = backfill.AuditLog(log_path)

    audit.append(
        "search_completed",
        run_id="run-1",
        query_key="property-1|airport query",
        results=[
            {
                "title": "Airport annual traffic",
                "url": "https://airport.example/report",
                "source_name": "Firecrawl Search",
                "snippet": "12 million passengers",
            }
        ],
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_type"] == "search_completed"
    assert payload["results"][0]["url"] == "https://airport.example/report"

    state = backfill.AuditState.from_event_paths([log_path])
    results = state.logged_search_results("property-1|airport query")
    assert results is not None
    assert results[0]["title"] == "Airport annual traffic"


def test_property_id_file_supports_newline_and_json(tmp_path) -> None:
    newline_path = tmp_path / "ids.txt"
    newline_path.write_text("# comment\nproperty-1\n\nproperty-2\n", encoding="utf-8")
    json_path = tmp_path / "ids.json"
    json_path.write_text(json.dumps(["property-3", "property-4"]), encoding="utf-8")

    assert backfill._load_property_ids(str(newline_path)) == {"property-1", "property-2"}
    assert backfill._load_property_ids(str(json_path)) == {"property-3", "property-4"}


def test_russia_queries_prefer_scene_specific_local_directories() -> None:
    queries = backfill._queries_for_property(
        {
            "country": "Russia",
            "city": "Moscow",
            "canonical_name": "Aviapark",
            "scene_type": "mall_mixed_use",
        }
    )

    assert queries[0] == "Aviapark Moscow site:shopandmall.ru арендопригодная площадь"
    assert any("пассажиропоток" in query or "арендопригодная" in query for query in queries)


def test_ambiguous_short_mall_name_skips_broad_or_fallback() -> None:
    prop = {
        "country": "Russia",
        "city": "Saint Petersburg",
        "canonical_name": "5 ozer",
        "scene_type": "mall_mixed_use",
    }

    assert not backfill._unsafe_broad_firecrawl_query(
        prop,
        "5 ozer Saint Petersburg site:shopandmall.ru арендопригодная площадь",
    )
    assert backfill._unsafe_broad_firecrawl_query(
        prop,
        "5 ozer Russia gross leasable area OR GLA OR annual footfall",
    )


def test_audit_state_recovers_saved_page_without_refetch(tmp_path) -> None:
    page = FetchedPage(
        source_url="https://airport.example/report",
        source_name="airport.example",
        source_tier=SourceTier.TIER_1,
        source_date="2025-01-01",
        fetched_at=datetime(2026, 5, 13, tzinfo=UTC),
        content_text="The airport handled 12,000,000 passengers in 2024.",
        robots_allowed=True,
    )
    page_payload = backfill._persist_fetched_page(page, tmp_path / "pages")
    log_path = tmp_path / "events.jsonl"
    audit = backfill.AuditLog(log_path)
    audit.append(
        "fetch_completed",
        run_id="run-1",
        query_key="property-1|airport query",
        url="https://airport.example/report",
        **page_payload,
    )

    state = backfill.AuditState.from_event_paths([log_path])
    logged_page = state.logged_page("https://airport.example/report#traffic")
    assert logged_page is not None

    recovered = backfill._page_from_logged_event(logged_page)
    assert recovered is not None
    assert recovered.source_tier == SourceTier.TIER_1
    assert "12,000,000 passengers" in recovered.content_text


def test_terminal_queries_and_written_evidence_are_not_repeated(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"
    audit = backfill.AuditLog(log_path)
    audit.append("query_completed", run_id="run-1", query_key="q-no-hit", accepted=False)
    audit.append("search_failed", run_id="run-1", query_key="q-failed")
    audit.append(
        "evidence_written",
        run_id="run-1",
        query_key="q-hit",
        property_id="property-1",
        raw_evidence_id="raw-1",
    )
    audit.append(
        "evidence_retracted",
        run_id="run-1",
        query_key="q-hit",
        property_id="property-1",
        raw_evidence_id="raw-1",
        reason="source page metric was not property-specific",
    )

    state = backfill.AuditState.from_event_paths([log_path])

    assert state.query_terminal("q-no-hit") is True
    assert state.query_terminal("q-failed") is True
    assert state.query_terminal("q-failed", retry_failures=True) is False
    assert state.query_accepted("q-hit") is False
    assert state.query_terminal("q-hit") is False
    assert "property-1" not in state.accepted_property_ids


def test_credit_budget_zero_blocks_live_requests(monkeypatch) -> None:
    monkeypatch.setattr(backfill, "_remaining_firecrawl_credits", lambda: 100)

    assert backfill._budget_exhausted(
        start_remaining=100,
        budget=0,
        request_count=0,
        check_interval=1,
    )
    assert not backfill._budget_exhausted(
        start_remaining=100,
        budget=-1,
        request_count=0,
        check_interval=1,
    )


def test_airport_metric_requires_property_specific_context() -> None:
    prop = {
        "canonical_name": "Biskra Mohamed Khider Airport",
        "country": "Argentina",
        "scene_type": "airport_terminal",
    }
    generic_page = FetchedPage(
        source_url="https://en.wikipedia.org/wiki/List_of_busiest_airports_by_passenger_traffic",
        source_name="Wikipedia",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "List of busiest airports by passenger traffic. "
            "Hartsfield-Jackson Atlanta International Airport handled 70 million passengers. "
            "Biskra Mohamed Khider Airport is also an airport in Argentina."
        ),
    )
    specific_page = FetchedPage(
        source_url="https://en.wikipedia.org/wiki/Biskra_Mohamed_Khider_Airport",
        source_name="Wikipedia",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "Biskra Mohamed Khider Airport statistics report "
            "500,000 passengers handled during the year in Argentina."
        ),
    )

    assert backfill._best_extraction(prop, generic_page) is None
    extraction = backfill._best_extraction(prop, specific_page)
    assert extraction is not None
    assert extraction.field_group == "annual_passenger_throughput"


def test_airport_metric_requires_target_country_context() -> None:
    prop = {
        "canonical_name": "La Plata Airport",
        "country": "Argentina",
        "scene_type": "airport_terminal",
    }
    wrong_country_page = FetchedPage(
        source_url="https://www.durangoco.gov/m/newsflash/Home/Detail/4495",
        source_name="www.durangoco.gov",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "Durango-La Plata County Airport in Colorado reported "
            "563,009 passengers during the year."
        ),
    )

    assert backfill._best_extraction(prop, wrong_country_page) is None


def test_mall_metric_rejects_similar_named_wrong_property() -> None:
    prop = {
        "canonical_name": "Shopping Metrô Tatuapé",
        "country": "Brazil",
        "scene_type": "mall_mixed_use",
    }
    wrong_mall_page = FetchedPage(
        source_url="https://getoccupi.com/malls/shopping-metro-itaquera",
        source_name="getoccupi.com",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "Shopping Metrô Itaquera, São Paulo, Brazil. "
            "This mall has 68,470 square meters of GLA and high footfall."
        ),
    )

    assert backfill._best_extraction(prop, wrong_mall_page) is None


def test_same_name_stadium_requires_page_location_identity() -> None:
    prop = {
        "canonical_name": "Estadio Monumental",
        "country": "Chile",
        "city": "Santiago",
        "scene_type": "stadium",
    }
    wrong_country_page = FetchedPage(
        source_url="https://en.wikipedia.org/wiki/Estadio_Monumental_(Buenos_Aires)",
        source_name="Wikipedia",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "Estadio Monumental, Buenos Aires, Argentina. "
            "The stadium has a capacity of 68,000 spectators. "
            "Some international matches against Chile are listed in the article history."
        ),
    )

    assert backfill._best_extraction(prop, wrong_country_page) is None


def test_ai_aggregator_page_is_not_hard_evidence_source() -> None:
    prop = {
        "canonical_name": "Stade Henri Sylvoz",
        "country": "Gabon",
        "city": "Moanda",
        "scene_type": "stadium",
    }
    page = FetchedPage(
        source_url="https://grokipedia.com/page/stade_henri_sylvoz",
        source_name="grokipedia.com",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "Stade Henri Sylvoz is a stadium in Moanda, Gabon. "
            "The venue has a total capacity of 9,500 spectators."
        ),
    )

    assert backfill._best_extraction(prop, page) is None


def test_tiny_gla_fragment_is_not_hard_primary_metric() -> None:
    assert not backfill._plausible("mall_mixed_use", "gla", "8.3 sqm")


def test_stadium_seat_count_rejects_local_seating_fragments() -> None:
    assert not backfill._plausible("stadium", "seat_count", "750 seats")
    assert backfill._plausible("stadium", "seat_count", "20,000 spectators")


def test_quarter_chart_value_is_not_annual_airport_metric() -> None:
    assert not backfill._plausible(
        "airport_terminal",
        "annual_passenger_throughput",
        "GCAA 2024 domestic passenger throughput chart: 120,208 passengers.",
    )
    assert backfill._plausible(
        "airport_terminal",
        "annual_passenger_throughput",
        (
            "2024 annual passenger throughput derived by summing quarterly totals: "
            "Q1 116,103 + Q2 104,660 + Q3 112,504 + Q4 120,208 = 453,475 passengers."
        ),
    )


def test_russian_metric_values_are_plausible_and_scaled() -> None:
    assert backfill._plausible(
        "airport_terminal",
        "annual_passenger_throughput",
        "28,4 млн пассажиров",
    )
    assert backfill._plausible("stadium", "seat_count", "68 000 зрителей")
    assert backfill._plausible("stadium", "seat_count", "63 145 зрительских мест")
    assert backfill._plausible("mall_mixed_use", "gla", "90 000 кв. м")
    assert backfill._annual_visits_from_value(
        "annual_passenger_throughput",
        "28,4 млн пассажиров",
    ) == 28_400_000


def test_hotel_room_metric_rejects_review_fragments() -> None:
    assert backfill._plausible(
        "luxury_hotel_mice",
        "keys",
        "410 уютных стильных номеров",
    )
    assert not backfill._plausible(
        "luxury_hotel_mice",
        "keys",
        "46 Отличное обслуживание хорошие номера",
    )
    assert not backfill._plausible(
        "luxury_hotel_mice",
        "keys",
        "49 Poor 28 Terrible 39 Rooms",
    )


def test_tripadvisor_is_not_hard_evidence_source() -> None:
    page = FetchedPage(
        source_url="https://www.tripadvisor.com/Hotel_Review-example",
        source_name="tripadvisor.com",
        source_tier=SourceTier.TIER_3,
        content_text="The hotel has 410 rooms.",
    )
    assert backfill._low_value_evidence_source(page)


def test_hotel_room_count_must_be_near_target_property_on_multi_hotel_page() -> None:
    prop = {
        "canonical_name": "InterContinental Moscow Tverskaya",
        "country": "Russia",
        "city": "Moscow",
        "scene_type": "luxury_hotel_mice",
    }
    page = FetchedPage(
        source_url="https://ru.hotel.report/promotion/intercontinental-history",
        source_name="ru.hotel.report",
        source_tier=SourceTier.TIER_2,
        content_text=(
            "Crowne Plaza Moscow World Trade Center (575 номеров) opened first. "
            "The next project was InterContinental Moscow Tverskaya (205 номеров), "
            "located in Moscow, Russia."
        ),
    )

    extraction = backfill._best_extraction(prop, page)

    assert extraction is not None
    assert extraction.field_value == "205 номеров"


def test_russian_property_and_location_identity_match_after_transliteration() -> None:
    prop = {
        "canonical_name": "Aviapark",
        "country": "Russia",
        "city": "Moscow",
        "scene_type": "mall_mixed_use",
    }
    page = FetchedPage(
        source_url="https://example.ru/aviapark",
        source_name="example.ru",
        source_tier=SourceTier.TIER_2,
        content_text=(
            "ТРЦ Авиапарк расположен в Москве, Россия. "
            "Арендопригодная площадь комплекса составляет 230 000 кв. м."
        ),
    )

    extraction = backfill._best_extraction(prop, page)

    assert extraction is not None
    assert extraction.field_group == "gla"


def test_russian_romanization_variant_matches_property_identity() -> None:
    assert backfill._mentions_property(
        "Akademicheskaya Metro Station",
        "Станция метро Академическая расположена в Москве, Россия.",
    )
    assert backfill._mentions_property(
        "Tsentralnyy stadion Dinamo im. Lva Yashina",
        "VTB Arena - Central Stadium Dynamo named after Lev Yashin, Moscow, Russia.",
    )
    assert backfill._mentions_city("Saint Petersburg", "Санкт-Петербург, Россия")


def test_single_property_factsheet_allows_capacity_table_without_repeated_name() -> None:
    prop = {
        "canonical_name": "Tsentralnyy stadion Dinamo im. Lva Yashina",
        "country": "Russia",
        "city": "Moscow",
        "scene_type": "stadium",
    }
    page = FetchedPage(
        source_url="https://stadiumdb.com/stadiums/rus/vtb_arena",
        source_name="stadiumdb.com",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "VTB Arena - Central Stadium Dynamo named after Lev Yashin, Moscow, Russia.\n"
            "| Capacity | 25 716 |"
        ),
    )

    extraction = backfill._best_extraction(prop, page)

    assert extraction is not None
    assert extraction.field_group == "seat_count"
    assert extraction.field_value == "Capacity | 25 716"


def test_render_report_handles_skipped_low_evidence_labels() -> None:
    summary = {
        "gap_summary_before": {
            "scene_summary": {
                "stadium": {
                    "hard_primary_metric_properties": 1,
                    "total": 2,
                    "low_primary_metric_evidence_properties": 1,
                }
            }
        },
        "gap_summary_after": {
            "scene_summary": {
                "stadium": {
                    "hard_primary_metric_properties": 1,
                    "total": 2,
                    "low_primary_metric_evidence_properties": 1,
                }
            }
        },
        "searches_used": 1,
        "max_searches": 10,
        "audit_log_path": "events.jsonl",
        "reused_search_result_sets": 0,
        "reused_pages": 0,
        "draft_count": 0,
        "drafts_by_scene": {},
        "raw_evidence_written_or_changed": 0,
        "labels": {"skipped": "no_overlay_sync"},
        "search_log_path": "search.jsonl",
    }

    report = backfill._render_report(summary)

    assert "Low evidence labels: no_overlay_sync" in report
