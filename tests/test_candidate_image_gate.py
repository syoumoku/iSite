from types import SimpleNamespace

from scripts import run_apac_image_and_duplicate_gate as image_gate
from scripts.run_apac_image_and_duplicate_gate import _image_check_is_retryable


def test_image_open_connection_reset_is_retryable() -> None:
    assert _image_check_is_retryable(
        {
            "ok": False,
            "status_code": None,
            "reason": "[Errno 54] Connection reset by peer",
        }
    )


def test_image_open_timeout_is_retryable() -> None:
    assert _image_check_is_retryable(
        {
            "ok": False,
            "status_code": None,
            "reason": "ReadTimeout: timed out while opening image",
        }
    )


def test_candidate_image_gate_rejects_known_ai_generated_image_host() -> None:
    assert image_gate._image_is_invalid(
        {
            "hero_image": {
                "url": "https://www.kupi.com/kland-storage/images/airport.webp",
                "source_name": "Kupi airport guide image",
                "source_url": "https://www.kupi.com/explore/airport",
            }
        }
    )


def test_candidate_source_page_urls_exclude_direct_images() -> None:
    candidate = {
        "source_url": "https://hotel.example/grand",
        "hero_image": {
            "source_url": "https://hotel.example/gallery",
            "url": "https://cdn.example/grand.jpg",
        },
        "evidence": [
            {"source_url": "https://hotel.example/grand/facts"},
            {"source_url": "https://cdn.example/direct.png"},
        ],
    }

    assert image_gate._candidate_source_page_urls(candidate) == [
        "https://hotel.example/gallery",
        "https://hotel.example/grand",
        "https://hotel.example/grand/facts",
    ]


def test_scrapling_page_image_search_accepts_property_page_image(monkeypatch, tmp_path) -> None:
    class FakeScraplingProvider:
        def __init__(self, **kwargs) -> None:
            self.stats = SimpleNamespace(
                fetch_requests=1,
                static_fetch_count=1,
                dynamic_fetch_count=0,
                stealth_fetch_count=0,
                cache_hits=0,
                robots_disallowed_count=0,
                restricted_page_count=0,
                artifact_write_count=1,
                failures=[],
                warnings=[],
            )

        def extract_image_candidates(self, url: str):
            return [
                {
                    "imageUrl": "https://assets.example/grand-hotel-accra.jpg",
                    "title": "Grand Hotel Accra official exterior",
                    "url": url,
                    "imageWidth": 1200,
                }
            ]

    monkeypatch.setattr(image_gate, "ScraplingPublicEvidenceProvider", FakeScraplingProvider)
    monkeypatch.setattr(image_gate, "_accepted_image_opens", lambda url, cache_dir: True)
    candidate = {
        "property_name": "Grand Hotel",
        "city": "Accra",
        "scene_type": "luxury_hotel_mice",
        "evidence": [{"source_url": "https://hotel.example/grand-hotel-accra"}],
    }

    result = image_gate._search_image_for_candidate(
        "Ghana",
        candidate,
        image_limit=5,
        cache_dir=tmp_path,
        source_date="2026-06-18",
    )

    assert result["provider"] == "scrapling_page_images"
    assert result["accepted_image"]["url"] == "https://assets.example/grand-hotel-accra.jpg"
    assert "firecrawl" not in str(result["cache_path"])


def test_shared_scrapling_provider_fetches_a_common_source_page_once(monkeypatch, tmp_path) -> None:
    class FakeScraplingProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.stats = SimpleNamespace(
                fetch_requests=0,
                static_fetch_count=0,
                dynamic_fetch_count=0,
                stealth_fetch_count=0,
                cache_hits=0,
                robots_disallowed_count=0,
                restricted_page_count=0,
                artifact_write_count=0,
                failures=[],
                warnings=[],
            )

        def extract_image_candidates(self, url: str):
            self.calls += 1
            return [
                {
                    "imageUrl": "https://assets.example/shared-property-page.jpg",
                    "title": "Shared property page",
                    "url": url,
                    "imageWidth": 1200,
                    "imageHeight": 800,
                }
            ]

    provider = FakeScraplingProvider()
    cache = image_gate._SourcePageImageCache(provider)

    first = cache.extract("https://example.org/shared")
    second = cache.extract("https://example.org/shared")

    assert first == second
    assert provider.calls == 1


def test_local_official_image_query_uses_country_language_terms() -> None:
    candidate = {
        "property_name": "Centro Santa Fe",
        "city": "Ciudad de Mexico",
        "scene_type": "mall_mixed_use",
    }

    query = image_gate._image_query("Mexico", candidate, profile="local_official")

    assert "centro comercial fachada" in query
    assert "sitio oficial" in query


def test_image_open_timeout_can_be_tuned_for_batch_runs(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_check(url, cache_dir, timeout_seconds):
        captured["timeout_seconds"] = timeout_seconds
        return {"ok": True}

    monkeypatch.setenv("ISITE2_IMAGE_OPEN_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setattr(image_gate, "_check_image_url_variants_open", fake_check)

    assert image_gate._accepted_image_opens("https://example.org/property.jpg", tmp_path)
    assert captured["timeout_seconds"] == 2.5


def test_three_unique_failed_image_attempts_allow_explicit_missing_image_release() -> None:
    candidate = {}
    failed = {"provider": "local_search", "query": "q", "reason": "no match"}

    for attempt_id in ("source-pages", "exact-search", "local-official"):
        image_gate._record_image_search_attempt(candidate, attempt_id, failed)

    assert candidate["image_search_attempt_count"] == 3
    assert candidate["image_status"] == "unavailable_after_three_searches"

    image_gate._record_image_search_attempt(candidate, "local-official", failed)
    assert candidate["image_search_attempt_count"] == 3


def test_successful_image_attempt_marks_image_verified() -> None:
    candidate = {}
    image_gate._record_image_search_attempt(
        candidate,
        "exact-search",
        {
            "provider": "local_search",
            "query": "q",
            "accepted_image": {"url": "https://example.org/property.jpg"},
        },
    )

    assert candidate["image_status"] == "verified"


def test_scrapling_page_image_search_rejects_unrelated_page_asset(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(image_gate, "_accepted_image_opens", lambda url, cache_dir: True)
    candidate = {
        "property_name": "Cairo Military Academy Stadium",
        "city": "Cairo",
        "country": "Egypt",
        "scene_type": "stadium",
    }
    payload = {
        "provider": "scrapling_page_images",
        "data": {
            "images": [
                {
                    "imageUrl": "https://stadiumdb.com/img/b/dallas-poster1.jpg",
                    "title": "Cairo Military Academy Stadium, Cairo Egypt",
                    "url": "https://stadiumdb.com/stadiums/egy/cairo_military_academy_stadium",
                    "imageWidth": 1200,
                    "imageHeight": 675,
                }
            ]
        },
    }

    assert image_gate._choose_image(candidate, payload, "2026-08-05", tmp_path) is None


def test_image_search_accepts_distinctive_exact_property_without_city(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(image_gate, "_accepted_image_opens", lambda url, cache_dir: True)
    candidate = {
        "property_name": "Dandy Mega Mall",
        "city": "Giza",
        "country": "Egypt",
        "scene_type": "mall_mixed_use",
    }
    payload = {
        "provider": "firecrawl_image_search",
        "data": {
            "images": [
                {
                    "imageUrl": "https://dandymegamall.com/images/dandy-exterior.jpg",
                    "title": "About | Dandy Mega Mall",
                    "url": "https://dandymegamall.com/about",
                    "imageWidth": 1600,
                    "imageHeight": 900,
                }
            ]
        },
    }

    image = image_gate._choose_image(candidate, payload, "2026-08-05", tmp_path)

    assert image is not None
    assert image["url"].endswith("dandy-exterior.jpg")


def test_image_search_prefers_city_specific_airport_over_other_airport(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(image_gate, "_accepted_image_opens", lambda url, cache_dir: True)
    candidate = {
        "property_name": "Aswan International Airport",
        "city": "Aswan",
        "country": "Egypt",
        "scene_type": "airport_terminal",
    }
    payload = {
        "provider": "firecrawl_image_search",
        "data": {
            "images": [
                {
                    "imageUrl": "https://cdn.example/cochin-airport.jpg",
                    "title": "Travel Club Lounge at Cochin International Airport",
                    "url": "https://example.com/cochin-airport",
                    "imageWidth": 2000,
                    "imageHeight": 1500,
                },
                {
                    "imageUrl": "https://cdn.example/Aswan-International-Airport.jpg",
                    "title": "Egypt airports guide",
                    "url": "https://example.com/egypt-airports",
                    "imageWidth": 1200,
                    "imageHeight": 600,
                },
            ]
        },
    }

    image = image_gate._choose_image(candidate, payload, "2026-08-05", tmp_path)

    assert image is not None
    assert image["url"].endswith("Aswan-International-Airport.jpg")


def test_image_search_rejects_low_value_property_adjacent_assets(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(image_gate, "_accepted_image_opens", lambda url, cache_dir: True)
    cases = [
        (
            {
                "property_name": "October 6 University",
                "city": "Giza",
                "country": "Egypt",
                "scene_type": "university",
            },
            "https://www.unirank.org/i/university-control-type-private-48x48.png",
            "October 6 University's Control Type",
            "https://www.unirank.org/eg/uni/october-6-university/",
        ),
        (
            {
                "property_name": "Saudi German Hospital Cairo",
                "city": "Cairo",
                "country": "Egypt",
                "scene_type": "hospital",
            },
            "https://cdn.example/hotel-near-hospital.webp",
            "10 best hotels near Saudi German Hospital Cairo",
            "https://travel.example/hotels-near-saudi-german-hospital-cairo",
        ),
        (
            {
                "property_name": "Sharm El Sheikh Cruise Port",
                "city": "Sharm El Sheikh",
                "country": "Egypt",
                "scene_type": "cruise_port",
            },
            "https://www.cruisemapper.com/images/ports/map/619.jpg",
            "Sharm El Sheikh cruise port",
            "https://www.cruisemapper.com/ports/sharm-el-sheikh-port-619",
        ),
        (
            {
                "property_name": "Police Academy Stadium",
                "city": "Cairo",
                "country": "Egypt",
                "scene_type": "stadium",
            },
            "https://images.pexels.com/photos/police-street.jpeg",
            "Nighttime Street Scene with Police Vehicle in Cairo",
            "https://www.pexels.com/photo/police-vehicle-in-cairo/",
        ),
        (
            {
                "property_name": "Future University in Egypt",
                "city": "Cairo",
                "country": "Egypt",
                "scene_type": "university",
            },
            "https://www.unirank.org/i/screenshots/future-university-eg-website-screenshot.jpg",
            "Future University in Egypt official website homepage screenshot",
            "https://www.unirank.org/eg/uni/future-university-in-egypt/",
        ),
        (
            {
                "property_name": "Port Said Cultural Entertainment Center",
                "city": "Port Said",
                "country": "Egypt",
                "scene_type": "convention_center",
            },
            "https://cdn.leonardo.ai/generations/gpt-image-cultural-center.jpg",
            "Create gate walls Port Said cultural center | Leonardo.Ai",
            "https://app.leonardo.ai/generation/cultural-center",
        ),
    ]

    for candidate, image_url, title, page_url in cases:
        payload = {
            "provider": "firecrawl_image_search",
            "data": {
                "images": [
                    {
                        "imageUrl": image_url,
                        "title": title,
                        "url": page_url,
                        "imageWidth": 1200,
                        "imageHeight": 800,
                    }
                ]
            },
        }
        assert image_gate._choose_image(candidate, payload, "2026-08-05", tmp_path) is None


def test_image_search_rejects_partial_museum_name_collision(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(image_gate, "_accepted_image_opens", lambda url, cache_dir: True)
    candidate = {
        "property_name": "National Museum of Egyptian Civilization Theatre",
        "city": "Cairo",
        "country": "Egypt",
        "scene_type": "convention_center",
    }
    payload = {
        "provider": "firecrawl_image_search",
        "data": {
            "images": [
                {
                    "imageUrl": "https://cdn.example/egyptian-museum.jpg",
                    "title": "The Egyptian Museum Cairo half day tour",
                    "url": "https://travel.example/egyptian-museum-cairo",
                    "imageWidth": 1200,
                    "imageHeight": 800,
                }
            ]
        },
    }

    assert image_gate._choose_image(candidate, payload, "2026-08-05", tmp_path) is None


def test_image_search_does_not_match_city_token_as_url_substring(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(image_gate, "_accepted_image_opens", lambda url, cache_dir: True)
    candidate = {
        "property_name": "Ministry of Foreign Affairs New Premises",
        "city": "New Administrative Capital",
        "country": "Egypt",
        "scene_type": "office_government",
    }
    payload = {
        "provider": "firecrawl_image_search",
        "data": {
            "images": [
                {
                    "imageUrl": "https://news.example/foreign-affairs-story.jpg",
                    "title": "Egypt urges citizens to avoid travelling",
                    "url": "https://news.example/NewsContent/Foreign-Affairs/story",
                    "imageWidth": 800,
                    "imageHeight": 500,
                }
            ]
        },
    }

    assert image_gate._choose_image(candidate, payload, "2026-08-05", tmp_path) is None
