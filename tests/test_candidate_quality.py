from isite2.rules.candidate_quality import (
    BLOCKED_QUALITY,
    REVIEW_REQUIRED,
    READY,
    apply_designated_lead_primary_metric_exception,
    evaluate_candidate_quality,
    is_city_or_country_only_name,
)
from isite2.domain.models import CandidateVisibility


def test_candidate_quality_blocks_mvp_placeholder_name() -> None:
    quality = evaluate_candidate_quality(
        country="Exampleland",
        city="Beijing",
        property_name="Beijing 机场 MVP Candidate",
        scene_type="airport_terminal",
        geocode_precision="MVP fake centroid",
        source_urls=["https://example.com/isite2-mvp-evidence"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "property_name contains placeholder or fixture token" in quality.issues
    assert quality.visibility.map_ready is False
    assert quality.visibility.export_ready is False


def test_candidate_quality_blocks_city_only_wikipedia_pollution() -> None:
    quality = evaluate_candidate_quality(
        country="Malawi",
        city="Zomba",
        property_name="Beijing",
        scene_type="transport_hub",
        geocode_precision="gift",
        source_urls=["https://en.wikipedia.org/wiki/Beijing"],
        source_names=["en.wikipedia.org"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "property_name appears to be a location-only page title" in quality.issues
    assert "geocode_precision is not property-level for the scene" in quality.issues
    assert any("city/country/topic page" in issue for issue in quality.issues)


def test_candidate_quality_does_not_reject_real_property_with_city_token() -> None:
    quality = evaluate_candidate_quality(
        country="Egypt",
        city="Cairo",
        property_name="Cairo International Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://en.wikipedia.org/wiki/Cairo_International_Airport"],
        source_names=["Wikipedia API seed"],
    )

    assert quality.status == READY
    assert quality.issues == []


def test_candidate_quality_blocks_restricted_city_country_mismatch() -> None:
    quality = evaluate_candidate_quality(
        country="Ghana",
        city="São Paulo",
        property_name="Kumasi Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://en.wikipedia.org/wiki/Kumasi_Airport"],
        source_names=["Wikipedia"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["annual_passenger_throughput"],
        evidence_indicator_names=["annual_passenger_throughput"],
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert any("city-country mismatch" in issue for issue in quality.issues)


def test_candidate_quality_blocks_city_defaulted_to_country() -> None:
    quality = evaluate_candidate_quality(
        country="Ghana",
        city="Ghana",
        property_name="Kumasi Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://en.wikipedia.org/wiki/Kumasi_Airport"],
        source_names=["Wikipedia"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["annual_passenger_throughput"],
        evidence_indicator_names=["annual_passenger_throughput"],
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city appears defaulted to country" in quality.issues


def test_candidate_quality_blocks_country_alias_as_city() -> None:
    quality = evaluate_candidate_quality(
        country="Cote d'Ivoire",
        city="Ivory Coast",
        property_name="Bouaké Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://en.wikipedia.org/wiki/Bouak%C3%A9_Airport"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city is an administrative region, not an observed city/locality" in quality.issues


def test_candidate_quality_blocks_admin_region_as_city() -> None:
    quality = evaluate_candidate_quality(
        country="Maldives",
        city="Baa Atoll",
        property_name="Dusit Thani Maldives",
        scene_type="luxury_hotel_mice",
        geocode_precision="resort venue centroid",
        source_urls=["https://example.org/dusit-thani-maldives"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city is an administrative region, not an observed city/locality" in quality.issues


def test_candidate_quality_blocks_country_scoped_district_as_city() -> None:
    quality = evaluate_candidate_quality(
        country="Mexico",
        city="Cuauhtémoc",
        property_name="Reforma 222",
        scene_type="mall_mixed_use",
        geocode_precision="mall venue centroid",
        source_urls=["https://example.org/reforma-222"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city is an administrative region, not an observed city/locality" in quality.issues


def test_candidate_quality_blocks_vietnam_region_or_province_as_city() -> None:
    quality = evaluate_candidate_quality(
        country="Vietnam",
        city="South Central Coast",
        property_name="Da Nang International Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://example.org/da-nang-airport"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city is an administrative region, not an observed city/locality" in quality.issues

    province_quality = evaluate_candidate_quality(
        country="Vietnam",
        city="Khanh Hoa",
        property_name="Duyen Ha Resort Cam Ranh",
        scene_type="luxury_hotel_mice",
        geocode_precision="hotel venue centroid",
        source_urls=["https://example.org/duyen-ha-resort-cam-ranh"],
    )

    assert province_quality.status == BLOCKED_QUALITY
    assert (
        "city is an administrative region, not an observed city/locality"
        in province_quality.issues
    )

    airport_province_quality = evaluate_candidate_quality(
        country="Vietnam",
        city="Quang Binh",
        property_name="Dong Hoi Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://example.org/dong-hoi-airport"],
    )

    assert airport_province_quality.status == BLOCKED_QUALITY
    assert (
        "city is an administrative region, not an observed city/locality"
        in airport_province_quality.issues
    )


def test_candidate_quality_blocks_algeria_province_as_city() -> None:
    quality = evaluate_candidate_quality(
        country="Algeria",
        city="Annaba Province",
        property_name="Annaba Rabah Bitat Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://example.org/annaba-airport"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city is an administrative region, not an observed city/locality" in quality.issues


def test_candidate_quality_blocks_entity_id_as_city() -> None:
    quality = evaluate_candidate_quality(
        country="Turkey",
        city="Q207998",
        property_name="Dalaman Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://example.org/dalaman-airport"],
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city contains an entity id rather than an observed city/locality" in quality.issues


def test_candidate_quality_blocks_proxy_metric_as_objective_evidence() -> None:
    quality = evaluate_candidate_quality(
        country="Ghana",
        city="Accra",
        property_name="Kotoka International Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://en.wikipedia.org/wiki/Kotoka_International_Airport"],
        source_names=["Wikipedia"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["annual_passenger_throughput"],
        evidence_indicator_names=["annual_passenger_throughput"],
        evidence_types=["Proxy"],
        evidence_values=["Annual passenger throughput proxy: 3,000,000 passengers/year"],
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert "scene objective evidence metric missing or defaulted" in quality.issues


def test_candidate_quality_allows_restricted_city_in_allowed_country() -> None:
    quality = evaluate_candidate_quality(
        country="Brazil",
        city="São Paulo",
        property_name="São Paulo/Guarulhos International Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://en.wikipedia.org/wiki/S%C3%A3o_Paulo/Guarulhos_International_Airport"],
        source_names=["Wikipedia"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["annual_passenger_throughput"],
        evidence_indicator_names=["annual_passenger_throughput"],
        require_surface_assets=True,
    )

    assert quality.status == READY
    assert quality.issues == []


def test_surface_quality_allows_missing_hero_image_but_blocks_missing_scene_metric() -> None:
    quality = evaluate_candidate_quality(
        country="Benin",
        city="Cotonou",
        property_name="Cardinal Bernadin Gantin International Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal",
        source_urls=["https://en.wikipedia.org/wiki/Cotonou_Cadjehoun_Airport"],
        source_names=["Wikipedia"],
        evidence_field_groups=["airport_role"],
        evidence_indicator_names=["iata_code"],
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert "hero_image missing or invalid for UI map surface" not in quality.issues
    assert "scene objective evidence metric missing" in quality.issues
    assert quality.visibility.map_ready is False


def test_surface_quality_blocks_unverified_city_assignment() -> None:
    quality = evaluate_candidate_quality(
        country="Mexico",
        city="Acolman",
        property_name="Example General Hospital",
        scene_type="hospital",
        geocode_precision="property centroid",
        source_urls=["https://hospital.example/facts"],
        source_names=["Official hospital profile"],
        evidence_field_groups=["licensed_beds"],
        evidence_indicator_names=["licensed_beds"],
        city_assignment_status="unmapped",
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert "city assignment is not verified" in quality.issues


def test_designated_lead_exception_only_allows_missing_primary_metric() -> None:
    quality = evaluate_candidate_quality(
        country="Algeria",
        city="Algiers",
        property_name="Designated Office Tower",
        scene_type="office_government",
        geocode_precision="office building centroid",
        source_urls=["https://example.org/designated-office"],
        source_names=["Official property profile"],
        evidence_field_groups=["property_identity"],
        evidence_indicator_names=["property_identity"],
        evidence_types=["Context"],
        evidence_values=["Exact building identity confirmed"],
        require_surface_assets=True,
    )
    packet = type(
        "PacketStub",
        (),
        {
            "candidate_quality_status": quality.status,
            "visibility": CandidateVisibility(),
            "quality_issues": [],
        },
    )()

    exception = apply_designated_lead_primary_metric_exception(packet, quality)

    assert exception.status == REVIEW_REQUIRED
    assert exception.visibility.map_ready is True
    assert exception.visibility.review_required is True
    assert packet.quality_issues == ["scene objective evidence metric missing"]


def test_designated_lead_exception_does_not_hide_entity_or_city_defects() -> None:
    quality = evaluate_candidate_quality(
        country="Algeria",
        city="Algeria",
        property_name="MVP Candidate Office",
        scene_type="office_government",
        geocode_precision="province centroid",
        source_urls=["https://example.org/not-a-property"],
        evidence_field_groups=["property_identity"],
        evidence_indicator_names=["property_identity"],
        require_surface_assets=True,
    )
    packet = type(
        "PacketStub",
        (),
        {
            "candidate_quality_status": quality.status,
            "visibility": CandidateVisibility(),
            "quality_issues": [],
        },
    )()

    exception = apply_designated_lead_primary_metric_exception(packet, quality)

    assert exception.status == BLOCKED_QUALITY
    assert exception.visibility.map_ready is False


def test_surface_quality_blocks_airport_gateway_role_as_primary_metric() -> None:
    quality = evaluate_candidate_quality(
        country="Benin",
        city="Cotonou",
        property_name="Cardinal Bernadin Gantin International Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal",
        source_urls=["https://en.wikipedia.org/wiki/Cotonou_Cadjehoun_Airport"],
        source_names=["Wikipedia"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["terminal_role"],
        evidence_indicator_names=["gateway_role"],
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert "scene objective evidence metric missing" in quality.issues


def test_surface_quality_allows_hard_primary_metric_without_direct_annual_visits() -> None:
    quality = evaluate_candidate_quality(
        country="Ghana",
        city="Accra",
        property_name="Accra Sports Stadium",
        scene_type="stadium",
        geocode_precision="stadium venue centroid",
        source_urls=["https://example.org/accra-stadium-capacity"],
        source_names=["Example Stadium Authority"],
        hero_image_url="https://example.org/accra-sports-stadium.jpg",
        evidence_field_groups=["seat_count"],
        evidence_indicator_names=["seat_count"],
        evidence_types=["Direct"],
        evidence_values=["40,000 seats"],
        annual_visits_est=None,
        require_surface_assets=True,
    )

    assert quality.status == READY
    assert quality.issues == []


def test_surface_quality_allows_image_and_objective_scene_metric() -> None:
    quality = evaluate_candidate_quality(
        country="Benin",
        city="Cotonou",
        property_name="Cardinal Bernadin Gantin International Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal",
        source_urls=["https://en.wikipedia.org/wiki/Cotonou_Cadjehoun_Airport"],
        source_names=["Wikipedia"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["annual_passenger_throughput"],
        evidence_indicator_names=["annual_passenger_throughput"],
        require_surface_assets=True,
    )

    assert quality.status == READY
    assert quality.issues == []


def test_surface_quality_blocks_quarter_chart_as_annual_airport_metric() -> None:
    quality = evaluate_candidate_quality(
        country="Ghana",
        city="Kumasi",
        property_name="Kumasi Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://www.gcaa.com.gh/kumasi-2024-chart.pdf"],
        source_names=["Ghana Civil Aviation Authority"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["annual_passenger_throughput"],
        evidence_indicator_names=["annual_passenger_throughput"],
        evidence_types=["Direct"],
        evidence_values=["GCAA 2024 domestic passenger throughput chart: 120,208 passengers."],
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert any("chart-derived" in issue for issue in quality.issues)


def test_surface_quality_allows_annual_airport_metric_from_quarter_sum() -> None:
    quality = evaluate_candidate_quality(
        country="Ghana",
        city="Kumasi",
        property_name="Kumasi Airport",
        scene_type="airport_terminal",
        geocode_precision="airport terminal centroid",
        source_urls=["https://www.gcaa.com.gh/kumasi-2024-chart.pdf"],
        source_names=["Ghana Civil Aviation Authority"],
        hero_image_url="https://upload.wikimedia.org/wikipedia/commons/example.jpg",
        evidence_field_groups=["annual_passenger_throughput"],
        evidence_indicator_names=["annual_passenger_throughput"],
        evidence_types=["Direct"],
        evidence_values=[
            "2024 annual passenger throughput derived by summing quarterly totals: "
            "Q1 116,103 + Q2 104,660 + Q3 112,504 + Q4 120,208 = 453,475 passengers."
        ],
        require_surface_assets=True,
    )

    assert quality.status == READY
    assert quality.issues == []


def test_surface_quality_allows_mosque_area_or_visitor_metric() -> None:
    quality = evaluate_candidate_quality(
        country="United Arab Emirates",
        city="Abu Dhabi",
        property_name="Sheikh Zayed Grand Mosque",
        scene_type="mosque",
        geocode_precision="mosque building centroid",
        source_urls=["https://www.szgmc.gov.ae/en"],
        source_names=["Sheikh Zayed Grand Mosque Centre"],
        hero_image_url="https://example.org/sheikh-zayed-grand-mosque.jpg",
        evidence_field_groups=["mosque_area"],
        evidence_indicator_names=["mosque_area"],
        evidence_types=["Direct"],
        evidence_values=["Mosque area: 22,412 square meters."],
        require_surface_assets=True,
    )

    assert quality.status == READY
    assert quality.issues == []


def test_surface_quality_blocks_mosque_landmark_role_as_primary_metric() -> None:
    quality = evaluate_candidate_quality(
        country="United Arab Emirates",
        city="Dubai",
        property_name="Jumeirah Mosque",
        scene_type="mosque",
        geocode_precision="mosque building centroid",
        source_urls=["https://example.org/jumeirah-mosque"],
        source_names=["Tourism profile"],
        hero_image_url="https://example.org/jumeirah-mosque.jpg",
        evidence_field_groups=["landmark_role"],
        evidence_indicator_names=["landmark_role"],
        evidence_types=["Direct"],
        evidence_values=["One of Dubai's most photographed mosques."],
        require_surface_assets=True,
    )

    assert quality.status == BLOCKED_QUALITY
    assert "scene objective evidence metric missing" in quality.issues


def test_city_or_country_only_helper_is_exact_not_contains() -> None:
    assert is_city_or_country_only_name("Cairo", "Cairo", "Egypt")
    assert not is_city_or_country_only_name("Cairo International Airport", "Cairo", "Egypt")
