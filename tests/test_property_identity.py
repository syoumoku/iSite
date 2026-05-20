from isite2.growth.property_identity import (
    NEW_OPPORTUNITY,
    POSSIBLE_DUPLICATE,
    KnownOpportunityIndex,
    KnownOpportunityRecord,
    build_property_identity,
    property_identity_key,
)


def test_property_identity_key_normalizes_case_punctuation_and_report_suffixes() -> None:
    canonical = property_identity_key(
        country="Egypt",
        city="Cairo",
        property_name="Cairo International Airport",
        scene_type="airport_terminal",
    )
    variant = property_identity_key(
        country="EGYPT",
        city=" cairo ",
        property_name="Cairo Intl. Airport Official Annual Passenger Traffic Report",
        scene_type="airport_terminal",
    )

    assert variant == canonical


def test_property_identity_key_keeps_city_boundary() -> None:
    cairo = property_identity_key(
        country="Egypt",
        city="Cairo",
        property_name="City Center Mall",
        scene_type="mall_mixed_use",
    )
    alexandria = property_identity_key(
        country="Egypt",
        city="Alexandria",
        property_name="City Center Mall",
        scene_type="mall_mixed_use",
    )

    assert cairo != alexandria


def test_known_opportunity_index_flags_possible_duplicate_without_auto_merge() -> None:
    index = KnownOpportunityIndex(
        [
            KnownOpportunityRecord(
                identity=build_property_identity(
                    country="Egypt",
                    city="Cairo",
                    property_name="Cairo International Stadium",
                    scene_type="stadium",
                    latitude=30.069,
                    longitude=31.312,
                ),
                source="test",
            )
        ]
    )

    possible = index.match(
        country="Egypt",
        city="Cairo",
        property_name="Cairo International Sports Stadium",
        scene_type="stadium",
        latitude=30.069,
        longitude=31.312,
    )
    new = index.match(
        country="Egypt",
        city="Alexandria",
        property_name="Alexandria Stadium",
        scene_type="stadium",
        latitude=31.197,
        longitude=29.913,
    )

    assert possible.status == POSSIBLE_DUPLICATE
    assert new.status == NEW_OPPORTUNITY
