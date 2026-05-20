from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, HttpUrl

from isite2.domain.enums import SourceTier


class SearchResult(BaseModel):
    title: str
    url: HttpUrl | str
    source_name: str
    snippet: str = ""


class FetchedPage(BaseModel):
    source_url: HttpUrl | str
    source_name: str
    source_tier: SourceTier
    source_date: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    content_text: str
    robots_allowed: bool = True


class GeocodeResult(BaseModel):
    latitude: float
    longitude: float
    geocode_precision: str
    map_source: str
    city: str | None = None
    country: str | None = None
    display_name: str | None = None
    boundingbox: list[float] | None = None


class EvidenceExtractionResult(BaseModel):
    property_name: str
    field_group: str
    field_value: str
    source_url: HttpUrl | str
    source_name: str
    source_tier: SourceTier
    source_date: str | None = None
    extraction_status: str = "extracted"
    review_reason: str | None = None
    next_action: str | None = None
