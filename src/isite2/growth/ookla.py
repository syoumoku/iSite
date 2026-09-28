from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class OoklaTile:
    quadkey: str
    latitude: float
    longitude: float
    avg_download_mbps: float
    avg_upload_mbps: float
    avg_latency_ms: float
    avg_loaded_latency_down_ms: float | None
    avg_loaded_latency_up_ms: float | None
    tests: int
    devices: int


@dataclass(frozen=True)
class OoklaTileMatch:
    tile: OoklaTile
    match_method: str
    distance_m: float
    confidence: str


def latlon_to_quadkey(latitude: float, longitude: float, *, zoom: int = 16) -> str:
    tile_x, tile_y = latlon_to_tile_xy(latitude, longitude, zoom=zoom)
    digits: list[str] = []
    for level in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (level - 1)
        if tile_x & mask:
            digit += 1
        if tile_y & mask:
            digit += 2
        digits.append(str(digit))
    return "".join(digits)


def tile_xy_to_quadkey(tile_x: int, tile_y: int, *, zoom: int = 16) -> str:
    digits: list[str] = []
    for level in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (level - 1)
        if tile_x & mask:
            digit += 1
        if tile_y & mask:
            digit += 2
        digits.append(str(digit))
    return "".join(digits)


def quadkey_to_tile_xy(quadkey: str) -> tuple[int, int, int]:
    tile_x = 0
    tile_y = 0
    zoom = len(quadkey)
    for offset, character in enumerate(quadkey):
        mask = 1 << (zoom - offset - 1)
        digit = int(character)
        if digit & 1:
            tile_x |= mask
        if digit & 2:
            tile_y |= mask
    return tile_x, tile_y, zoom


def quadkey_centroid(quadkey: str) -> tuple[float, float]:
    tile_x, tile_y, zoom = quadkey_to_tile_xy(quadkey)
    size = 1 << zoom
    longitude = ((tile_x + 0.5) / size) * 360.0 - 180.0
    mercator_y = 0.5 - (tile_y + 0.5) / size
    latitude = 90.0 - 360.0 * math.atan(math.exp(-mercator_y * 2 * math.pi)) / math.pi
    return latitude, longitude


def neighboring_quadkeys(latitude: float, longitude: float, *, zoom: int = 16) -> set[str]:
    tile_x, tile_y = latlon_to_tile_xy(latitude, longitude, zoom=zoom)
    size = 1 << zoom
    output: set[str] = set()
    for delta_x in (-1, 0, 1):
        for delta_y in (-1, 0, 1):
            x = min(size - 1, max(0, tile_x + delta_x))
            y = min(size - 1, max(0, tile_y + delta_y))
            output.add(tile_xy_to_quadkey(x, y, zoom=zoom))
    return output


def quadkeys_within_radius(
    latitude: float,
    longitude: float,
    radius_m: float,
    *,
    zoom: int = 16,
) -> set[str]:
    """Return z-level quadkeys whose centroids fall within a property radius."""
    center_x, center_y = latlon_to_tile_xy(latitude, longitude, zoom=zoom)
    size = 1 << zoom
    center_lat, center_lon = quadkey_centroid(
        tile_xy_to_quadkey(center_x, center_y, zoom=zoom)
    )
    east_x = min(size - 1, center_x + 1)
    south_y = min(size - 1, center_y + 1)
    _, east_lon = quadkey_centroid(tile_xy_to_quadkey(east_x, center_y, zoom=zoom))
    south_lat, _ = quadkey_centroid(tile_xy_to_quadkey(center_x, south_y, zoom=zoom))
    tile_width_m = max(1.0, haversine_m(center_lat, center_lon, center_lat, east_lon))
    tile_height_m = max(1.0, haversine_m(center_lat, center_lon, south_lat, center_lon))
    search_radius_tiles = int(math.ceil(radius_m / min(tile_width_m, tile_height_m))) + 2
    output: set[str] = set()
    for delta_x in range(-search_radius_tiles, search_radius_tiles + 1):
        for delta_y in range(-search_radius_tiles, search_radius_tiles + 1):
            x = center_x + delta_x
            y = center_y + delta_y
            if x < 0 or y < 0 or x >= size or y >= size:
                continue
            quadkey = tile_xy_to_quadkey(x, y, zoom=zoom)
            tile_latitude, tile_longitude = quadkey_centroid(quadkey)
            if haversine_m(latitude, longitude, tile_latitude, tile_longitude) <= radius_m:
                output.add(quadkey)
    return output


def latlon_to_tile_xy(latitude: float, longitude: float, *, zoom: int = 16) -> tuple[int, int]:
    latitude = min(85.05112878, max(-85.05112878, float(latitude)))
    longitude = min(180.0, max(-180.0, float(longitude)))
    x = (longitude + 180.0) / 360.0
    sin_lat = math.sin(math.radians(latitude))
    y = 0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)
    size = 1 << zoom
    return (
        min(size - 1, max(0, int(x * size))),
        min(size - 1, max(0, int(y * size))),
    )


def match_property_to_tile(
    latitude: float,
    longitude: float,
    tiles: list[OoklaTile],
    *,
    zoom: int = 16,
    max_distance_m: float = 1_000,
) -> OoklaTileMatch | None:
    if not tiles:
        return None
    containing_quadkey = latlon_to_quadkey(latitude, longitude, zoom=zoom)
    containing = [tile for tile in tiles if tile.quadkey == containing_quadkey]
    if containing:
        tile = sorted(
            containing,
            key=lambda item: haversine_m(latitude, longitude, item.latitude, item.longitude),
        )[0]
        distance = haversine_m(latitude, longitude, tile.latitude, tile.longitude)
        return OoklaTileMatch(
            tile=tile,
            match_method="containing_tile",
            distance_m=distance,
            confidence=ookla_confidence(
                "containing_tile",
                distance,
                tests=tile.tests,
                devices=tile.devices,
            ),
        )

    tile = min(
        tiles,
        key=lambda item: haversine_m(latitude, longitude, item.latitude, item.longitude),
    )
    distance = haversine_m(latitude, longitude, tile.latitude, tile.longitude)
    if distance > max_distance_m:
        return None
    return OoklaTileMatch(
        tile=tile,
        match_method="nearest_tile",
        distance_m=distance,
        confidence=ookla_confidence(
            "nearest_tile",
            distance,
            tests=tile.tests,
            devices=tile.devices,
        ),
    )


def ookla_confidence(
    match_method: str,
    distance_m: float,
    *,
    tests: int,
    devices: int,
) -> str:
    if match_method == "containing_tile" and tests >= 10 and devices >= 5:
        return "high"
    if distance_m <= 1_000 and tests >= 3 and devices >= 2:
        return "medium"
    return "low"


def ookla_tile_confidence(*, tests: int, devices: int) -> str:
    if tests >= 10 and devices >= 5:
        return "high"
    if tests >= 3 and devices >= 2:
        return "medium"
    return "low"


def classify_performance(
    *,
    download_mbps: float,
    loaded_latency_ms: float | None,
    download_p20: float,
    download_p40: float,
    latency_p60: float | None,
    latency_p80: float | None,
) -> str:
    poor_latency = (
        loaded_latency_ms is not None
        and latency_p80 is not None
        and loaded_latency_ms >= latency_p80
    )
    if download_mbps <= download_p20 or poor_latency:
        return "poor"
    moderate_latency = (
        loaded_latency_ms is not None
        and latency_p60 is not None
        and loaded_latency_ms >= latency_p60
    )
    if download_mbps <= download_p40 or moderate_latency:
        return "moderate"
    return "good"


def haversine_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_m = 6_371_008.8
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return earth_radius_m * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
