from __future__ import annotations

import hashlib
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class FootfallOverlayCell:
    cell_id: str
    latitude: float
    longitude: float
    radius_m: float
    score: float
    metric_value: float | None
    metric_label: str
    confidence: str
    source_name: str
    source_url: str | None
    period: str | None
    resolution_m: float | None
    license: str | None
    distance_to_property_m: float | None = None
    observation_kind: str | None = None


@dataclass(frozen=True)
class FootfallOverlayResult:
    status: str
    provider: str
    message: str
    cells: list[FootfallOverlayCell]


class PlacerFootfallClient:
    """Small Placer.ai adapter for property-level overlay data.

    The endpoint template is intentionally configurable because Placer PAPI access
    is account-specific. Tests and deployments can point it at a stable adapter
    endpoint that returns visits/activity cells without leaking the API key.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        endpoint_template: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("PLACER_API_KEY", "")).strip()
        self.base_url = (
            base_url if base_url is not None else os.getenv("PLACER_BASE_URL", "https://papi.placer.ai")
        ).strip().rstrip("/")
        self.endpoint_template = (
            endpoint_template
            if endpoint_template is not None
            else os.getenv("PLACER_FOOTFALL_ENDPOINT_TEMPLATE", "")
        ).strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.endpoint_template)

    def overlay_cells(
        self,
        *,
        property_id: str,
        property_name: str,
        latitude: float,
        longitude: float,
        radius_m: int,
    ) -> FootfallOverlayResult:
        if not self.api_key:
            return FootfallOverlayResult(
                status="not_configured",
                provider="placer_ai",
                message="Placer.ai API key is not configured.",
                cells=[],
            )
        if not self.endpoint_template:
            return FootfallOverlayResult(
                status="not_configured",
                provider="placer_ai",
                message="Placer.ai footfall endpoint template is not configured.",
                cells=[],
            )

        url = self._endpoint_url(
            property_id=property_id,
            property_name=property_name,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
        )
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = client.get(url, headers={"x-api-key": self.api_key})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                status = "unauthorized"
            elif status_code == 429:
                status = "rate_limited"
            else:
                status = "upstream_error"
            return FootfallOverlayResult(
                status=status,
                provider="placer_ai",
                message=f"Placer.ai request failed with HTTP {status_code}.",
                cells=[],
            )
        except httpx.HTTPError as exc:
            return FootfallOverlayResult(
                status="upstream_error",
                provider="placer_ai",
                message=f"Placer.ai request failed: {type(exc).__name__}.",
                cells=[],
            )

        payload = response.json()
        cells = parse_placer_footfall_cells(
            payload,
            property_id=property_id,
            fallback_latitude=latitude,
            fallback_longitude=longitude,
        )
        return FootfallOverlayResult(
            status="ready" if cells else "no_data",
            provider="placer_ai",
            message="Placer.ai footfall layer ready." if cells else "No Placer.ai footfall cells are available for this property.",
            cells=cells,
        )

    def _endpoint_url(
        self,
        *,
        property_id: str,
        property_name: str,
        latitude: float,
        longitude: float,
        radius_m: int,
    ) -> str:
        path = self.endpoint_template.format(
            property_id=quote(property_id, safe=""),
            property_name=quote(property_name, safe=""),
            lat=latitude,
            latitude=latitude,
            lng=longitude,
            lon=longitude,
            longitude=longitude,
            radius_m=radius_m,
        )
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"


def parse_placer_footfall_cells(
    payload: dict[str, Any],
    *,
    property_id: str,
    fallback_latitude: float,
    fallback_longitude: float,
) -> list[FootfallOverlayCell]:
    raw_cells = _candidate_rows(payload)
    if not raw_cells:
        raw_cells = _synthetic_cells_from_summary(payload, fallback_latitude, fallback_longitude)
    values = [
        value
        for value in (_metric_value(row) for row in raw_cells)
        if value is not None and math.isfinite(value)
    ]
    max_value = max(values) if values else None
    cells: list[FootfallOverlayCell] = []
    for index, row in enumerate(raw_cells):
        latitude = _float(row, "latitude", "lat", "center_lat", "centroid_lat")
        longitude = _float(row, "longitude", "lng", "lon", "center_lng", "center_lon", "centroid_lng", "centroid_lon")
        if latitude is None or longitude is None:
            latitude = fallback_latitude
            longitude = fallback_longitude
        metric_value = _metric_value(row)
        score = _score(metric_value, max_value, row)
        if score <= 0:
            continue
        cells.append(
            FootfallOverlayCell(
                cell_id=str(row.get("id") or row.get("cell_id") or _stable_cell_id(property_id, index, latitude, longitude)),
                latitude=latitude,
                longitude=longitude,
                radius_m=_float(row, "radius_m", "radius", "resolution_m", default=140.0) or 140.0,
                score=score,
                metric_value=metric_value,
                metric_label=str(row.get("metric_label") or row.get("label") or "Visits"),
                confidence=str(row.get("confidence") or row.get("quality") or "third_party"),
                source_name=str(row.get("source_name") or "Placer.ai"),
                source_url=_optional_str(row.get("source_url") or row.get("url")),
                period=_optional_str(row.get("period") or row.get("date_range") or row.get("window")),
                resolution_m=_float(row, "resolution_m", "radius_m", "radius"),
                license=_optional_str(row.get("license") or row.get("terms")),
            )
        )
    return cells


def footfall_cells_to_feature_collection(result: FootfallOverlayResult) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for cell in result.cells:
        properties = {
            "cell_id": cell.cell_id,
            "score": round(cell.score, 4),
            "metric_value": cell.metric_value,
            "metric_label": cell.metric_label,
            "confidence": cell.confidence,
            "source_name": cell.source_name,
            "source_url": cell.source_url,
            "period": cell.period,
            "resolution_m": cell.resolution_m,
            "license": cell.license,
            "distance_to_property_m": (
                round(cell.distance_to_property_m, 1)
                if cell.distance_to_property_m is not None
                else None
            ),
            "observation_kind": cell.observation_kind,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [_circle_polygon(cell.longitude, cell.latitude, cell.radius_m)],
                },
                "properties": {
                    **properties,
                    "feature_kind": "footfall_cell",
                },
            }
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [cell.longitude, cell.latitude],
                },
                "properties": {
                    **properties,
                    "feature_kind": "footfall_center",
                    "center_label": "Observation center",
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "layer": "footfall",
        "provider": result.provider,
        "status": result.status,
        "message": result.message,
        "features": features,
    }


PUBLIC_GERMANY_FOOTFALL_OBSERVATIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "de-db-berlin-hbf-daily-visitors",
        "name": "Berlin Hauptbahnhof",
        "latitude": 52.525,
        "longitude": 13.36944,
        "metric_value": 330_000,
        "metric_label": "daily travelers / visitors",
        "period": "public station profile, daily average",
        "source_name": "Deutsche Bahn station public profile",
        "source_url": "https://www.bahnhof.de/berlin-hauptbahnhof",
        "license": "public webpage",
        "observation_kind": "transport_hub_daily_visitors",
        "radius_m": 260,
    },
    {
        "id": "de-db-hamburg-hbf-daily-visitors",
        "name": "Hamburg Hbf",
        "latitude": 53.55278,
        "longitude": 10.00639,
        "metric_value": 550_000,
        "metric_label": "daily travelers / visitors",
        "period": "public station profile, daily average",
        "source_name": "Deutsche Bahn station public profile",
        "source_url": "https://www.bahnhof.de/hamburg-hbf",
        "license": "public webpage",
        "observation_kind": "transport_hub_daily_visitors",
        "radius_m": 280,
    },
    {
        "id": "de-db-munich-hbf-daily-visitors",
        "name": "Munich Hbf",
        "latitude": 48.14028,
        "longitude": 11.56028,
        "metric_value": 450_000,
        "metric_label": "daily travelers / visitors",
        "period": "public station profile, daily average",
        "source_name": "Deutsche Bahn station public profile",
        "source_url": "https://www.bahnhof.de/muenchen-hbf",
        "license": "public webpage",
        "observation_kind": "transport_hub_daily_visitors",
        "radius_m": 270,
    },
    {
        "id": "de-db-frankfurt-hbf-daily-visitors",
        "name": "Frankfurt(Main)Hbf",
        "latitude": 50.10694,
        "longitude": 8.6625,
        "metric_value": 493_000,
        "metric_label": "daily travelers / visitors",
        "period": "public station profile, daily average",
        "source_name": "Deutsche Bahn station public profile",
        "source_url": "https://www.bahnhof.de/frankfurt-main-hbf",
        "license": "public webpage",
        "observation_kind": "transport_hub_daily_visitors",
        "radius_m": 270,
    },
    {
        "id": "de-wuerzburg-schoenbornstrasse-public-counter",
        "name": "Schönbornstraße pedestrian counter",
        "latitude": 49.795490162266525,
        "longitude": 9.931060093195851,
        "metric_value": 33_550,
        "metric_label": "pedestrians / day",
        "period": "2026-04-08 daily open data sample",
        "source_name": "Open Data Portal Würzburg",
        "source_url": "https://opendata.wuerzburg.de/explore/dataset/passantenzaehlung_tagesdaten/",
        "license": "DL-DE-BY-2.0",
        "observation_kind": "pedestrian_counter_daily",
        "radius_m": 160,
    },
    {
        "id": "de-wuerzburg-kaiserstrasse-public-counter",
        "name": "Kaiserstraße pedestrian counter",
        "latitude": 49.798622,
        "longitude": 9.934037,
        "metric_value": 23_313,
        "metric_label": "pedestrians / day",
        "period": "2026-04-08 daily open data sample",
        "source_name": "Open Data Portal Würzburg",
        "source_url": "https://opendata.wuerzburg.de/explore/dataset/passantenzaehlung_tagesdaten/",
        "license": "DL-DE-BY-2.0",
        "observation_kind": "pedestrian_counter_daily",
        "radius_m": 150,
    },
    {
        "id": "de-oldenburg-achternstrasse-public-counter",
        "name": "Achternstraße pedestrian counter",
        "latitude": 53.1421,
        "longitude": 8.2130,
        "metric_value": 23_887,
        "metric_label": "pedestrians / day",
        "period": "2025-01-02 daily open data sample",
        "source_name": "Offene Daten Oldenburg",
        "source_url": "https://opendata.oldenburg.de/dataset/passantenfrequenzen",
        "license": "DL-DE-BY-2.0",
        "observation_kind": "pedestrian_counter_daily",
        "radius_m": 150,
    },
)


def public_footfall_overlay(
    *,
    country: str,
    latitude: float,
    longitude: float,
    radius_m: int,
) -> FootfallOverlayResult:
    if country.casefold() != "germany":
        return FootfallOverlayResult(
            status="no_data",
            provider="public_open_data",
            message="No public footfall observation source is configured for this country.",
            cells=[],
        )
    nearby_rows: list[tuple[float, dict[str, Any]]] = []
    for row in PUBLIC_GERMANY_FOOTFALL_OBSERVATIONS:
        distance_m = _distance_m(
            latitude,
            longitude,
            float(row["latitude"]),
            float(row["longitude"]),
        )
        if distance_m <= radius_m:
            nearby_rows.append((distance_m, row))
    if not nearby_rows:
        return FootfallOverlayResult(
            status="no_data",
            provider="public_open_data",
            message=(
                "No Germany public footfall observation is available within "
                f"{_format_radius_km(radius_m)} of this property."
            ),
            cells=[],
        )
    max_value = max(float(row["metric_value"]) for _, row in nearby_rows)
    cells = [
        FootfallOverlayCell(
            cell_id=str(row["id"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            radius_m=float(row.get("radius_m") or 180),
            score=max(0.18, min(1.0, float(row["metric_value"]) / max_value)),
            metric_value=float(row["metric_value"]),
            metric_label=str(row["metric_label"]),
            confidence="official_public_proxy",
            source_name=str(row["source_name"]),
            source_url=_optional_str(row.get("source_url")),
            period=_optional_str(row.get("period")),
            resolution_m=float(row.get("radius_m") or 180),
            license=_optional_str(row.get("license")),
            distance_to_property_m=distance_m,
            observation_kind=_optional_str(row.get("observation_kind")),
        )
        for distance_m, row in sorted(nearby_rows, key=lambda item: item[0])
    ]
    return FootfallOverlayResult(
        status="ready",
        provider="public_open_data",
        message=(
            f"{len(cells)} public footfall observation"
            f"{'' if len(cells) == 1 else 's'} within {_format_radius_km(radius_m)}."
        ),
        cells=cells,
    )


def _candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        "cells",
        "grid",
        "tiles",
        "heatmap",
        "points",
        "segments",
        "nearby_activity",
        "data",
        "results",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _candidate_rows(value)
            if nested:
                return nested
    return []


def _synthetic_cells_from_summary(
    payload: dict[str, Any],
    latitude: float,
    longitude: float,
) -> list[dict[str, Any]]:
    metric = _metric_value(payload)
    if metric is None:
        return []
    return [
        {
            "latitude": latitude,
            "longitude": longitude,
            "radius_m": 180,
            "metric_value": metric,
            "metric_label": payload.get("metric_label") or "Visits",
            "source_name": payload.get("source_name") or "Placer.ai",
            "source_url": payload.get("source_url"),
            "period": payload.get("period"),
            "confidence": payload.get("confidence") or "third_party_summary",
            "license": payload.get("license"),
        }
    ]


def _metric_value(row: dict[str, Any]) -> float | None:
    return _float(
        row,
        "metric_value",
        "visits",
        "visit_count",
        "footfall",
        "traffic",
        "activity",
        "density",
        "estimated_visits",
    )


def _score(value: float | None, max_value: float | None, row: dict[str, Any]) -> float:
    configured_score = _float(row, "score", "intensity", "normalized_score")
    if configured_score is not None:
        return max(0.0, min(1.0, configured_score if configured_score <= 1 else configured_score / 100))
    if value is None or max_value is None or max_value <= 0:
        return 0.0
    return max(0.0, min(1.0, value / max_value))


def _float(row: dict[str, Any], *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stable_cell_id(property_id: str, index: int, latitude: float, longitude: float) -> str:
    digest = hashlib.sha256(f"{property_id}|{index}|{latitude:.6f}|{longitude:.6f}".encode()).hexdigest()
    return digest[:16]


def _circle_polygon(longitude: float, latitude: float, radius_m: float, *, steps: int = 24) -> list[list[float]]:
    lat_radius = radius_m / 111_320.0
    lng_radius = radius_m / (111_320.0 * max(0.2, math.cos(math.radians(latitude))))
    points: list[list[float]] = []
    for index in range(steps):
        angle = 2 * math.pi * index / steps
        points.append([
            longitude + math.cos(angle) * lng_radius,
            latitude + math.sin(angle) * lat_radius,
        ])
    points.append(points[0])
    return points


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return earth_radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _format_radius_km(radius_m: int) -> str:
    radius_km = radius_m / 1000
    return f"{radius_km:g} km"


def footfall_runtime_status() -> dict[str, Any]:
    client = PlacerFootfallClient()
    return {
        "provider": "public_open_data",
        "configured": True,
        "requiresApiKey": False,
        "endpointConfigured": bool(client.endpoint_template),
        "placerConfigured": client.configured,
        "publicCountries": ["Germany"],
        "checkedAt": datetime.now(UTC).isoformat(),
    }


_CACHE: dict[str, tuple[float, FootfallOverlayResult]] = {}


def cached_placer_overlay(
    *,
    property_id: str,
    property_name: str,
    latitude: float,
    longitude: float,
    radius_m: int,
    ttl_seconds: int = 60 * 60,
) -> FootfallOverlayResult:
    key = f"{property_id}|{latitude:.6f}|{longitude:.6f}|{radius_m}"
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= ttl_seconds:
        return cached[1]
    result = PlacerFootfallClient().overlay_cells(
        property_id=property_id,
        property_name=property_name,
        latitude=latitude,
        longitude=longitude,
        radius_m=radius_m,
    )
    if result.status in {"ready", "no_data", "not_configured"}:
        _CACHE[key] = (now, result)
    return result


def cached_footfall_overlay(
    *,
    property_id: str,
    property_name: str,
    country: str,
    latitude: float,
    longitude: float,
    radius_m: int,
    ttl_seconds: int = 60 * 60,
) -> FootfallOverlayResult:
    key = f"public+placer|{country}|{property_id}|{latitude:.6f}|{longitude:.6f}|{radius_m}"
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= ttl_seconds:
        return cached[1]

    public_result = public_footfall_overlay(
        country=country,
        latitude=latitude,
        longitude=longitude,
        radius_m=radius_m,
    )
    if public_result.status == "ready":
        _CACHE[key] = (now, public_result)
        return public_result

    placer_client = PlacerFootfallClient()
    if placer_client.configured:
        placer_result = placer_client.overlay_cells(
            property_id=property_id,
            property_name=property_name,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
        )
        if placer_result.status in {"ready", "no_data", "not_configured"}:
            _CACHE[key] = (now, placer_result)
        return placer_result

    _CACHE[key] = (now, public_result)
    return public_result


def placer_runtime_status() -> dict[str, Any]:
    return footfall_runtime_status()
