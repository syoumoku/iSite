from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "run_cvent_apac_candidate_expansion.py"
    spec = importlib.util.spec_from_file_location("cvent_candidate_expansion_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_current_cvent_markdown_card() -> None:
    module = _module()
    entry = r"""![Lake Victoria Serena Golf Resort & Spa](https://images.example/hotel.jpg)\
    \
    ### Lake Victoria Serena Golf Resort & Spa\
    \
    Kampala, UG•Resort•Independent / Other\
    \
    Select venue\
    \
    6\
    •\
    122\
    •\
    3,045 sq. ft.\
    Select venue](https://www.cvent.com/venues/en-US/kampala/resort/lake-victoria-serena-golf-resort-spa/venue-4b61183b-db71-4cbb-89ec-11b61f9d8392)"""

    venue = module._parse_entry(
        country="Uganda",
        country_code="UG",
        entry="- [" + entry,
        source_file="fixture.json",
        hero_index={},
    )

    assert venue is not None
    assert venue.property_name == "Lake Victoria Serena Golf Resort & Spa"
    assert venue.city == "Kampala"
    assert venue.venue_type == "Resort"
    assert venue.rooms == 122
    assert venue.meeting_space_sqft == 3045
    assert venue.hero_url == "https://images.example/hotel.jpg"


def test_parse_cvent_next_html_page() -> None:
    module = _module()
    html = """
    <html><body>
      <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"ItemList","itemListElement":[
          {"item":{"name":"Example Grand Hotel",
           "url":"https://www-eur.cvent.com/venues/en-US/dubai/hotel/example-grand-hotel/venue-123",
           "geo":{"latitude":25.2,"longitude":55.3}}}
        ]}
      </script>
      <article>
        <img src="/venues/_next/image?url=https%3A%2F%2Fimages.example%2Fhotel.jpg&amp;w=1920&amp;q=75"
             alt="Example Grand Hotel" />
        <a href="https://www-eur.cvent.com/venues/en-US/dubai/hotel/example-grand-hotel/venue-123?p=1">
          Example Grand Hotel
          <span title="Dubai, AE | Hotel | Example Brand">Dubai, AE • Hotel • Example Brand</span>
          Select venue 12 • 420 • 18,000 sq. ft. • 2024
        </a>
      </article>
    </body></html>
    """

    venues = module._parse_html_page(
        country="United Arab Emirates",
        country_code="AE",
        html_text=html,
        source_file="fixture.html",
    )

    assert len(venues) == 1
    assert venues[0].property_name == "Example Grand Hotel"
    assert venues[0].city == "Dubai"
    assert venues[0].venue_type == "Hotel"
    assert venues[0].rooms == 420
    assert venues[0].meeting_space_sqft == 18000
    assert venues[0].hero_url == "https://images.example/hotel.jpg"
    assert venues[0].latitude == 25.2
    assert venues[0].longitude == 55.3
