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
    "Philippines",
    "Vietnam",
    "Indonesia",
    "Thailand",
]

# Business-defined sweep cluster for the user's North Africa portfolio scope.
# It intentionally includes West/Central Africa markets beyond geographic North Africa.
NORTH_AFRICA_COUNTRIES = [
    "Egypt",
    "Ethiopia",
    "Algeria",
    "Morocco",
    "Cameroon",
    "Senegal",
    "Cote d'Ivoire",
    "Congo",
    "Mali",
    "Burkina Faso",
    "Guinea",
    "Gambia",
    "Mauritania",
    "Libya",
    "Tunisia",
    "Democratic Republic of the Congo",
    "Gabon",
    "Chad",
    "Equatorial Guinea",
    "Central African Republic",
    "Cape Verde",
    "Benin",
]

EMEA_COUNTRIES = [
    "Saudi Arabia",
    "Turkey",
]

REGION_COUNTRIES = {
    "Africa": AFRICAN_COUNTRIES,
    "North Africa": NORTH_AFRICA_COUNTRIES,
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
