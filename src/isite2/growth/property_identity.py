from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from isite2.db.models import EvidenceItemDB, PropertyDB, SourceCacheDB

KNOWN_PROPERTY = "known_property"
POSSIBLE_DUPLICATE = "possible_duplicate"
NEW_OPPORTUNITY = "new_opportunity"

_GENERIC_EVIDENCE_TOKENS = {
    "annual",
    "capacity",
    "details",
    "official",
    "passenger",
    "passengers",
    "profile",
    "public",
    "report",
    "reports",
    "seats",
    "source",
    "statistics",
    "stats",
    "traffic",
}
_TOKEN_SYNONYMS = {
    "centre": "center",
    "centres": "center",
    "centro": "center",
    "intl": "international",
    "int": "international",
    "aeroport": "airport",
    "aéroport": "airport",
    "stade": "stadium",
}


@dataclass(frozen=True)
class PropertyIdentity:
    country: str
    city: str
    property_name: str
    scene_type: str
    identity_key: str
    normalized_country: str
    normalized_city: str
    normalized_name: str
    normalized_scene_type: str
    coordinate_bucket: str | None = None
    source_urls: frozenset[str] = field(default_factory=frozenset)
    aliases: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class KnownOpportunityRecord:
    identity: PropertyIdentity
    property_id: str | None = None
    source: str = "unknown"
    google_maps_link: str | None = None


@dataclass(frozen=True)
class KnownOpportunityMatch:
    status: str
    record: KnownOpportunityRecord | None = None
    confidence: float = 0.0
    reason: str = ""


class KnownOpportunityIndex:
    def __init__(self, records: Iterable[KnownOpportunityRecord] | None = None) -> None:
        self._records: list[KnownOpportunityRecord] = []
        self._by_identity: dict[str, KnownOpportunityRecord] = {}
        self._by_country_scene_name: dict[tuple[str, str, str], list[KnownOpportunityRecord]] = {}
        self._by_coordinate_bucket: dict[tuple[str, str, str], list[KnownOpportunityRecord]] = {}
        self._source_urls: set[str] = set()
        if records:
            for record in records:
                self.add(record)

    @property
    def source_urls(self) -> set[str]:
        return set(self._source_urls)

    def add(self, record: KnownOpportunityRecord) -> None:
        identity = record.identity
        if identity.identity_key in self._by_identity:
            existing = self._by_identity[identity.identity_key]
            merged_urls = existing.identity.source_urls | identity.source_urls
            merged_aliases = existing.identity.aliases | identity.aliases
            merged_identity = PropertyIdentity(
                country=existing.identity.country,
                city=existing.identity.city,
                property_name=existing.identity.property_name,
                scene_type=existing.identity.scene_type,
                identity_key=existing.identity.identity_key,
                normalized_country=existing.identity.normalized_country,
                normalized_city=existing.identity.normalized_city,
                normalized_name=existing.identity.normalized_name,
                normalized_scene_type=existing.identity.normalized_scene_type,
                coordinate_bucket=existing.identity.coordinate_bucket
                or identity.coordinate_bucket,
                source_urls=frozenset(merged_urls),
                aliases=frozenset(merged_aliases),
            )
            record = KnownOpportunityRecord(
                identity=merged_identity,
                property_id=existing.property_id or record.property_id,
                source=existing.source,
                google_maps_link=existing.google_maps_link or record.google_maps_link,
            )
            self._replace_record(record)
            return

        self._records.append(record)
        self._index_record(record)

    def add_source_url(self, source_url: str | None) -> None:
        normalized = normalize_source_url(source_url)
        if normalized:
            self._source_urls.add(normalized)

    def knows_source_url(self, source_url: str | None) -> bool:
        normalized = normalize_source_url(source_url)
        return bool(normalized and normalized in self._source_urls)

    def match(
        self,
        *,
        country: str,
        city: str,
        property_name: str,
        scene_type: str,
        latitude: float | None = None,
        longitude: float | None = None,
        source_url: str | None = None,
        google_maps_link: str | None = None,
    ) -> KnownOpportunityMatch:
        source_url_key = normalize_source_url(source_url)
        if source_url_key and source_url_key in self._source_urls:
            return KnownOpportunityMatch(
                status=KNOWN_PROPERTY,
                confidence=1.0,
                reason="source_url already known",
            )

        identity = build_property_identity(
            country=country,
            city=city,
            property_name=property_name,
            scene_type=scene_type,
            latitude=latitude,
            longitude=longitude,
            source_urls=[source_url] if source_url else [],
        )
        record = self._by_identity.get(identity.identity_key)
        if record is not None:
            return KnownOpportunityMatch(
                status=KNOWN_PROPERTY,
                record=record,
                confidence=1.0,
                reason="identity key matched",
            )

        name_key = (
            identity.normalized_country,
            identity.normalized_scene_type,
            identity.normalized_name,
        )
        name_matches = self._by_country_scene_name.get(name_key, [])
        if len(name_matches) == 1 and not identity.normalized_city:
            return KnownOpportunityMatch(
                status=KNOWN_PROPERTY,
                record=name_matches[0],
                confidence=0.98,
                reason="country-scene-name matched with missing candidate city",
            )

        candidate_records = self._candidate_records_for(identity)
        possible = _best_possible_duplicate(
            identity,
            candidate_records,
            google_maps_link=google_maps_link,
        )
        if possible is not None:
            return possible
        return KnownOpportunityMatch(status=NEW_OPPORTUNITY, confidence=0.0)

    def _replace_record(self, record: KnownOpportunityRecord) -> None:
        old_record = self._by_identity.get(record.identity.identity_key)
        self._records = [
            record if item.identity.identity_key == record.identity.identity_key else item
            for item in self._records
        ]
        if old_record is not None:
            self._remove_record_indexes(old_record)
        self._index_record(record)

    def _remove_record_indexes(self, record: KnownOpportunityRecord) -> None:
        identity = record.identity
        name_key = (
            identity.normalized_country,
            identity.normalized_scene_type,
            identity.normalized_name,
        )
        if name_key in self._by_country_scene_name:
            self._by_country_scene_name[name_key] = [
                item
                for item in self._by_country_scene_name[name_key]
                if item.identity.identity_key != identity.identity_key
            ]
            if not self._by_country_scene_name[name_key]:
                del self._by_country_scene_name[name_key]
        if identity.coordinate_bucket:
            coordinate_key = (
                identity.normalized_country,
                identity.normalized_scene_type,
                identity.coordinate_bucket,
            )
            if coordinate_key in self._by_coordinate_bucket:
                self._by_coordinate_bucket[coordinate_key] = [
                    item
                    for item in self._by_coordinate_bucket[coordinate_key]
                    if item.identity.identity_key != identity.identity_key
                ]
                if not self._by_coordinate_bucket[coordinate_key]:
                    del self._by_coordinate_bucket[coordinate_key]

    def _rebuild(self) -> None:
        records = list(self._records)
        self._records = []
        self._by_identity.clear()
        self._by_country_scene_name.clear()
        self._by_coordinate_bucket.clear()
        self._source_urls.clear()
        for record in records:
            self._records.append(record)
            self._index_record(record)

    def _index_record(self, record: KnownOpportunityRecord) -> None:
        identity = record.identity
        self._by_identity[identity.identity_key] = record
        self._by_country_scene_name.setdefault(
            (
                identity.normalized_country,
                identity.normalized_scene_type,
                identity.normalized_name,
            ),
            [],
        ).append(record)
        if identity.coordinate_bucket:
            self._by_coordinate_bucket.setdefault(
                (
                    identity.normalized_country,
                    identity.normalized_scene_type,
                    identity.coordinate_bucket,
                ),
                [],
            ).append(record)
        for source_url in identity.source_urls:
            self._source_urls.add(source_url)
        if record.google_maps_link:
            self.add_source_url(record.google_maps_link)

    def _candidate_records_for(self, identity: PropertyIdentity) -> list[KnownOpportunityRecord]:
        records: list[KnownOpportunityRecord] = []
        if identity.coordinate_bucket:
            records.extend(
                self._by_coordinate_bucket.get(
                    (
                        identity.normalized_country,
                        identity.normalized_scene_type,
                        identity.coordinate_bucket,
                    ),
                    [],
                )
            )
        prefix = (identity.normalized_country, identity.normalized_scene_type)
        for key, key_records in self._by_country_scene_name.items():
            if key[:2] == prefix:
                records.extend(key_records)
        seen: set[str] = set()
        deduped: list[KnownOpportunityRecord] = []
        for record in records:
            key = record.identity.identity_key
            if key in seen:
                continue
            seen.add(key)
            deduped.append(record)
        return deduped


def build_property_identity(
    *,
    country: str,
    city: str,
    property_name: str,
    scene_type: str,
    latitude: float | None = None,
    longitude: float | None = None,
    source_urls: Iterable[str | None] | None = None,
    aliases: Iterable[str | None] | None = None,
) -> PropertyIdentity:
    normalized_country = normalize_text(country)
    normalized_city = normalize_text(city)
    normalized_scene_type = normalize_text(scene_type)
    normalized_name = normalize_property_name(property_name)
    identity_key = "|".join(
        [normalized_country, normalized_scene_type, normalized_city, normalized_name]
    )
    return PropertyIdentity(
        country=country,
        city=city,
        property_name=property_name,
        scene_type=scene_type,
        identity_key=identity_key,
        normalized_country=normalized_country,
        normalized_city=normalized_city,
        normalized_name=normalized_name,
        normalized_scene_type=normalized_scene_type,
        coordinate_bucket=coordinate_bucket(latitude, longitude),
        source_urls=frozenset(
            url for url in (normalize_source_url(item) for item in source_urls or []) if url
        ),
        aliases=frozenset(
            alias for alias in (normalize_property_name(item or "") for item in aliases or []) if alias
        ),
    )


def property_identity_key(
    *,
    country: str,
    city: str,
    property_name: str,
    scene_type: str,
) -> str:
    return build_property_identity(
        country=country,
        city=city,
        property_name=property_name,
        scene_type=scene_type,
    ).identity_key


def normalize_text(value: str | None) -> str:
    text_value = unicodedata.normalize("NFKD", str(value or ""))
    text_value = "".join(ch for ch in text_value if not unicodedata.combining(ch))
    text_value = text_value.casefold()
    text_value = text_value.replace("&", " and ")
    text_value = re.sub(r"['’`]", "", text_value)
    text_value = re.sub(r"[^a-z0-9]+", " ", text_value)
    tokens = [_TOKEN_SYNONYMS.get(token, token) for token in text_value.split()]
    return " ".join(tokens)


def normalize_property_name(value: str | None) -> str:
    tokens = normalize_text(value).split()
    while tokens and tokens[-1] in _GENERIC_EVIDENCE_TOKENS:
        tokens.pop()
    return " ".join(token for token in tokens if token)


def normalize_source_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(str(value).strip())
    if not parsed.scheme or not parsed.netloc:
        return str(value).strip().casefold()
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    path = re.sub(r"/+$", "", parsed.path or "")
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            query,
            "",
        )
    )


def coordinate_bucket(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    return f"{lat:.3f},{lon:.3f}"


def known_opportunity_index_from_registry(
    registry: dict[str, Any],
    *,
    session_factory: sessionmaker | None = None,
) -> KnownOpportunityIndex:
    index = KnownOpportunityIndex()
    for country, country_registry in registry.get("countries", {}).items():
        for candidate in country_registry.get("candidates", []) or []:
            index.add(_record_from_registry_candidate(country, candidate))
    if session_factory is not None:
        _add_database_records(index, session_factory)
    return index


def ensure_property_identity_schema(connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(properties)").all()
        }
        if "property_identity_key" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE properties ADD COLUMN property_identity_key TEXT"
            )
    elif dialect == "postgresql":
        connection.exec_driver_sql(
            "ALTER TABLE properties ADD COLUMN IF NOT EXISTS property_identity_key TEXT"
        )
    _backfill_property_identity_keys(connection)
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_properties_country_scene_identity "
        "ON properties (country, scene_type, property_identity_key)"
    )


def _record_from_registry_candidate(
    country: str,
    candidate: dict[str, Any],
) -> KnownOpportunityRecord:
    coordinate = candidate.get("coordinate") or {}
    source_urls = _candidate_source_urls(candidate)
    aliases = candidate.get("aliases", []) or []
    identity = build_property_identity(
        country=country,
        city=str(candidate.get("city") or ""),
        property_name=str(candidate.get("property_name") or ""),
        scene_type=str(candidate.get("scene_type") or ""),
        latitude=coordinate.get("latitude"),
        longitude=coordinate.get("longitude"),
        source_urls=source_urls,
        aliases=aliases,
    )
    return KnownOpportunityRecord(
        identity=identity,
        property_id=str(candidate.get("property_id") or "") or None,
        source="source_registry",
        google_maps_link=candidate.get("google_maps_link"),
    )


def _candidate_source_urls(candidate: dict[str, Any]) -> list[str]:
    urls = [
        str(item.get("source_url") or "")
        for item in candidate.get("evidence", []) or []
        if item.get("source_url")
    ]
    hero_image = candidate.get("hero_image") or {}
    for key in ("url", "source_url"):
        if hero_image.get(key):
            urls.append(str(hero_image[key]))
    if candidate.get("google_maps_link"):
        urls.append(str(candidate["google_maps_link"]))
    return urls


def _add_database_records(
    index: KnownOpportunityIndex,
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        property_rows = session.scalars(select(PropertyDB)).all()
        for row in property_rows:
            identity = build_property_identity(
                country=row.country,
                city=row.city,
                property_name=row.canonical_name,
                scene_type=row.scene_type,
                latitude=row.latitude,
                longitude=row.longitude,
                source_urls=[row.google_maps_link] if row.google_maps_link else [],
            )
            index.add(
                KnownOpportunityRecord(
                    identity=identity,
                    property_id=str(row.id),
                    source="active_db",
                    google_maps_link=row.google_maps_link,
                )
            )
        for source_url in session.scalars(select(SourceCacheDB.source_url)).all():
            index.add_source_url(source_url)
        for source_url in session.scalars(select(EvidenceItemDB.source_url)).all():
            index.add_source_url(source_url)


def _best_possible_duplicate(
    identity: PropertyIdentity,
    records: list[KnownOpportunityRecord],
    *,
    google_maps_link: str | None,
) -> KnownOpportunityMatch | None:
    best: tuple[float, KnownOpportunityRecord, str] | None = None
    normalized_google_maps_link = normalize_source_url(google_maps_link)
    for record in records:
        known = record.identity
        if normalized_google_maps_link and normalize_source_url(
            record.google_maps_link
        ) == normalized_google_maps_link:
            return KnownOpportunityMatch(
                status=POSSIBLE_DUPLICATE,
                record=record,
                confidence=0.95,
                reason="google maps link matched but identity key differed",
            )
        if identity.normalized_country != known.normalized_country:
            continue
        if identity.normalized_scene_type != known.normalized_scene_type:
            continue
        if identity.normalized_city and known.normalized_city:
            city_similarity = _similarity(identity.normalized_city, known.normalized_city)
            if city_similarity < 0.82:
                continue
        name_similarity = _similarity(identity.normalized_name, known.normalized_name)
        has_coordinate_overlap = (
            identity.coordinate_bucket is not None
            and identity.coordinate_bucket == known.coordinate_bucket
        )
        confidence = name_similarity + (0.08 if has_coordinate_overlap else 0.0)
        if confidence >= 0.9:
            reason = "high name similarity"
            if has_coordinate_overlap:
                reason += " and coordinate bucket matched"
            if best is None or confidence > best[0]:
                best = (confidence, record, reason)
    if best is None:
        return None
    return KnownOpportunityMatch(
        status=POSSIBLE_DUPLICATE,
        record=best[1],
        confidence=min(best[0], 0.99),
        reason=best[2],
    )


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _backfill_property_identity_keys(connection) -> None:
    rows = connection.execute(
        text(
            "SELECT id, country, city, canonical_name, scene_type "
            "FROM properties "
            "WHERE property_identity_key IS NULL OR property_identity_key = ''"
        )
    ).mappings()
    for row in rows:
        identity_key = property_identity_key(
            country=row["country"] or "",
            city=row["city"] or "",
            property_name=row["canonical_name"] or "",
            scene_type=row["scene_type"] or "",
        )
        connection.execute(
            text("UPDATE properties SET property_identity_key = :identity_key WHERE id = :id"),
            {"identity_key": identity_key, "id": row["id"]},
        )
