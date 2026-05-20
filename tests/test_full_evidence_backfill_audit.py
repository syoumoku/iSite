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


def test_tiny_gla_fragment_is_not_hard_primary_metric() -> None:
    assert not backfill._plausible("mall_mixed_use", "gla", "8.3 sqm")


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
