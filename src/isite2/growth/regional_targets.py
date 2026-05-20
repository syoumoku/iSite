from __future__ import annotations

from isite2.growth.africa_loop import AFRICAN_COUNTRIES

LATIN_AMERICA_COUNTRIES = [
    "Antigua and Barbuda",
    "Argentina",
    "Bahamas",
    "Barbados",
    "Belize",
    "Bolivia",
    "Brazil",
    "Chile",
    "Colombia",
    "Costa Rica",
    "Cuba",
    "Dominica",
    "Dominican Republic",
    "Ecuador",
    "El Salvador",
    "Grenada",
    "Guatemala",
    "Guyana",
    "Haiti",
    "Honduras",
    "Jamaica",
    "Mexico",
    "Nicaragua",
    "Panama",
    "Paraguay",
    "Peru",
    "Saint Kitts and Nevis",
    "Saint Lucia",
    "Saint Vincent and the Grenadines",
    "Suriname",
    "Trinidad and Tobago",
    "Uruguay",
    "Venezuela",
]

ASIA_PACIFIC_COUNTRIES = [
    "Sri Lanka",
    "Cambodia",
    "Maldives",
]

EMEA_COUNTRIES = [
    "Turkey",
]

REGION_COUNTRIES = {
    "Africa": AFRICAN_COUNTRIES,
    "Latin America": LATIN_AMERICA_COUNTRIES,
    "Asia Pacific": ASIA_PACIFIC_COUNTRIES,
    "EMEA": EMEA_COUNTRIES,
}
DEFAULT_REGIONS = ["Africa", "Latin America"]


def countries_for_regions(regions: list[str]) -> list[str]:
    countries: list[str] = []
    for region in regions:
        if region not in REGION_COUNTRIES:
            raise KeyError(f"unknown target region: {region}")
        countries.extend(REGION_COUNTRIES[region])
    return countries
