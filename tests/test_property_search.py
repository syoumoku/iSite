from isite2.property_search import (
    normalize_property_search_text,
    property_search_document,
    rank_property_search_rows,
)


def test_property_search_normalizes_accents_punctuation_and_scripts() -> None:
    assert normalize_property_search_text("  Aéroport—d'Alger  ") == "aeroport d alger"
    assert normalize_property_search_text("北京首都国际机场") == "北京首都国际机场"
    assert normalize_property_search_text("مطار الجزائر") == "مطار الجزائر"
    assert normalize_property_search_text("Аэропорт Пулково") == "аэропорт пулково"
    assert normalize_property_search_text("New_Airport.Terminal") == "new airport terminal"


def test_property_search_ranks_canonical_before_alias_matches() -> None:
    rows = [
        {
            "property_id": "alias-exact",
            "property_name": "Houari Boumediene International Airport",
            "aliases": ["Aéroport d'Alger"],
            "country": "Algeria",
            "city": "Algiers",
            "scene_type": "airport_terminal",
        },
        {
            "property_id": "canonical-exact",
            "property_name": "Aeroport d Alger",
            "aliases": [],
            "country": "Algeria",
            "city": "Algiers",
            "scene_type": "airport_terminal",
        },
        {
            "property_id": "canonical-prefix",
            "property_name": "Aeroport d Alger Terminal 1",
            "aliases": [],
            "country": "Algeria",
            "city": "Algiers",
            "scene_type": "airport_terminal",
        },
    ]

    results = rank_property_search_rows(rows, "aéroport d'alger", limit=20)

    assert [row["property_id"] for row in results] == [
        "canonical-exact",
        "alias-exact",
        "canonical-prefix",
    ]
    assert results[1]["matched_name"] == "Aéroport d'Alger"
    assert results[1]["match_type"] == "alias_exact"
    assert "aeroport d alger" in property_search_document(
        rows[0]["property_name"], rows[0]["aliases"]
    )
