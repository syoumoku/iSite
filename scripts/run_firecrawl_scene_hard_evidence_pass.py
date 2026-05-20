from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_intake import (
    CandidateDraft,
    load_effective_source_registry,
    validate_candidate_draft,
)
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.property_identity import (
    KNOWN_PROPERTY,
    NEW_OPPORTUNITY,
    known_opportunity_index_from_registry,
    normalize_property_name,
)
from isite2.growth.regional_targets import REGION_COUNTRIES
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository

DB_PATH = Path("outputs/isite2_dev.db")
DB_URL = f"sqlite+pysqlite:///{DB_PATH}"
OUTPUT_DIR = Path("outputs") / "regional_scan_loop"
SOURCE_TYPE = "firecrawl_scene_hard_evidence_pass"
TODAY = "2026-05-13"
ANNUAL_VISIT_FIELDS = {
    "annual_visits",
    "annual_passenger_throughput",
    "passenger_throughput",
    "annual_footfall",
    "footfall",
    "daily_ridership",
    "interchange_volume",
}


@dataclass(frozen=True)
class ExistingEvidence:
    country: str
    property_name: str
    scene_type: str
    field_group: str
    indicator_name: str
    field_value: str
    source_name: str
    source_tier: str
    source_url: str
    source_date: str
    content_text: str
    annual_visits: float | None = None


@dataclass(frozen=True)
class NewCandidate:
    country: str
    city: str
    property_name: str
    scene_type: str
    field_group: str
    indicator_name: str
    field_value: str
    source_name: str
    source_tier: str
    source_url: str
    source_date: str
    content_text: str
    annual_visits: float | None
    latitude: float
    longitude: float
    geocode_precision: str
    map_source: str
    map_source_date: str
    hero_url: str
    hero_source_url: str
    hero_source_name: str


EXISTING_EVIDENCE = [
    ExistingEvidence(
        country="Egypt",
        property_name="Cairo International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="2024 passenger traffic: over 27.7 million passengers and 211,600 flights.",
        source_name="Ahram Online / Egypt Ministry of Civil Aviation statement",
        source_tier="Tier 2",
        source_url=(
            "https://english.ahram.org.eg/NewsContent/1/1235/544024/Egypt/"
            "Urban--Transport/Cairo-Int;l-Airport-records-highestever-daily-traf.aspx"
        ),
        source_date="2025-04-06",
        content_text=(
            "Ahram Online reported that Cairo Airport served over 27.7 million "
            "passengers and handled 211,600 flights in 2024, citing Egypt's "
            "Ministry of Civil Aviation."
        ),
        annual_visits=27_700_000.0,
    ),
    ExistingEvidence(
        country="Colombia",
        property_name="El Dorado International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="2024 passenger traffic: over 45 million passengers.",
        source_name="El Dorado International Airport official news",
        source_tier="Tier 1",
        source_url=(
            "https://eldorado.aero/en/comunicados/"
            "el-dorado-el-aeropuerto-numero-uno-en-america-latina-y-el-caribe-por-pasajeros-carga-y-aeronaves"
        ),
        source_date="2025-06-04",
        content_text=(
            "El Dorado's official news release states that in 2024 the airport "
            "welcomed over 45 million passengers."
        ),
        annual_visits=45_000_000.0,
    ),
    ExistingEvidence(
        country="South Africa",
        property_name="O. R. Tambo International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="Fiscal Apr 2023-Mar 2024 passenger traffic: over 17.8 million passengers.",
        source_name="Engineering News / ACSA statement",
        source_tier="Tier 2",
        source_url=(
            "https://www.engineeringnews.co.za/article/"
            "or-tambo-international-airport-among-top-ten-busiest-airports-in-middle-east-africa-region-2025-02-07"
        ),
        source_date="2025-02-07",
        content_text=(
            "Engineering News reported ACSA's statement that OR Tambo handled "
            "over 17.8 million passengers in the fiscal year from April 2023 to March 2024."
        ),
        annual_visits=17_800_000.0,
    ),
    ExistingEvidence(
        country="Mexico",
        property_name="Mexico City International Airport",
        scene_type="airport_terminal",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="ACI ranking article reports 45.36 million passengers for 2023.",
        source_name="Mexico Business News / ACI ranking article",
        source_tier="Tier 2",
        source_url="https://mexicobusiness.news/aerospace/news/mexico-city-airport-drops-50th-2024-global-ranking",
        source_date="2025-07-15",
        content_text=(
            "Mexico Business News, summarizing ACI's global passenger traffic ranking, "
            "reports Mexico City International Airport handled 45.36 million passengers in 2023."
        ),
        annual_visits=45_360_000.0,
    ),
    ExistingEvidence(
        country="Mexico",
        property_name="Estadio Azteca",
        scene_type="stadium",
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="StadiumDB 2026 World Cup profile lists tournament capacity of 83,000.",
        source_name="StadiumDB",
        source_tier="Tier 2",
        source_url="https://stadiumdb.com/tournaments/world_cup/2026/estadio_azteca",
        source_date=TODAY,
        content_text="StadiumDB lists Estadio Azteca tournament capacity as 83,000.",
        annual_visits=1_245_000.0,
    ),
    ExistingEvidence(
        country="South Africa",
        property_name="FNB Stadium",
        scene_type="stadium",
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="South African Tourism profile states FNB Stadium is a 94,736-seater.",
        source_name="South African Tourism",
        source_tier="Tier 2",
        source_url=(
            "https://www.southafrica.net/us/en/travel/article/"
            "fnb-stadium-step-into-the-calabash-one-of-africa-s-biggest-stadiums"
        ),
        source_date=TODAY,
        content_text=(
            "South African Tourism describes FNB Stadium as an iconic 94,736-seater "
            "and the largest venue in South Africa."
        ),
        annual_visits=1_421_040.0,
    ),
    ExistingEvidence(
        country="Egypt",
        property_name="Cairo International Stadium",
        scene_type="stadium",
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="StadiumDB lists capacity of 75,000.",
        source_name="StadiumDB",
        source_tier="Tier 2",
        source_url="https://stadiumdb.com/stadiums/egy/cairo_international_stadium",
        source_date=TODAY,
        content_text="StadiumDB's Cairo International Stadium profile lists capacity as 75,000.",
        annual_visits=1_125_000.0,
    ),
    ExistingEvidence(
        country="Argentina",
        property_name="La Bombonera",
        scene_type="stadium",
        field_group="seat_count",
        indicator_name="seat_count",
        field_value="The Stadium Guide lists capacity of 49,000.",
        source_name="The Stadium Guide",
        source_tier="Tier 2",
        source_url="https://www.stadiumguide.com/bombonera/",
        source_date=TODAY,
        content_text="The Stadium Guide lists La Bombonera capacity as 49,000.",
        annual_visits=735_000.0,
    ),
    ExistingEvidence(
        country="Mexico",
        property_name="Centro Citibanamex",
        scene_type="convention_center",
        field_group="exhibition_area",
        indicator_name="exhibition_area",
        field_value="Operating regulations list exhibition area of 34,283 square meters across four halls.",
        source_name="Centro Citibanamex operating regulations",
        source_tier="Tier 1",
        source_url=(
            "https://centrobanamex.mx/wp-content/themes/cititheme/img/"
            "CENTRO_CITIBANAMEX_REGLAMENTO_DE_OPERACIONES_EN.pdf"
        ),
        source_date=TODAY,
        content_text=(
            "Centro Citibanamex operating regulations state the venue has an "
            "exhibition area of 34,283 square meters distributed across four halls."
        ),
        annual_visits=342_830.0,
    ),
    ExistingEvidence(
        country="South Africa",
        property_name="Cape Town International Convention Centre",
        scene_type="convention_center",
        field_group="exhibition_area",
        indicator_name="exhibition_area",
        field_value="AIPC member profile lists total exhibition space of 21,399 m2.",
        source_name="AIPC member profile",
        source_tier="Tier 2",
        source_url="https://aipc.org/member/cape-town-international-convention-centre/",
        source_date=TODAY,
        content_text=(
            "AIPC's CTICC member profile lists total function space of 140,855 m2 "
            "and total exhibition space of 21,399 m2."
        ),
        annual_visits=250_000.0,
    ),
    ExistingEvidence(
        country="Nigeria",
        property_name="Eko Convention Centre",
        scene_type="convention_center",
        field_group="peak_event_capacity",
        indicator_name="peak_event_capacity",
        field_value="Eko Hotels and Suites profile says its convention centre can cater to 6,000 people.",
        source_name="Wikipedia public profile via Firecrawl scrape",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Eko_Hotels_and_Suites",
        source_date=TODAY,
        content_text=(
            "The Eko Hotels and Suites profile states that its convention centre "
            "can cater to 6,000 people."
        ),
        annual_visits=250_000.0,
    ),
]


NEW_CANDIDATES = [
    NewCandidate(
        country="Nigeria",
        city="Lagos",
        property_name="Eko Hotels and Suites",
        scene_type="luxury_hotel_mice",
        field_group="keys",
        indicator_name="keys",
        field_value="Public hotel profile lists 825 rooms and suites.",
        source_name="Wikipedia public profile via Firecrawl scrape",
        source_tier="Tier 3",
        source_url="https://en.wikipedia.org/wiki/Eko_Hotels_and_Suites",
        source_date=TODAY,
        content_text="Eko Hotels and Suites comprises 825 rooms and suites in Lagos.",
        annual_visits=742_500.0,
        latitude=6.42253,
        longitude=3.42720194,
        geocode_precision="hotel venue centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Eko%20Hotels%20%26%20Suites%20Building.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Eko_Hotels_%26_Suites_Building.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Kenya",
        city="Nairobi",
        property_name="Trademark Hotel",
        scene_type="luxury_hotel_mice",
        field_group="keys",
        indicator_name="keys",
        field_value="Official hotel page describes Trademark as a 215-room urban business hotel.",
        source_name="Trademark Hotel official site",
        source_tier="Tier 1",
        source_url="https://www.trademark-hotel.com/",
        source_date=TODAY,
        content_text="Trademark Hotel's official site describes it as a 215-room urban business hotel.",
        annual_visits=250_000.0,
        latitude=-1.230726111,
        longitude=36.804303055,
        geocode_precision="hotel venue centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Trademark%20Hotel%20entrance%20during%20Wikimania%202025.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Trademark_Hotel_entrance_during_Wikimania_2025.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Argentina",
        city="Buenos Aires",
        property_name="Unicenter Shopping",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 220,000 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q920787",
        source_date=TODAY,
        content_text="Unicenter Shopping has a Wikidata P2046 area value of 220,000 square meters.",
        annual_visits=4_400_000.0,
        latitude=-34.5086,
        longitude=-58.5239,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Unicenter%20entrada%20lateral.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Unicenter_entrada_lateral.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Brazil",
        city="Salvador",
        property_name="Shopping da Bahia",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists gross leasable area of 65,281 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q10300989",
        source_date=TODAY,
        content_text="Shopping da Bahia has a Wikidata gross leasable area value of 65,281 square meters.",
        annual_visits=1_305_620.0,
        latitude=-12.98196944,
        longitude=-38.46390278,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Vista%20a%C3%A9rea%20do%20Shopping%20Iguatemi%20-%20detalhe.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Vista_a%C3%A9rea_do_Shopping_Iguatemi_-_detalhe.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Chile",
        city="Santiago",
        property_name="Mall Parque Arauco",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 119,500 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q16597095",
        source_date=TODAY,
        content_text="Mall Parque Arauco has a Wikidata P2046 area value of 119,500 square meters.",
        annual_visits=2_390_000.0,
        latitude=-33.40208056,
        longitude=-70.57804167,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Parque%20Arauco%2C%20Las%20Condes%2C%20Santiago%2020230421%2002.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Parque_Arauco,_Las_Condes,_Santiago_20230421_02.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Argentina",
        city="Buenos Aires",
        property_name="Alvear Palace Hotel",
        scene_type="luxury_hotel_mice",
        field_group="keys",
        indicator_name="keys",
        field_value="Travel Weekly hotel profile lists 207 rooms.",
        source_name="Travel Weekly hotel profile",
        source_tier="Tier 2",
        source_url="https://www.travelweekly.com/Hotels/Buenos-Aires/Alvear-Palace-Hotel-p4146623",
        source_date=TODAY,
        content_text="Travel Weekly's Alvear Palace Hotel profile lists 207 rooms.",
        annual_visits=250_000.0,
        latitude=-34.58769444,
        longitude=-58.38880556,
        geocode_precision="hotel venue centroid",
        map_source="Wikipedia coordinate statement",
        map_source_date=TODAY,
        hero_url="https://upload.wikimedia.org/wikipedia/commons/5/52/Buenos_Aires_-_Avenida_Alvear_-_20090104-r.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Buenos_Aires_-_Avenida_Alvear_-_20090104-r.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Argentina",
        city="Buenos Aires",
        property_name="Sheraton Buenos Aires Hotel & Convention Center",
        scene_type="luxury_hotel_mice",
        field_group="keys",
        indicator_name="keys",
        field_value="Travel Weekly hotel profile lists 740 rooms.",
        source_name="Travel Weekly hotel profile",
        source_tier="Tier 2",
        source_url="https://www.travelweekly.com/Hotels/Buenos-Aires/Sheraton-Buenos-Aires-Hotel-Conv-Ctr-p4051823",
        source_date=TODAY,
        content_text="Travel Weekly's Sheraton Buenos Aires Hotel & Convention Center profile lists 740 rooms.",
        annual_visits=666_000.0,
        latitude=-34.59305556,
        longitude=-58.3725,
        geocode_precision="hotel venue centroid",
        map_source="Wikipedia coordinate statement",
        map_source_date=TODAY,
        hero_url="https://upload.wikimedia.org/wikipedia/commons/8/8b/SheratonBuenosAiresRetiro.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:SheratonBuenosAiresRetiro.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Egypt",
        city="Giza",
        property_name="Marriott Mena House, Cairo",
        scene_type="luxury_hotel_mice",
        field_group="keys",
        indicator_name="keys",
        field_value="Tripadvisor hotel profile states the property comprises 331 rooms and suites.",
        source_name="Tripadvisor hotel profile",
        source_tier="Tier 2",
        source_url="https://www.tripadvisor.com/Hotel_Review-g294202-d302723-Reviews-Marriott_Mena_House_Cairo-Giza_Giza_Governorate.html",
        source_date=TODAY,
        content_text="Tripadvisor's Marriott Mena House, Cairo profile states the property comprises 331 rooms and suites.",
        annual_visits=297_900.0,
        latitude=29.98555556,
        longitude=31.13277778,
        geocode_precision="hotel venue centroid",
        map_source="Wikipedia coordinate statement",
        map_source_date=TODAY,
        hero_url="https://upload.wikimedia.org/wikipedia/commons/4/4e/The_Oberoi_-_Mena_House%2C_Egypt.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:The_Oberoi_-_Mena_House,_Egypt.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Mauritius",
        city="Trou d'Eau Douce",
        property_name="Shangri-La Le Touessrok, Mauritius",
        scene_type="luxury_hotel_mice",
        field_group="keys",
        indicator_name="keys",
        field_value="Travel Weekly hotel profile lists 203 rooms.",
        source_name="Travel Weekly hotel profile",
        source_tier="Tier 2",
        source_url="https://www.travelweekly.com/Hotels/Trou-dEau-Douce-Mauritius/Shangri-Las-Le-Touessrok-Resort-Spa-p50420132",
        source_date=TODAY,
        content_text="Travel Weekly's Shangri-La Le Touessrok Resort & Spa profile lists 203 rooms.",
        annual_visits=250_000.0,
        latitude=-20.252036,
        longitude=57.797473,
        geocode_precision="hotel venue centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://ik.imgkit.net/3vlqs5axxjf/external/ik-seo/https://media.iceportal.com/73948/photos/82356501_XL/Shangri-La%27s-Le-Touessrok-Resort-%26-Spa-Exterior.jpg?tr=w-656%2Ch-390%2Cfo-auto",
        hero_source_url="https://www.travelweekly.com/Hotels/Trou-dEau-Douce-Mauritius/Shangri-Las-Le-Touessrok-Resort-Spa-p50420132",
        hero_source_name="Travel Weekly media",
    ),
    NewCandidate(
        country="Brazil",
        city="João Pessoa",
        property_name="Mangabeira Shopping",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 112,000 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q83811519",
        source_date=TODAY,
        content_text="Mangabeira Shopping has a Wikidata P2046 area value of 112,000 square meters.",
        annual_visits=2_240_000.0,
        latitude=-7.1620727,
        longitude=-34.8327498,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Shopping%20Mangabeira%20-%2002.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Shopping_Mangabeira_-_02.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Brazil",
        city="Salvador",
        property_name="Salvador Shopping",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 89,597 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q10367129",
        source_date=TODAY,
        content_text="Salvador Shopping has a Wikidata P2046 area value of 89,597 square meters.",
        annual_visits=1_791_940.0,
        latitude=-12.9785741,
        longitude=-38.457304,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Salvador%20Shopping%20-%20panoramio.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Salvador_Shopping_-_panoramio.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Brazil",
        city="Salvador",
        property_name="Shopping Paralela",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 75,351 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q62470808",
        source_date=TODAY,
        content_text="Shopping Paralela has a Wikidata P2046 area value of 75,351 square meters.",
        annual_visits=1_507_020.0,
        latitude=-12.936709,
        longitude=-38.3970853,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Shoppin%20Paralela%20-%2011.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Shoppin_Paralela_-_11.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Brazil",
        city="Salvador",
        property_name="Shopping Barra",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 50,000 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q10371127",
        source_date=TODAY,
        content_text="Shopping Barra has a Wikidata P2046 area value of 50,000 square meters.",
        annual_visits=1_000_000.0,
        latitude=-13.00680833,
        longitude=-38.524675,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Shopping%20Barra%20-%20Salvador%2C%20Brazil.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Shopping_Barra_-_Salvador,_Brazil.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Colombia",
        city="Medellín",
        property_name="Centro Comercial Santafé Medellín",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 203,175 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q5761106",
        source_date=TODAY,
        content_text="Centro Comercial Santafé Medellín has a Wikidata P2046 area value of 203,175 square meters.",
        annual_visits=4_063_500.0,
        latitude=6.196502,
        longitude=-75.574065,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Medell%C3%ADn-Santaf%C3%A9%20en%20construcci%C3%B3n.JPG",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Medell%C3%ADn-Santaf%C3%A9_en_construcci%C3%B3n.JPG",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Colombia",
        city="Cúcuta",
        property_name="Ventura Plaza Cúcuta",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 34,847 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q83194569",
        source_date=TODAY,
        content_text="Ventura Plaza Cúcuta has a Wikidata P2046 area value of 34,847 square meters.",
        annual_visits=696_940.0,
        latitude=7.888057,
        longitude=-72.496554,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Ventura%20Plaza%20C%C3%BAcuta%20my%202021.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Ventura_Plaza_C%C3%BAcuta_my_2021.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Egypt",
        city="Alexandria",
        property_name="City Centre Alexandria",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 60,000 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q16245602",
        source_date=TODAY,
        content_text="City Centre Alexandria has a Wikidata P2046 area value of 60,000 square meters.",
        annual_visits=1_200_000.0,
        latitude=31.168,
        longitude=29.9319,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Carrefour%20Alexandria.JPG",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Carrefour_Alexandria.JPG",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Kenya",
        city="Nairobi",
        property_name="Westgate Shopping Mall",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 33,000 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q13125208",
        source_date=TODAY,
        content_text="Westgate Shopping Mall has a Wikidata P2046 area value of 33,000 square meters.",
        annual_visits=660_000.0,
        latitude=-1.256802777,
        longitude=36.803283333,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Nakumatt%20Westgate.JPG",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Nakumatt_Westgate.JPG",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Morocco",
        city="Rabat",
        property_name="Mega Mall Rabat",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 26,367 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q3304231",
        source_date=TODAY,
        content_text="Mega Mall Rabat has a Wikidata P2046 area value of 26,367 square meters.",
        annual_visits=527_340.0,
        latitude=33.967907,
        longitude=-6.829414,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/20161219%20MM-Entree%20zaers-INGLOT%20%281%29.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:20161219_MM-Entree_zaers-INGLOT_(1).jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="South Africa",
        city="Polokwane",
        property_name="Mall of the North",
        scene_type="mall_mixed_use",
        field_group="gla",
        indicator_name="gla",
        field_value="Wikidata structured statement lists area of 77,788 square meters.",
        source_name="Wikidata structured statement",
        source_tier="Tier 3",
        source_url="http://www.wikidata.org/entity/Q110920869",
        source_date=TODAY,
        content_text="Mall of the North has a Wikidata P2046 area value of 77,788 square meters.",
        annual_visits=1_555_760.0,
        latitude=-23.8737,
        longitude=29.5093,
        geocode_precision="shopping mall centroid",
        map_source="Wikidata coordinate statement",
        map_source_date=TODAY,
        hero_url="https://commons.wikimedia.org/wiki/Special:FilePath/Mall%20of%20the%20North%2C%20Polokwane%2C%20Limpopo%2C%20South%20Africa%20%2810186032573%29.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:Mall_of_the_North,_Polokwane,_Limpopo,_South_Africa_(10186032573).jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="South Africa",
        city="Durban",
        property_name="Inkosi Albert Luthuli International Convention Centre",
        scene_type="convention_center",
        field_group="exhibition_area",
        indicator_name="exhibition_area",
        field_value="Cvent venue profile lists exhibit space of 244,556 square feet, about 22,720 square meters.",
        source_name="Cvent venue profile",
        source_tier="Tier 2",
        source_url="https://www.cvent.com/venues/durban/convention-center/international%20convention%20centre%20durban/venue-29b5668e-d05e-4b78-a9d8-81689bfe076c",
        source_date=TODAY,
        content_text="Cvent's International Convention Centre Durban profile lists exhibit space of 244,556 square feet, approximately 22,720 square meters.",
        annual_visits=250_000.0,
        latitude=-29.85361111,
        longitude=31.03,
        geocode_precision="convention centre centroid",
        map_source="Wikipedia coordinate statement",
        map_source_date=TODAY,
        hero_url="https://upload.wikimedia.org/wikipedia/commons/c/c3/ICC_Durban-20140315.jpg",
        hero_source_url="https://commons.wikimedia.org/wiki/File:ICC_Durban-20140315.jpg",
        hero_source_name="Wikimedia Commons",
    ),
    NewCandidate(
        country="Rwanda",
        city="Kigali",
        property_name="Kigali Convention Centre",
        scene_type="convention_center",
        field_group="peak_event_capacity",
        indicator_name="peak_event_capacity",
        field_value="AIPC member profile states total capacity exceeds 5,000 across 20 venues.",
        source_name="AIPC member profile",
        source_tier="Tier 2",
        source_url="https://aipc.org/member/kigali-convention-centre/",
        source_date=TODAY,
        content_text="AIPC's Kigali Convention Centre profile states total capacity exceeds 5,000 across 20 venues.",
        annual_visits=250_000.0,
        latitude=-1.95472222,
        longitude=30.09388889,
        geocode_precision="convention centre centroid",
        map_source="Wikipedia coordinate statement",
        map_source_date=TODAY,
        hero_url="https://upload.wikimedia.org/wikipedia/en/thumb/7/78/KCC_Wallpaper_by_Mudahunga.jpg/3840px-KCC_Wallpaper_by_Mudahunga.jpg",
        hero_source_url="https://en.wikipedia.org/wiki/File:KCC_Wallpaper_by_Mudahunga.jpg",
        hero_source_name="Wikipedia image",
    ),
]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    registry = load_effective_source_registry()
    store = EvidenceCurationStore(database_url=DB_URL)
    known_index = known_opportunity_index_from_registry(
        registry,
        session_factory=store.session_factory,
    )
    active = _active_properties()
    drafts: list[CandidateDraft] = []
    skipped: list[dict] = []
    invalid: list[dict] = []

    for evidence in EXISTING_EVIDENCE:
        prop = active.get(_active_key(evidence.country, evidence.property_name, evidence.scene_type))
        if prop is None:
            skipped.append(
                {
                    "property_name": evidence.property_name,
                    "country": evidence.country,
                    "scene_type": evidence.scene_type,
                    "reason": "active property not found",
                }
            )
            continue
        drafts.append(_draft_for_existing(evidence, prop, registry))

    for candidate in NEW_CANDIDATES:
        match = known_index.match(
            country=candidate.country,
            city=candidate.city,
            property_name=candidate.property_name,
            scene_type=candidate.scene_type,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            source_url=candidate.source_url,
        )
        if match.status != NEW_OPPORTUNITY:
            skipped.append(
                {
                    "property_name": candidate.property_name,
                    "country": candidate.country,
                    "scene_type": candidate.scene_type,
                    "reason": match.reason or match.status,
                }
            )
            continue
        draft = _draft_for_new(candidate, registry)
        validation = validate_candidate_draft(draft)
        if not validation.accepted:
            invalid.append(
                {
                    "property_name": candidate.property_name,
                    "country": candidate.country,
                    "scene_type": candidate.scene_type,
                    "issues": validation.issues,
                }
            )
            continue
        drafts.append(draft)

    raw_ids = []
    written_or_changed = 0
    for draft in drafts:
        result = store.upsert_candidate_evidence(draft, source_type=SOURCE_TYPE)
        raw_ids.append(result.raw_evidence_id)
        if result.is_new_evidence or result.is_changed_evidence:
            written_or_changed += 1

    curation = run_pending_evidence_curation(store=store, output_dir=OUTPUT_DIR)
    repository = SQLAlchemyScanRunRepository.from_url(DB_URL, storage_mode="sqlite")
    sync = sync_overlay_to_active_repository(repository)
    summary = {
        "mode": SOURCE_TYPE,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "draft_count": len(drafts),
        "drafts_by_scene": dict(Counter(d.scene_type for d in drafts)),
        "drafts_by_match_status": dict(Counter(d.identity_match_status for d in drafts)),
        "raw_evidence_written_or_changed": written_or_changed,
        "skipped": skipped,
        "invalid": invalid,
        "curation": None
        if curation is None
        else {
            "curation_run_id": curation.curation_run_id,
            "new_evidence_count": curation.new_evidence_count,
            "accepted_count": curation.accepted_count,
            "updated_count": curation.updated_count,
            "rejected_count": curation.rejected_count,
            "report_path": str(curation.report_path),
            "summary_path": str(curation.summary_path),
        },
        "overlay_sync": {
            "created": sync.created,
            "run_id": str(sync.run_id) if sync.run_id else None,
            "candidate_count": sync.candidate_count,
            "registry_candidate_count": sync.registry_candidate_count,
            "blocked_candidate_count": sync.blocked_candidate_count,
            "skipped_reason": sync.skipped_reason,
            "derived_refresh": sync.derived_refresh,
        },
        "active_db": _active_db_summary(),
        "raw_evidence_ids": raw_ids,
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary_path = OUTPUT_DIR / f"{SOURCE_TYPE}_{timestamp}.json"
    report_path = summary_path.with_suffix(".md")
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _draft_for_existing(evidence: ExistingEvidence, prop: dict, registry: dict) -> CandidateDraft:
    return CandidateDraft(
        region=_region_for_country(evidence.country),
        country=evidence.country,
        city=prop["city"],
        property_name=prop["canonical_name"],
        scene_type=evidence.scene_type,
        annual_visits=(
            evidence.annual_visits
            if evidence.field_group in ANNUAL_VISIT_FIELDS
            else None
        ),
        latitude=float(prop["latitude"]),
        longitude=float(prop["longitude"]),
        geocode_precision=prop["geocode_precision"],
        map_source=prop["map_source"] or "",
        map_source_date=prop["map_source_date"] or "",
        field_group=evidence.field_group,
        indicator_name=evidence.indicator_name,
        field_value=evidence.field_value,
        source_name=evidence.source_name,
        source_tier=evidence.source_tier,
        source_url=evidence.source_url,
        source_date=evidence.source_date,
        evidence_type="Direct",
        bbox=registry["countries"][evidence.country]["bbox"],
        source_type=SOURCE_TYPE,
        content_text=evidence.content_text,
        identity_match_status=KNOWN_PROPERTY,
        matched_property_id=prop["id"],
        matched_property_name=prop["canonical_name"],
        identity_match_reason="active property matched by country/name/scene",
        hero_image=json.loads(prop["hero_image"]) if prop["hero_image"] else None,
    )


def _draft_for_new(candidate: NewCandidate, registry: dict) -> CandidateDraft:
    return CandidateDraft(
        region=_region_for_country(candidate.country),
        country=candidate.country,
        city=candidate.city,
        property_name=candidate.property_name,
        scene_type=candidate.scene_type,
        annual_visits=(
            candidate.annual_visits
            if candidate.field_group in ANNUAL_VISIT_FIELDS
            else None
        ),
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        geocode_precision=candidate.geocode_precision,
        map_source=candidate.map_source,
        map_source_date=candidate.map_source_date,
        field_group=candidate.field_group,
        indicator_name=candidate.indicator_name,
        field_value=candidate.field_value,
        source_name=candidate.source_name,
        source_tier=candidate.source_tier,
        source_url=candidate.source_url,
        source_date=candidate.source_date,
        evidence_type="Direct",
        bbox=registry["countries"][candidate.country]["bbox"],
        source_type=SOURCE_TYPE,
        content_text=candidate.content_text,
        hero_image={
            "url": candidate.hero_url,
            "alt_text": f"{candidate.property_name} public image",
            "source_url": candidate.hero_source_url,
            "source_name": candidate.hero_source_name,
            "source_date": TODAY,
            "license": "Public source image metadata",
        },
    )


def _active_properties() -> dict[tuple[str, str, str], dict]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT p.*, sm.annual_visits_est
        FROM properties p
        JOIN scan_candidates sc ON sc.property_id = p.id
        LEFT JOIN scene_model_results sm
          ON sm.property_id = p.id AND sm.scan_run_id = sc.scan_run_id
        """
    ).fetchall()
    connection.close()
    active = {}
    for row in rows:
        data = dict(row)
        active[_active_key(data["country"], data["canonical_name"], data["scene_type"])] = data
    return active


def _active_key(country: str, property_name: str, scene_type: str) -> tuple[str, str, str]:
    return (country.casefold(), normalize_property_name(property_name), scene_type.casefold())


def _region_for_country(country: str) -> str:
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return "Unknown"


def _active_db_summary() -> dict:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    def rows(sql: str) -> list[dict]:
        return [dict(row) for row in connection.execute(sql)]

    def one(sql: str) -> int:
        return int(connection.execute(sql).fetchone()[0])

    summary = {
        "total_properties": one("select count(*) from properties"),
        "country_counts": rows(
            "select country, count(*) as n from properties group by country order by n desc"
        ),
        "scene_counts": rows(
            "select scene_type, count(*) as n from properties group by scene_type order by scene_type"
        ),
        "missing_hero": one(
            "select count(*) from properties "
            "where hero_image is null or json_extract(hero_image,'$.url') is null "
            "or json_extract(hero_image,'$.url') not like 'http%'"
        ),
        "non_ready_scan_candidates": one(
            "select count(*) from scan_candidates where candidate_quality_status!='ready'"
        ),
        "duplicate_identity_groups": one(
            "select count(*) from (select property_identity_key from properties "
            "group by property_identity_key having count(*)>1)"
        ),
    }
    connection.close()
    return summary


def _render_report(summary: dict) -> str:
    scene_lines = "\n".join(
        f"- {item['scene_type']}: {item['n']}"
        for item in summary["active_db"]["scene_counts"]
    )
    return (
        "# Firecrawl Scene Hard Evidence Pass\n\n"
        f"- Drafts written: {summary['draft_count']} {summary['drafts_by_scene']}\n"
        f"- Raw evidence written/changed: {summary['raw_evidence_written_or_changed']}\n"
        f"- Curation accepted new: {(summary['curation'] or {}).get('accepted_count', 0)}\n"
        f"- Curation updated existing: {(summary['curation'] or {}).get('updated_count', 0)}\n"
        f"- Curation rejected: {(summary['curation'] or {}).get('rejected_count', 0)}\n"
        f"- Active total properties: {summary['active_db']['total_properties']}\n"
        f"- Missing hero images: {summary['active_db']['missing_hero']}\n"
        f"- Non-ready scan candidates: {summary['active_db']['non_ready_scan_candidates']}\n"
        f"- Duplicate identity groups: {summary['active_db']['duplicate_identity_groups']}\n\n"
        "## Active Scenes\n"
        f"{scene_lines}\n"
    )


if __name__ == "__main__":
    main()
