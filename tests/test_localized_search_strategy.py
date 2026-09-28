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


def test_firecrawl_gap_fill_prefers_city_constrained_query() -> None:
    queries = build_localized_queries(
        country="Ghana",
        city="Accra",
        property_name="Accra Mall",
        scene_type="mall_mixed_use",
        phases=["firecrawl_gap_fill"],
    )

    assert queries[0].query.startswith("Accra Mall Accra Ghana")


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


def test_country_profile_requires_localized_search_metadata() -> None:
    profile = country_language_profile("Russia")
    terms = localized_metric_terms(
        country="Russia",
        scene_type="airport_terminal",
        language="ru",
    )

    assert profile["languages"][0] == "ru"
    assert profile["official_languages"] == ["ru"]
    assert profile["search_engine_profile_required"] is True
    assert profile["primary_search_engines"] == ["Yandex", "Google cross-check"]
    assert "пассажиропоток" in terms


def test_guinea_and_libya_profiles_are_local_language_first() -> None:
    guinea = country_language_profile("Guinea")
    libya = country_language_profile("Libya")
    guinea_terms = localized_metric_terms(
        country="Guinea",
        scene_type="airport_terminal",
        language="fr",
    )
    libya_terms = localized_metric_terms(
        country="Libya",
        scene_type="stadium",
        language="ar",
    )

    assert guinea["languages"][0] == "fr"
    assert guinea["official_languages"] == ["fr"]
    assert "Google" in guinea["primary_search_engines"]
    assert "trafic passagers" in guinea_terms
    assert libya["languages"][0] == "ar"
    assert libya["official_languages"] == ["ar"]
    assert "Google" in libya["primary_search_engines"]
    assert "السعة" in libya_terms


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


def test_extraction_accepts_russian_quantitative_terms() -> None:
    page = FetchedPage(
        source_url="https://example.ru/report",
        source_name="Official Russian Report",
        source_tier=SourceTier.TIER_1,
        content_text=(
            "Пассажиропоток аэропорта составил 28,4 млн пассажиров в 2024 году. "
            "Стадион рассчитан на 68 000 зрителей. "
            "Номерной фонд гостиницы включает 302 современных номера. "
            "Арендопригодная площадь торгового центра составляет 90 000 кв. м."
        ),
    )

    results = extract_indicators(
        page,
        property_name="Example Russian Venue",
        preferred_indicators=[
            "annual_passenger_throughput",
            "seat_count",
            "rooms",
            "gla",
        ],
    )

    assert {result.field_group for result in results} == {
        "annual_passenger_throughput",
        "seat_count",
        "rooms",
        "gla",
    }
    assert any("28,4 млн пассажиров" in result.field_value for result in results)
    assert any("68 000 зрителей" in result.field_value for result in results)
    assert any("302 современных номера" in result.field_value for result in results)
    assert any("90 000 кв. м" in result.field_value for result in results)


def test_extraction_accepts_capacity_table_label_before_value() -> None:
    page = FetchedPage(
        source_url="https://stadiumdb.com/stadiums/rus/vtb_arena",
        source_name="StadiumDB",
        source_tier=SourceTier.TIER_3,
        content_text=(
            "VTB Arena - Central Stadium Dynamo named after Lev Yashin, Moscow, Russia.\n"
            "| Capacity | 25 716 |"
        ),
    )

    results = extract_indicators(
        page,
        property_name="VTB Arena",
        preferred_indicators=["seat_count"],
    )

    assert results[0].field_group == "seat_count"
    assert results[0].field_value == "Capacity | 25 716"


def test_room_extraction_does_not_cross_markdown_line_breaks() -> None:
    page = FetchedPage(
        source_url="https://example.ru/hotel",
        source_name="Hotel page",
        source_tier=SourceTier.TIER_1,
        content_text="image.jpg?w=793&h=52\n\nНомера\n\nОтель предлагает 144 современных номера.",
    )

    results = extract_indicators(page, "Example Hotel", ["rooms"])

    assert results[0].field_value == "144 современных номера"


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
