from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from isite2.connectors.models import FetchedPage
from isite2.domain.enums import SourceTier
from isite2.growth.evidence_intake import CandidateDraft
from isite2.growth.regional_targets import REGION_COUNTRIES
from isite2.rules.config_loader import load_localized_search_strategy
from isite2.rules.metric_period import annual_metric_period_issue

USER_AGENT = "isite2-codex/0.1 public structured evidence research"
TODAY = "2026-05-13"

HARD_PRIMARY: dict[str, list[str]] = {
    "airport_terminal": [
        "annual_passenger_throughput",
        "passenger_throughput",
        "terminal_capacity",
        "international_passenger_share",
    ],
    "convention_center": [
        "exhibition_area",
        "meeting_area",
        "annual_events",
        "international_event_frequency",
        "peak_event_capacity",
        "plenary_capacity",
    ],
    "stadium": ["seat_count", "event_days", "international_events", "peak_event_capacity"],
    "luxury_hotel_mice": ["keys", "rooms", "meeting_ballroom_area", "ballroom_capacity"],
    "mall_mixed_use": ["gla", "annual_footfall", "footfall"],
    "office_government": ["office_nla", "office_gfa"],
    "hospital": ["beds", "outpatient_volume"],
    "university": ["enrollment", "students", "student_count", "campus_population"],
    "transport_hub": ["daily_ridership", "ridership", "interchange_volume", "line_count"],
    "cruise_port": ["passenger_throughput", "annual_passenger_throughput"],
}

INFOBOX_KEY_HINTS: dict[str, dict[str, list[str]]] = {
    "airport_terminal": {
        "annual_passenger_throughput": [
            "passengers",
            "passenger traffic",
            "passenger_traffic",
            "stat1-data",
        ],
        "terminal_capacity": ["capacity"],
    },
    "stadium": {
        "seat_count": ["capacity", "seating_capacity", "seats"],
    },
    "convention_center": {
        "exhibition_area": ["exhibit", "exhibition", "floor_area", "area"],
        "peak_event_capacity": ["capacity"],
    },
    "luxury_hotel_mice": {
        "keys": ["rooms", "number_of_rooms", "keys"],
        "rooms": ["rooms", "number_of_rooms"],
        "meeting_ballroom_area": ["meeting", "conference", "ballroom"],
    },
    "mall_mixed_use": {
        "gla": ["gla", "floor_area", "retail_floor_area", "area"],
        "annual_footfall": ["visitors", "footfall"],
    },
    "office_government": {
        "office_gfa": ["floor_area", "gross_floor_area", "area"],
        "office_nla": ["lettable", "net_lettable_area"],
    },
    "transport_hub": {
        "daily_ridership": ["daily_ridership", "passengers"],
        "ridership": ["ridership", "passengers"],
    },
    "hospital": {
        "beds": ["beds"],
        "outpatient_volume": ["outpatients", "patients"],
    },
    "university": {
        "enrollment": ["students", "enrollment"],
        "students": ["students", "enrollment"],
    },
}

IDENTITY_FIELD_GROUPS = {"airport_identity", "osm_identity"}


class HttpClient(Protocol):
    def get_json(self, url: str, *, timeout: int = 30) -> dict[str, Any]:
        ...

    def get_text(self, url: str, *, timeout: int = 30) -> str:
        ...


@dataclass(frozen=True)
class PublicStructuredTarget:
    property_id: str
    country: str
    city: str
    property_name: str
    scene_type: str
    latitude: float
    longitude: float
    geocode_precision: str
    map_source: str
    map_source_date: str
    annual_visits_est: float
    hero_image: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PublicStructuredEvidence:
    target: PublicStructuredTarget
    source_id: str
    source_name: str
    source_tier: str
    source_url: str
    source_date: str
    field_group: str
    indicator_name: str
    field_value: str
    content_text: str
    evidence_type: str = "Direct"
    hero_image: dict[str, Any] | None = None

    @property
    def is_primary_metric(self) -> bool:
        return self.field_group in HARD_PRIMARY.get(self.target.scene_type, [])

    def to_candidate_draft(self, *, registry: dict[str, Any]) -> CandidateDraft:
        country_entry = registry.get("countries", {}).get(self.target.country, {})
        return CandidateDraft(
            region=_region_for_country(self.target.country),
            country=self.target.country,
            city=self.target.city,
            property_name=self.target.property_name,
            scene_type=self.target.scene_type,
            annual_visits=(
                float(self.target.annual_visits_est)
                if self.target.annual_visits_est is not None
                else None
            ),
            latitude=float(self.target.latitude),
            longitude=float(self.target.longitude),
            geocode_precision=self.target.geocode_precision or "",
            map_source=self.target.map_source or "",
            map_source_date=self.target.map_source_date or "",
            field_group=self.field_group,
            indicator_name=self.indicator_name,
            field_value=self.field_value,
            source_name=self.source_name,
            source_tier=self.source_tier,
            source_url=self.source_url,
            source_date=self.source_date,
            evidence_type=self.evidence_type,
            bbox=country_entry.get("bbox", {}),
            source_type=self.source_id,
            content_text=self.content_text,
            identity_match_status="known_property",
            matched_property_id=self.target.property_id,
            matched_property_name=self.target.property_name,
            identity_match_reason="active property selected for structured-first evidence backfill",
            hero_image=self.hero_image or self.target.hero_image,
        )

    def as_page(self) -> FetchedPage:
        return FetchedPage(
            source_url=self.source_url,
            source_name=self.source_name,
            source_tier=SourceTier(self.source_tier),
            source_date=self.source_date,
            content_text=self.content_text,
            robots_allowed=True,
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["is_primary_metric"] = self.is_primary_metric
        return payload


class CachedHttpClient:
    def __init__(self, cache_dir: Path, *, user_agent: str = USER_AGENT) -> None:
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_json(self, url: str, *, timeout: int = 30) -> dict[str, Any]:
        text = self.get_text(url, timeout=timeout)
        return json.loads(text)

    def get_text(self, url: str, *, timeout: int = 30) -> str:
        cache_path = self.cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.txt"
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 1:
                    raise
                retry_after = exc.headers.get("Retry-After")
                sleep_seconds = int(retry_after) if str(retry_after or "").isdigit() else 8
                time.sleep(sleep_seconds)
        text = raw.decode("utf-8", errors="replace")
        cache_path.write_text(text, encoding="utf-8")
        return text


class MediaWikiAdapter:
    source_id = "wikipedia_mediawiki_api"
    source_name = "Wikipedia MediaWiki API"
    source_tier = "Tier 3"

    def __init__(
        self,
        client: HttpClient,
        *,
        strategy: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> None:
        self.client = client
        self.strategy = strategy or load_localized_search_strategy()
        self.timeout = timeout

    def collect(self, target: PublicStructuredTarget) -> list[PublicStructuredEvidence]:
        evidences: list[PublicStructuredEvidence] = []
        for lang in _languages_for_country(target.country, self.strategy):
            title = self._search_title(lang, target)
            if not title:
                continue
            page = self._fetch_page(lang, title)
            if page is None:
                continue
            source_url = page["source_url"]
            content = page["content_text"]
            if not _content_matches_target(target.property_name, page["title"], content):
                continue
            hero_image = _hero_from_page(page)
            seen: set[tuple[str, str]] = set()
            for field_group, value in _infobox_metrics(target.scene_type, content):
                key = (field_group, value.casefold())
                if key in seen:
                    continue
                seen.add(key)
                evidences.append(
                    PublicStructuredEvidence(
                        target=target,
                        source_id=self.source_id,
                        source_name=f"Wikipedia ({lang})",
                        source_tier="Tier 3",
                        source_url=source_url,
                        source_date=page.get("source_date") or "",
                        field_group=field_group,
                        indicator_name=field_group,
                        field_value=value,
                        content_text=content,
                        hero_image=hero_image,
                    )
                )
            if evidences:
                return evidences
        return evidences

    def _search_title(self, lang: str, target: PublicStructuredTarget) -> str | None:
        query = " ".join(part for part in [target.property_name, target.city] if part)
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": "3",
            "format": "json",
        }
        url = f"https://{lang}.wikipedia.org/w/api.php?{urllib.parse.urlencode(params)}"
        payload = self.client.get_json(url, timeout=self.timeout)
        for row in payload.get("query", {}).get("search", []) or []:
            title = str(row.get("title") or "")
            if _name_token_score(target.property_name, title) >= 0.35:
                return title
        return None

    def _fetch_page(self, lang: str, title: str) -> dict[str, Any] | None:
        params = {
            "action": "query",
            "prop": "revisions|pageimages|info",
            "rvprop": "content",
            "rvslots": "main",
            "formatversion": "2",
            "inprop": "url",
            "pithumbsize": "640",
            "titles": title,
            "format": "json",
        }
        url = f"https://{lang}.wikipedia.org/w/api.php?{urllib.parse.urlencode(params)}"
        payload = self.client.get_json(url, timeout=self.timeout)
        pages = payload.get("query", {}).get("pages", []) or []
        if not pages:
            return None
        page = pages[0]
        revision = (page.get("revisions") or [{}])[0]
        slots = revision.get("slots") or {}
        content = ((slots.get("main") or {}).get("content") or "").strip()
        if not content:
            return None
        title = str(page.get("title") or title)
        return {
            "title": title,
            "source_url": page.get("fullurl") or _wiki_url(lang, title),
            "source_date": TODAY,
            "content_text": content,
            "thumbnail": (page.get("thumbnail") or {}).get("source"),
        }


class OurAirportsAdapter:
    source_id = "ourairports_csv"
    source_name = "OurAirports CSV"
    source_tier = "Tier 3"
    csv_url = "https://davidmegginson.github.io/ourairports-data/airports.csv"

    def __init__(self, client: HttpClient, *, timeout: int = 30) -> None:
        self.client = client
        self.timeout = timeout
        self._rows: list[dict[str, str]] | None = None

    def collect(self, target: PublicStructuredTarget) -> list[PublicStructuredEvidence]:
        if target.scene_type != "airport_terminal":
            return []
        match = self._match(target)
        if match is None:
            return []
        parts = [
            f"OurAirports ident {match.get('ident')}",
            f"type {match.get('type')}",
        ]
        if match.get("iata_code"):
            parts.append(f"IATA {match['iata_code']}")
        if match.get("gps_code"):
            parts.append(f"ICAO/GPS {match['gps_code']}")
        content = json.dumps(match, ensure_ascii=False, sort_keys=True)
        return [
            PublicStructuredEvidence(
                target=target,
                source_id=self.source_id,
                source_name=self.source_name,
                source_tier=self.source_tier,
                source_url=match.get("wikipedia_link") or match.get("home_link") or self.csv_url,
                source_date=TODAY,
                field_group="airport_identity",
                indicator_name="airport_identity",
                field_value="; ".join(part for part in parts if part),
                content_text=content,
                evidence_type="Direct",
            )
        ]

    def _match(self, target: PublicStructuredTarget) -> dict[str, str] | None:
        best: tuple[float, dict[str, str]] | None = None
        for row in self._load_rows():
            try:
                distance_km = _distance_km(
                    target.latitude,
                    target.longitude,
                    float(row.get("latitude_deg") or 999),
                    float(row.get("longitude_deg") or 999),
                )
            except ValueError:
                continue
            if distance_km > 12:
                continue
            score = _name_token_score(target.property_name, row.get("name") or "")
            if score < 0.25:
                continue
            rank = score - (distance_km / 40.0)
            if best is None or rank > best[0]:
                best = (rank, row)
        return best[1] if best else None

    def _load_rows(self) -> list[dict[str, str]]:
        if self._rows is None:
            text = self.client.get_text(self.csv_url, timeout=self.timeout)
            self._rows = list(csv.DictReader(text.splitlines()))
        return self._rows


class DbpediaAdapter:
    source_id = "dbpedia_sparql"
    source_name = "DBpedia SPARQL"
    source_tier = "Tier 3"
    endpoint = "https://dbpedia.org/sparql"

    FIELD_MAP: dict[str, dict[str, str]] = {
        "stadium": {
            "seatingCapacity": "seat_count",
            "capacity": "seat_count",
        },
        "luxury_hotel_mice": {
            "numberOfRooms": "rooms",
        },
        "office_government": {
            "floorArea": "office_gfa",
        },
        "convention_center": {
            "capacity": "peak_event_capacity",
            "floorArea": "exhibition_area",
        },
        "university": {
            "numberOfStudents": "students",
        },
        "hospital": {
            "numberOfBeds": "beds",
        },
    }

    def __init__(self, client: HttpClient, *, timeout: int = 30) -> None:
        self.client = client
        self.timeout = timeout

    def collect(self, target: PublicStructuredTarget) -> list[PublicStructuredEvidence]:
        mapping = self.FIELD_MAP.get(target.scene_type, {})
        if not mapping:
            return []
        payload = self.client.get_json(self._query_url(target), timeout=self.timeout)
        bindings = payload.get("results", {}).get("bindings", []) or []
        evidences: list[PublicStructuredEvidence] = []
        for row in bindings:
            label = _binding_value(row, "label")
            if _name_token_score(target.property_name, label) < 0.35:
                continue
            source_url = _binding_value(row, "resource") or self.endpoint
            content = json.dumps(row, ensure_ascii=False, sort_keys=True)
            for variable, field_group in mapping.items():
                value = _binding_value(row, variable)
                if not value:
                    continue
                evidences.append(
                    PublicStructuredEvidence(
                        target=target,
                        source_id=self.source_id,
                        source_name=self.source_name,
                        source_tier=self.source_tier,
                        source_url=source_url,
                        source_date=TODAY,
                        field_group=field_group,
                        indicator_name=field_group,
                        field_value=f"DBpedia {variable}: {value}",
                        content_text=content,
                    )
                )
            if evidences:
                break
        return evidences

    def _query_url(self, target: PublicStructuredTarget) -> str:
        safe_label = target.property_name.replace("\\", "\\\\").replace('"', '\\"')
        query = f"""
        PREFIX dbo: <http://dbpedia.org/ontology/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?resource ?label ?capacity ?seatingCapacity ?numberOfRooms ?floorArea
               ?numberOfStudents ?numberOfBeds
        WHERE {{
          ?resource rdfs:label ?label .
          FILTER(lang(?label) = "en")
          FILTER(lcase(str(?label)) = lcase("{safe_label}"))
          OPTIONAL {{ ?resource dbo:capacity ?capacity. }}
          OPTIONAL {{ ?resource dbo:seatingCapacity ?seatingCapacity. }}
          OPTIONAL {{ ?resource dbo:numberOfRooms ?numberOfRooms. }}
          OPTIONAL {{ ?resource dbo:floorArea ?floorArea. }}
          OPTIONAL {{ ?resource dbo:numberOfStudents ?numberOfStudents. }}
          OPTIONAL {{ ?resource dbo:numberOfBeds ?numberOfBeds. }}
        }}
        LIMIT 5
        """
        params = {
            "query": query,
            "format": "application/sparql-results+json",
        }
        return f"{self.endpoint}?{urllib.parse.urlencode(params)}"


class OverpassAdapter:
    source_id = "openstreetmap_overpass"
    source_name = "OpenStreetMap Overpass"
    source_tier = "Tier 3"

    def __init__(self, client: HttpClient, *, timeout: int = 45, radius_m: int = 350) -> None:
        self.client = client
        self.timeout = timeout
        self.radius_m = radius_m

    def collect(self, target: PublicStructuredTarget) -> list[PublicStructuredEvidence]:
        query = self._query(target)
        url = "https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": query})
        payload = self.client.get_json(url, timeout=self.timeout)
        elements = payload.get("elements", []) or []
        best = self._best_element(target, elements)
        if best is None:
            return []
        tags = best.get("tags") or {}
        content = json.dumps(best, ensure_ascii=False, sort_keys=True)
        source_url = f"https://www.openstreetmap.org/{best.get('type')}/{best.get('id')}"
        evidences: list[PublicStructuredEvidence] = []
        if tags.get("capacity") and target.scene_type in {"stadium", "convention_center"}:
            evidences.append(
                PublicStructuredEvidence(
                    target=target,
                    source_id=self.source_id,
                    source_name=self.source_name,
                    source_tier=self.source_tier,
                    source_url=source_url,
                    source_date=TODAY,
                    field_group="seat_count" if target.scene_type == "stadium" else "peak_event_capacity",
                    indicator_name="capacity",
                    field_value=f"OpenStreetMap capacity tag: {tags['capacity']}",
                    content_text=content,
                )
            )
        evidences.append(
            PublicStructuredEvidence(
                target=target,
                source_id=self.source_id,
                source_name=self.source_name,
                source_tier=self.source_tier,
                source_url=source_url,
                source_date=TODAY,
                field_group="osm_identity",
                indicator_name="osm_identity",
                field_value=f"OpenStreetMap matched tags: {', '.join(sorted(tags)[:12])}",
                content_text=content,
            )
        )
        return evidences

    def _query(self, target: PublicStructuredTarget) -> str:
        return f"""
        [out:json][timeout:25];
        (
          node(around:{self.radius_m},{target.latitude},{target.longitude})[name];
          way(around:{self.radius_m},{target.latitude},{target.longitude})[name];
          relation(around:{self.radius_m},{target.latitude},{target.longitude})[name];
        );
        out center tags 20;
        """

    def _best_element(
        self,
        target: PublicStructuredTarget,
        elements: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        for element in elements:
            tags = element.get("tags") or {}
            name = str(tags.get("name") or tags.get("official_name") or "")
            score = _name_token_score(target.property_name, name)
            if score < 0.25:
                continue
            if best is None or score > best[0]:
                best = (score, element)
        return best[1] if best else None


def adapter_from_source_id(source_id: str, client: HttpClient, *, timeout: int = 30) -> Any:
    if source_id == "wikipedia_mediawiki_api":
        return MediaWikiAdapter(client, timeout=timeout)
    if source_id == "dbpedia_sparql":
        return DbpediaAdapter(client, timeout=timeout)
    if source_id == "ourairports_csv":
        return OurAirportsAdapter(client, timeout=timeout)
    if source_id == "openstreetmap_overpass":
        return OverpassAdapter(client, timeout=timeout)
    raise KeyError(f"unsupported public structured source adapter: {source_id}")


def is_hard_primary_evidence(evidence: PublicStructuredEvidence) -> bool:
    return evidence.field_group in HARD_PRIMARY.get(evidence.target.scene_type, [])


def _languages_for_country(country: str, strategy: dict[str, Any]) -> list[str]:
    profile = strategy.get("country_profiles", {}).get(country) or strategy.get(
        "country_profiles", {}
    ).get("default", {})
    languages = [str(lang) for lang in profile.get("languages", ["en"])]
    if "en" not in languages:
        languages.append("en")
    return [lang for lang in languages if lang in {"en", "es", "pt", "fr", "ar", "nl"}]


def _infobox_metrics(scene_type: str, content: str) -> list[tuple[str, str]]:
    values_by_key = _infobox_values(content)
    if scene_type == "airport_terminal":
        return _airport_infobox_metrics(values_by_key)
    key_hints = INFOBOX_KEY_HINTS.get(scene_type, {})
    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, value in values_by_key.items():
        key_norm = key.strip().casefold().replace(" ", "_")
        if not value or not re.search(r"\d", value):
            continue
        for field_group, hints in key_hints.items():
            if not any(hint in key_norm for hint in hints):
                continue
            field_value = f"MediaWiki infobox {key.strip()}: {value}"
            if not _metric_value_accepted(scene_type, field_group, field_value):
                continue
            dedupe = (field_group, field_value.casefold())
            if dedupe in seen:
                continue
            seen.add(dedupe)
            results.append((field_group, field_value))
    return results


def _infobox_values(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "=" not in line:
            continue
        key, raw_value = line[1:].split("=", 1)
        clean_key = key.strip()
        if not clean_key:
            continue
        values[clean_key] = _clean_wiki_value(raw_value)
    return values


def _airport_infobox_metrics(values_by_key: dict[str, str]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    stat_year = values_by_key.get("stat-year") or values_by_key.get("stat_year") or ""
    for key, header in values_by_key.items():
        match = re.fullmatch(r"stat(\d+)[-_]header", key, flags=re.I)
        if not match:
            continue
        header_norm = header.casefold()
        if not any(
            token in header_norm
            for token in ["passenger", "passageiros", "pasajeros", "passagers"]
        ):
            continue
        data = (
            values_by_key.get(f"stat{match.group(1)}-data")
            or values_by_key.get(f"stat{match.group(1)}_data")
            or ""
        )
        if not data or not re.search(r"\d", data):
            continue
        label = f"{header} ({stat_year})" if stat_year else header
        field_value = f"MediaWiki infobox {label}: {data}"
        if _metric_value_accepted("airport_terminal", "annual_passenger_throughput", field_value):
            results.append(("annual_passenger_throughput", field_value))
    capacity = values_by_key.get("capacity") or values_by_key.get("terminal_capacity") or ""
    if capacity:
        field_value = f"MediaWiki infobox capacity: {capacity}"
        if _metric_value_accepted("airport_terminal", "terminal_capacity", field_value):
            results.append(("terminal_capacity", field_value))
    return results


def _metric_value_accepted(scene_type: str, field_group: str, field_value: str) -> bool:
    value = _first_numeric_value(field_value)
    if value is None:
        return False
    if annual_metric_period_issue(field_group, field_value):
        return False
    text = field_value.casefold()
    if scene_type == "airport_terminal" and field_group in {
        "annual_passenger_throughput",
        "passenger_throughput",
        "terminal_capacity",
    }:
        return (
            "million" in text
            or "milhões" in text
            or "millones" in text
            or value >= 100_000
        )
    if field_group in {"seat_count", "peak_event_capacity", "capacity"}:
        return value >= 1_000
    if field_group in {"rooms", "keys"}:
        return value >= 50
    if field_group in {
        "gla",
        "office_gfa",
        "office_nla",
        "exhibition_area",
        "meeting_ballroom_area",
    }:
        return value >= 1_000
    if field_group in {"students", "enrollment", "beds", "daily_ridership", "ridership"}:
        return value >= 100
    return True


def _first_numeric_value(text: str) -> float | None:
    search_text = text.split(":", 1)[1] if ":" in text else text
    match = re.search(r"\d[\d,.]*", search_text)
    if not match:
        return None
    raw = match.group(0)
    if "," in raw and "." in raw:
        normalized = raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        normalized = raw.replace(",", "") if len(parts[-1]) == 3 else raw.replace(",", ".")
    elif "." in raw and len(raw.split(".")[-1]) == 3:
        normalized = raw.replace(".", "")
    else:
        normalized = raw
    try:
        return float(normalized)
    except ValueError:
        return None


def _clean_wiki_value(value: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", value)
    text = re.sub(r"<ref[^>]*>.*?</ref>", " ", text, flags=re.I)
    text = re.sub(r"<ref[^/]*/>", " ", text, flags=re.I)
    text = re.sub(r"\{\{convert\|([^}|]+)\|([^}|]+).*?\}\}", r"\1 \2", text, flags=re.I)
    text = re.sub(r"\{\{nowrap\|([^}]+)\}\}", r"\1", text, flags=re.I)
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"\[\[([^]|]+)\|([^]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^]]+)\]\]", r"\1", text)
    text = re.sub(r"'''?", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"\s+", " ", text).strip(" |")


def _content_matches_target(property_name: str, title: str, content: str) -> bool:
    if _name_token_score(property_name, title) >= 0.35:
        return True
    return _name_token_score(property_name, content[:2000]) >= 0.45


def _name_token_score(left: str | None, right: str | None) -> float:
    left_tokens = _name_tokens(left)
    right_tokens = _name_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens)


def _name_tokens(value: str | None) -> set[str]:
    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9\u00c0-\u024f\u0600-\u06ff]+", " ", text)
    stop = {
        "the",
        "of",
        "de",
        "do",
        "da",
        "del",
        "di",
        "el",
        "la",
        "le",
        "airport",
        "aeroporto",
        "aeropuerto",
        "aéroport",
        "international",
        "internacional",
        "stadium",
        "estadio",
        "estádio",
        "stade",
    }
    return {token for token in text.split() if len(token) > 2 and token not in stop}


def _hero_from_page(page: dict[str, Any]) -> dict[str, str] | None:
    thumbnail = page.get("thumbnail")
    if not thumbnail:
        return None
    return {
        "url": thumbnail,
        "alt_text": f"{page.get('title', 'property')} public image",
        "source_url": str(page.get("source_url") or ""),
        "source_name": "Wikipedia MediaWiki API",
        "source_date": str(page.get("source_date") or ""),
        "license": "Wikipedia page image metadata",
    }


def _wiki_url(lang: str, title: str) -> str:
    slug = urllib.parse.quote(title.replace(" ", "_"))
    return f"https://{lang}.wikipedia.org/wiki/{slug}"


def _binding_value(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, dict):
        return ""
    return str(value.get("value") or "")


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _region_for_country(country: str) -> str:
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return "Unknown"
