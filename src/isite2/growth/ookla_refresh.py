from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Engine

from isite2.db.models import (
    NetworkPerformanceObservationDB,
    NetworkPerformanceTileDB,
    PropertyDB,
    PropertyNetworkPerformanceRollupDB,
)
from isite2.db.session import create_session_factory
from isite2.growth.ookla import (
    OoklaTile,
    classify_performance,
    latlon_to_quadkey,
    match_property_to_tile,
    neighboring_quadkeys,
    ookla_confidence,
    ookla_tile_confidence,
    quadkey_centroid,
    quadkeys_within_radius,
)

OOKLA_LICENSE = "CC BY-NC-SA 4.0"


def refresh_ookla_from_artifact(
    engine: Engine,
    artifact_path: Path,
    *,
    public_enabled: bool = False,
) -> dict[str, Any]:
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact_checksum = _sha256(artifact_path)
    source_accessed_at = datetime.now(UTC)
    observations: list[dict[str, Any]] = []
    for property_result in payload.get("results", []):
        for service_type in ("mobile", "fixed"):
            item = (property_result.get("ookla") or {}).get(service_type)
            if not isinstance(item, dict):
                continue
            centroid = item.get("tile_centroid") or {}
            distance = float(item.get("distance_to_tile_centroid_m") or 0)
            if distance > 1_000:
                continue
            property_quadkey = latlon_to_quadkey(
                float(property_result["latitude"]),
                float(property_result["longitude"]),
            )
            match_method = (
                "containing_tile"
                if str(item.get("quadkey")) == property_quadkey
                else "nearest_tile"
            )
            observations.append(
                {
                    "property_id": str(property_result["id"]),
                    "country": str(property_result["country"]),
                    "service_type": service_type,
                    "period": _normalize_period(item.get("period") or payload.get("source_period")),
                    "quadkey": str(item["quadkey"]),
                    "tile_latitude": float(centroid["latitude"]),
                    "tile_longitude": float(centroid["longitude"]),
                    "avg_download_mbps": float(item["avg_download_mbps"]),
                    "avg_upload_mbps": float(item["avg_upload_mbps"]),
                    "avg_latency_ms": float(item["avg_latency_ms"]),
                    "avg_loaded_latency_down_ms": _float_or_none(
                        item.get("avg_loaded_latency_down_ms")
                    ),
                    "avg_loaded_latency_up_ms": _float_or_none(
                        item.get("avg_loaded_latency_up_ms")
                    ),
                    "tests": int(item["tests"]),
                    "devices": int(item["devices"]),
                    "match_method": match_method,
                    "distance_m": distance,
                    "confidence": ookla_confidence(
                        match_method,
                        distance,
                        tests=int(item["tests"]),
                        devices=int(item["devices"]),
                    ),
                    "source_url": str(item["source_url"]),
                    "source_checksum": artifact_checksum,
                    "source_accessed_at": source_accessed_at,
                    "license": OOKLA_LICENSE,
                }
            )
    summary = persist_ookla_observations(engine, observations)
    summary.update(
        {
            "mode": "ookla_artifact_refresh",
            "artifact": str(artifact_path),
            "artifact_checksum": artifact_checksum,
            "public_enabled": public_enabled,
            "public_exclusion_reason": (
                None
                if public_enabled
                else "license/product-use confirmation pending; internal QA only"
            ),
        }
    )
    return summary


def refresh_ookla_from_parquet(
    engine: Engine,
    *,
    period: str,
    service_type: str,
    parquet_url: str,
    country: str | None = None,
    source_checksum: str | None = None,
) -> dict[str, Any]:
    """Read only containing/neighbor z16 rows from official Parquet and persist matches."""
    if not source_checksum:
        source_checksum = _local_source_checksum(parquet_url)
    if not source_checksum:
        raise ValueError(
            "source_checksum is required for remote Ookla Parquet; record an "
            "official object checksum or ETag before import"
        )
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        statement = select(PropertyDB)
        if country:
            statement = statement.where(PropertyDB.country == country)
        properties = session.scalars(statement).all()
    quadkeys: set[str] = set()
    for property_row in properties:
        quadkeys.update(neighboring_quadkeys(property_row.latitude, property_row.longitude))
    tiles = _read_parquet_tiles(parquet_url, quadkeys)
    observations: list[dict[str, Any]] = []
    accessed_at = datetime.now(UTC)
    for property_row in properties:
        match = match_property_to_tile(
            property_row.latitude,
            property_row.longitude,
            tiles,
            max_distance_m=1_000,
        )
        if match is None:
            continue
        observations.append(
            {
                "property_id": str(property_row.id),
                "country": property_row.country,
                "service_type": service_type,
                "period": _normalize_period(period),
                "quadkey": match.tile.quadkey,
                "tile_latitude": match.tile.latitude,
                "tile_longitude": match.tile.longitude,
                "avg_download_mbps": match.tile.avg_download_mbps,
                "avg_upload_mbps": match.tile.avg_upload_mbps,
                "avg_latency_ms": match.tile.avg_latency_ms,
                "avg_loaded_latency_down_ms": match.tile.avg_loaded_latency_down_ms,
                "avg_loaded_latency_up_ms": match.tile.avg_loaded_latency_up_ms,
                "tests": match.tile.tests,
                "devices": match.tile.devices,
                "match_method": match.match_method,
                "distance_m": match.distance_m,
                "confidence": match.confidence,
                "source_url": parquet_url,
                "source_checksum": source_checksum,
                "source_accessed_at": accessed_at,
                "license": OOKLA_LICENSE,
            }
        )
    summary = persist_ookla_observations(engine, observations)
    summary.update(
        {
            "mode": "ookla_parquet_refresh",
            "period": _normalize_period(period),
            "service_type": service_type,
            "source_url": parquet_url,
            "source_checksum": source_checksum,
        }
    )
    return summary


def refresh_ookla_radius_tiles_from_parquet(
    engine: Engine,
    *,
    period: str,
    service_type: str,
    parquet_url: str,
    country: str,
    radius_m: int = 5_000,
    source_checksum: str | None = None,
) -> dict[str, Any]:
    """Persist all official Ookla z16 tiles within a radius of active properties."""
    if not source_checksum:
        source_checksum = _local_source_checksum(parquet_url)
    if not source_checksum:
        raise ValueError(
            "source_checksum is required for remote Parquet "
            "(official checksum, object ETag, or retained manifest hash)"
        )
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        properties = session.scalars(
            select(PropertyDB).where(PropertyDB.country == country)
        ).all()
    target_quadkeys: set[str] = set()
    for property_row in properties:
        target_quadkeys.update(
            quadkeys_within_radius(
                property_row.latitude,
                property_row.longitude,
                radius_m,
            )
        )
    tiles = _read_parquet_tiles(parquet_url, target_quadkeys)
    summary = persist_ookla_tiles(
        engine,
        country=country,
        service_type=service_type,
        period=_normalize_period(period),
        tiles=tiles,
        source_url=parquet_url,
        source_checksum=source_checksum,
    )
    summary.update(
        {
            "mode": "ookla_radius_tile_refresh",
            "country": country,
            "period": _normalize_period(period),
            "service_type": service_type,
            "radius_m": radius_m,
            "property_count": len(properties),
            "target_quadkey_count": len(target_quadkeys),
            "source_url": parquet_url,
            "source_checksum": source_checksum,
        }
    )
    return summary


def refresh_ookla_radius_tiles_from_arcgis_feature_layer(
    engine: Engine,
    *,
    feature_layer_url: str,
    country: str,
    radius_m: int = 5_000,
    period: str | None = None,
    source_checksum: str | None = None,
) -> dict[str, Any]:
    """Persist public Ookla mobile tiles from an ArcGIS FeatureServer radius query."""
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        properties = session.scalars(
            select(PropertyDB).where(PropertyDB.country == country)
        ).all()
    tiles_by_period_quadkey: dict[tuple[str, str], OoklaTile] = {}
    query_count = 0
    for property_row in properties:
        features = _query_arcgis_ookla_mobile_tiles(
            feature_layer_url,
            latitude=property_row.latitude,
            longitude=property_row.longitude,
            radius_m=radius_m,
            period=period,
        )
        query_count += 1
        for feature in features:
            tile = _arcgis_feature_to_ookla_tile(feature)
            if tile is None:
                continue
            tile_period = _arcgis_feature_period(feature) or _normalize_period(period)
            if not tile_period:
                continue
            key = (tile_period, tile.quadkey)
            existing = tiles_by_period_quadkey.get(key)
            if existing is None or tile.tests > existing.tests:
                tiles_by_period_quadkey[key] = tile
    grouped: dict[str, list[OoklaTile]] = {}
    for (tile_period, _quadkey), tile in tiles_by_period_quadkey.items():
        grouped.setdefault(tile_period, []).append(tile)
    counts: Counter[str] = Counter()
    classified_count = 0
    for tile_period, tiles in sorted(grouped.items()):
        result = persist_ookla_tiles(
            engine,
            country=country,
            service_type="mobile",
            period=tile_period,
            tiles=tiles,
            source_url=feature_layer_url,
            source_checksum=source_checksum or "arcgis-feature-layer",
        )
        counts.update(result.get("counts", {}))
        classified_count += int(result.get("classified_count") or 0)
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "mode": "ookla_arcgis_feature_layer_radius_refresh",
        "country": country,
        "service_type": "mobile",
        "radius_m": radius_m,
        "property_count": len(properties),
        "query_count": query_count,
        "tile_count": len(tiles_by_period_quadkey),
        "periods": sorted(grouped),
        "counts": dict(counts),
        "classified_count": classified_count,
        "source_url": feature_layer_url,
        "source_checksum": source_checksum or "arcgis-feature-layer",
    }


def persist_ookla_observations(
    engine: Engine,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    session_factory = create_session_factory(engine)
    counts: Counter[str] = Counter()
    touched_groups: set[tuple[str, str, str]] = set()
    with session_factory.begin() as session:
        property_ids = set(session.scalars(select(PropertyDB.id)).all())
        for item in observations:
            if item["property_id"] not in property_ids:
                counts["unknown_property_skipped"] += 1
                continue
            existing = session.scalar(
                select(NetworkPerformanceObservationDB).where(
                    NetworkPerformanceObservationDB.property_id == item["property_id"],
                    NetworkPerformanceObservationDB.service_type == item["service_type"],
                    NetworkPerformanceObservationDB.period == item["period"],
                )
            )
            if existing is None:
                session.add(NetworkPerformanceObservationDB(id=str(uuid4()), **item))
                counts["observations_inserted"] += 1
            elif (
                existing.quadkey == item["quadkey"]
                and existing.source_checksum == item["source_checksum"]
            ):
                counts["duplicates_suppressed"] += 1
            else:
                for key, value in item.items():
                    setattr(existing, key, value)
                counts["observations_updated"] += 1
            touched_groups.add((item["country"], item["service_type"], item["period"]))
    rollup_count = 0
    for country, service_type, period in sorted(touched_groups):
        rollup_count += _refresh_group_rollups(engine, country, service_type, period)
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "counts": dict(counts),
        "rollup_count": rollup_count,
        "group_count": len(touched_groups),
    }


def persist_ookla_tiles(
    engine: Engine,
    *,
    country: str,
    service_type: str,
    period: str,
    tiles: list[OoklaTile],
    source_url: str,
    source_checksum: str | None,
) -> dict[str, Any]:
    session_factory = create_session_factory(engine)
    counts: Counter[str] = Counter()
    accessed_at = datetime.now(UTC)
    with session_factory.begin() as session:
        for tile in tiles:
            existing = session.scalar(
                select(NetworkPerformanceTileDB).where(
                    NetworkPerformanceTileDB.country == country,
                    NetworkPerformanceTileDB.service_type == service_type,
                    NetworkPerformanceTileDB.period == period,
                    NetworkPerformanceTileDB.quadkey == tile.quadkey,
                )
            )
            values = {
                "tile_latitude": tile.latitude,
                "tile_longitude": tile.longitude,
                "avg_download_mbps": tile.avg_download_mbps,
                "avg_upload_mbps": tile.avg_upload_mbps,
                "avg_latency_ms": tile.avg_latency_ms,
                "avg_loaded_latency_down_ms": tile.avg_loaded_latency_down_ms,
                "avg_loaded_latency_up_ms": tile.avg_loaded_latency_up_ms,
                "tests": tile.tests,
                "devices": tile.devices,
                "confidence": ookla_tile_confidence(tests=tile.tests, devices=tile.devices),
                "performance_class": "unclassified",
                "download_percentile": None,
                "loaded_latency_percentile": None,
                "source_url": source_url,
                "source_checksum": source_checksum,
                "source_accessed_at": accessed_at,
                "license": OOKLA_LICENSE,
                "updated_at": accessed_at,
            }
            if existing is None:
                session.add(
                    NetworkPerformanceTileDB(
                        id=str(uuid4()),
                        country=country,
                        service_type=service_type,
                        period=period,
                        quadkey=tile.quadkey,
                        **values,
                    )
                )
                counts["tiles_inserted"] += 1
            elif (
                existing.source_checksum == source_checksum
                and existing.avg_download_mbps == tile.avg_download_mbps
                and existing.tests == tile.tests
                and existing.devices == tile.devices
            ):
                counts["duplicates_suppressed"] += 1
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
                counts["tiles_updated"] += 1
    classified_count = _refresh_tile_group_classes(engine, country, service_type, period)
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "counts": dict(counts),
        "tile_count": len(tiles),
        "classified_count": classified_count,
    }


def _refresh_group_rollups(
    engine: Engine,
    country: str,
    service_type: str,
    period: str,
) -> int:
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        rows = session.scalars(
            select(NetworkPerformanceObservationDB).where(
                NetworkPerformanceObservationDB.country == country,
                NetworkPerformanceObservationDB.service_type == service_type,
                NetworkPerformanceObservationDB.period == period,
            )
        ).all()
        if not rows:
            return 0
        downloads = sorted(row.avg_download_mbps for row in rows)
        loaded_latencies = sorted(
            row.avg_loaded_latency_down_ms
            for row in rows
            if row.avg_loaded_latency_down_ms is not None
        )
        download_p20 = _percentile(downloads, 0.2)
        download_p40 = _percentile(downloads, 0.4)
        latency_p60 = _percentile(loaded_latencies, 0.6) if loaded_latencies else None
        latency_p80 = _percentile(loaded_latencies, 0.8) if loaded_latencies else None
        updated = 0
        for observation in rows:
            existing = session.scalar(
                select(PropertyNetworkPerformanceRollupDB).where(
                    PropertyNetworkPerformanceRollupDB.property_id == observation.property_id,
                    PropertyNetworkPerformanceRollupDB.service_type == service_type,
                    PropertyNetworkPerformanceRollupDB.period == period,
                )
            )
            performance_class = classify_performance(
                download_mbps=observation.avg_download_mbps,
                loaded_latency_ms=observation.avg_loaded_latency_down_ms,
                download_p20=download_p20,
                download_p40=download_p40,
                latency_p60=latency_p60,
                latency_p80=latency_p80,
            )
            values = {
                "observation_id": observation.id,
                "performance_class": performance_class,
                "confidence": observation.confidence,
                "download_percentile": _rank_percentile(
                    downloads,
                    observation.avg_download_mbps,
                ),
                "loaded_latency_percentile": (
                    _rank_percentile(
                        loaded_latencies,
                        observation.avg_loaded_latency_down_ms,
                    )
                    if observation.avg_loaded_latency_down_ms is not None and loaded_latencies
                    else None
                ),
                "trend": _quarter_trend(session, observation, performance_class),
                "calculated_at": datetime.now(UTC),
            }
            if existing is None:
                session.add(
                    PropertyNetworkPerformanceRollupDB(
                        id=str(uuid4()),
                        property_id=observation.property_id,
                        country=country,
                        service_type=service_type,
                        period=period,
                        **values,
                    )
                )
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
            updated += 1
        return updated


def _refresh_tile_group_classes(
    engine: Engine,
    country: str,
    service_type: str,
    period: str,
) -> int:
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        rows = session.scalars(
            select(NetworkPerformanceTileDB).where(
                NetworkPerformanceTileDB.country == country,
                NetworkPerformanceTileDB.service_type == service_type,
                NetworkPerformanceTileDB.period == period,
            )
        ).all()
        if not rows:
            return 0
        downloads = sorted(row.avg_download_mbps for row in rows)
        loaded_latencies = sorted(
            row.avg_loaded_latency_down_ms
            for row in rows
            if row.avg_loaded_latency_down_ms is not None
        )
        download_p20 = _percentile(downloads, 0.2)
        download_p40 = _percentile(downloads, 0.4)
        latency_p60 = _percentile(loaded_latencies, 0.6) if loaded_latencies else None
        latency_p80 = _percentile(loaded_latencies, 0.8) if loaded_latencies else None
        for row in rows:
            row.performance_class = classify_performance(
                download_mbps=row.avg_download_mbps,
                loaded_latency_ms=row.avg_loaded_latency_down_ms,
                download_p20=download_p20,
                download_p40=download_p40,
                latency_p60=latency_p60,
                latency_p80=latency_p80,
            )
            row.download_percentile = _rank_percentile(downloads, row.avg_download_mbps)
            row.loaded_latency_percentile = (
                _rank_percentile(loaded_latencies, row.avg_loaded_latency_down_ms)
                if row.avg_loaded_latency_down_ms is not None and loaded_latencies
                else None
            )
            row.updated_at = datetime.now(UTC)
        return len(rows)


def _quarter_trend(session, current, current_class: str) -> str | None:
    if current.confidence not in {"high", "medium"}:
        return None
    previous_period = _previous_period(current.period)
    if previous_period is None:
        return None
    previous = session.scalar(
        select(PropertyNetworkPerformanceRollupDB).where(
            PropertyNetworkPerformanceRollupDB.property_id == current.property_id,
            PropertyNetworkPerformanceRollupDB.service_type == current.service_type,
            PropertyNetworkPerformanceRollupDB.period == previous_period,
        )
    )
    if previous is None or previous.confidence not in {"high", "medium"}:
        return None
    rank = {"poor": 0, "moderate": 1, "good": 2}
    delta = rank[current_class] - rank[previous.performance_class]
    return "improving" if delta > 0 else "degrading" if delta < 0 else "stable"


def _read_parquet_tiles(parquet_url: str, quadkeys: set[str]) -> list[OoklaTile]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required for Ookla Parquet refresh") from exc
    if not quadkeys:
        return []
    connection = duckdb.connect()
    placeholders = ", ".join("?" for _ in quadkeys)
    description = connection.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)",
        [parquet_url],
    ).fetchall()
    available = {str(row[0]) for row in description}
    loaded_down = "avg_d_lat_ms" if "avg_d_lat_ms" in available else "NULL"
    loaded_up = "avg_u_lat_ms" if "avg_u_lat_ms" in available else "NULL"
    statement = f"""
        SELECT quadkey, avg_d_kbps, avg_u_kbps, avg_lat_ms,
               {loaded_down}, {loaded_up}, tests, devices
        FROM read_parquet(?)
        WHERE quadkey IN ({placeholders})
    """
    try:
        rows = connection.execute(
            statement,
            [parquet_url, *sorted(quadkeys)],
        ).fetchall()
    finally:
        connection.close()
    output = []
    for row in rows:
        latitude, longitude = quadkey_centroid(str(row[0]))
        output.append(
            OoklaTile(
                quadkey=str(row[0]),
                latitude=latitude,
                longitude=longitude,
                avg_download_mbps=float(row[1]) / 1000,
                avg_upload_mbps=float(row[2]) / 1000,
                avg_latency_ms=float(row[3]),
                avg_loaded_latency_down_ms=_float_or_none(row[4]),
                avg_loaded_latency_up_ms=_float_or_none(row[5]),
                tests=int(row[6]),
                devices=int(row[7]),
            )
        )
    return output


def _query_arcgis_ookla_mobile_tiles(
    feature_layer_url: str,
    *,
    latitude: float,
    longitude: float,
    radius_m: int,
    period: str | None = None,
) -> list[dict[str, Any]]:
    period_filter = _arcgis_period_where(period)
    params = {
        "f": "geojson",
        "where": period_filter,
        "outFields": "QuadKey,CalYear,CalQuarter,AvgDown,AvgUp,AvgLat,Tests,Devices",
        "returnGeometry": "false",
        "geometry": json.dumps({"x": float(longitude), "y": float(latitude)}),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(int(radius_m)),
        "units": "esriSRUnit_Meter",
        "outSR": "4326",
        "resultRecordCount": "1000",
    }
    query_url = feature_layer_url.rstrip("/") + "/query?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        query_url,
        headers={"User-Agent": "isite2-ookla-poc/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return list(payload.get("features") or [])


def _arcgis_feature_to_ookla_tile(feature: dict[str, Any]) -> OoklaTile | None:
    properties = feature.get("properties") or {}
    quadkey = str(properties.get("QuadKey") or "").strip()
    if not quadkey:
        return None
    latitude, longitude = quadkey_centroid(quadkey)
    return OoklaTile(
        quadkey=quadkey,
        latitude=latitude,
        longitude=longitude,
        avg_download_mbps=float(properties["AvgDown"]),
        avg_upload_mbps=float(properties["AvgUp"]),
        avg_latency_ms=float(properties["AvgLat"]),
        avg_loaded_latency_down_ms=None,
        avg_loaded_latency_up_ms=None,
        tests=int(properties["Tests"]),
        devices=int(properties["Devices"]),
    )


def _arcgis_feature_period(feature: dict[str, Any]) -> str | None:
    properties = feature.get("properties") or {}
    year = properties.get("CalYear")
    quarter = properties.get("CalQuarter")
    if year is None or quarter is None:
        return None
    return _normalize_period(f"{year} Q{quarter}")


def _arcgis_period_where(period: str | None) -> str:
    if not period:
        return "1=1"
    normalized = _normalize_period(period)
    parts = normalized.split()
    if len(parts) != 2 or not parts[1].startswith("Q"):
        return "1=1"
    try:
        year = int(parts[0])
        quarter = int(parts[1][1:])
    except ValueError:
        return "1=1"
    return f"CalYear = {year} AND CalQuarter = {quarter}"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile for an empty list")
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(len(values) - 1, lower + 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _rank_percentile(values: list[float], value: float) -> float:
    if len(values) <= 1:
        return 0.5
    below_or_equal = sum(item <= value for item in values) - 1
    return max(0.0, min(1.0, below_or_equal / (len(values) - 1)))


def _normalize_period(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" QUARTER ", " Q")
    parts = text.replace("-", " ").split()
    if len(parts) >= 2 and parts[1].startswith("Q"):
        return f"{parts[0]} {parts[1]}"
    if len(parts) >= 2 and parts[1].isdigit():
        return f"{parts[0]} Q{parts[1]}"
    return text


def _previous_period(period: str) -> str | None:
    parts = period.split()
    if len(parts) != 2 or not parts[1].startswith("Q"):
        return None
    try:
        year = int(parts[0])
        quarter = int(parts[1][1:])
    except ValueError:
        return None
    return f"{year - 1} Q4" if quarter == 1 else f"{year} Q{quarter - 1}"


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_source_checksum(source: str) -> str | None:
    path = Path(source)
    return _sha256(path) if path.exists() and path.is_file() else None
