from isite2.connectors.extraction import extract_indicators
from isite2.connectors.models import FetchedPage
from isite2.domain.enums import SourceTier
from isite2.growth.localized_search_strategy import (
    build_localized_queries,
    country_language_profile,
    localized_metric_terms,
)


def test_brazil_queries_use_portuguese_terms_before_firecrawl() -> None:
    queries = build_localized_queries(
        country="Brazil",
        city="Sao Paulo",
        property_name="Sao Paulo Guarulhos Airport",
        scene_type="airport_terminal",
    )

    assert queries
    assert all(not query.use_firecrawl for query in queries)
    assert all(not query.scrape for query in queries)
    assert any(query.language == "pt" and "passageiros" in query.query for query in queries)
    assert any(".gov.br" in query.query for query in queries)


def test_firecrawl_gap_fill_is_explicit_last_resort() -> None:
    queries = build_localized_queries(
        country="Brazil",
        property_name="Sao Paulo Guarulhos Airport",
        scene_type="airport_terminal",
        phases=["firecrawl_gap_fill"],
    )

    assert queries
    assert all(query.use_firecrawl for query in queries)
    assert all(not query.scrape for query in queries)
    assert all(query.limit == 2 for query in queries)
    assert all(query.firecrawl_mode == "last_resort" for query in queries)


def test_country_profiles_fallback_and_spanish_terms() -> None:
    profile = country_language_profile("Argentina")
    terms = localized_metric_terms(
        country="Argentina",
        scene_type="stadium",
        language="es",
    )
    fallback_terms = localized_metric_terms(
        country="Unknownland",
        scene_type="stadium",
    )

    assert profile["languages"] == ["es", "en"]
    assert "capacidad" in terms
    assert "seats" in fallback_terms


def test_extraction_accepts_localized_quantitative_terms() -> None:
    page = FetchedPage(
        source_url="https://example.gov.br/report",
        source_name="Official Report",
        source_tier=SourceTier.TIER_1,
        content_text=(
            "O aeroporto registrou 43,6 milhões passageiros em 2024. "
            "O estádio tem 55.000 lugares."
        ),
    )

    results = extract_indicators(
        page,
        property_name="Example Venue",
        preferred_indicators=["annual_passenger_throughput", "seat_count"],
    )

    assert {result.field_group for result in results} == {
        "annual_passenger_throughput",
        "seat_count",
    }
    assert any("43,6 milhões passageiros" in result.field_value for result in results)
    assert any("55.000 lugares" in result.field_value for result in results)


def test_extraction_rejects_quarter_total_as_annual_passenger_metric() -> None:
    page = FetchedPage(
        source_url="https://example.gov.gh/kumasi-2024-chart.pdf",
        source_name="Ghana Civil Aviation Authority",
        source_tier=SourceTier.TIER_1,
        content_text=(
            "2024 GENERAL STATISTICS - DOMESTIC - PER AIRPORT. "
            "KUMASI - 2024 PASSENGER THRUPUT. "
            "4th Quarter Total 120,208 passengers."
        ),
    )

    results = extract_indicators(
        page,
        property_name="Kumasi Airport",
        preferred_indicators=["annual_passenger_throughput"],
    )

    assert results[0].extraction_status == "review_required"
    assert results[0].field_value == ""


def test_extraction_allows_quarter_sum_as_annual_passenger_metric() -> None:
    page = FetchedPage(
        source_url="https://example.gov.gh/kumasi-2024-chart.pdf",
        source_name="Ghana Civil Aviation Authority",
        source_tier=SourceTier.TIER_1,
        content_text=(
            "2024 annual passenger throughput derived by summing quarterly totals: "
            "Q1 116,103 + Q2 104,660 + Q3 112,504 + Q4 120,208 = 453,475 passengers."
        ),
    )

    results = extract_indicators(
        page,
        property_name="Kumasi Airport",
        preferred_indicators=["annual_passenger_throughput"],
    )

    assert results[0].field_group == "annual_passenger_throughput"
    assert results[0].field_value == "453,475 passengers"
