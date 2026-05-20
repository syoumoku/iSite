from __future__ import annotations

from dataclasses import dataclass

from isite2.domain.models import ReviewItem
from isite2.rules.validation import is_concrete_review_action

MAP_READY_STATUSES = {"Verified", "Geocoded", "Cross-Checked"}


@dataclass(frozen=True)
class CoordinateResolution:
    latitude: float
    longitude: float
    geocode_precision: str
    map_source: str
    map_source_date: str | None
    coordinate_status: str
    review_item: ReviewItem | None = None

    @property
    def map_ready(self) -> bool:
        return self.coordinate_status in MAP_READY_STATUSES and self.review_item is None


class CoordinateResolver:
    def __init__(self, country_registry: dict) -> None:
        self.country_registry = country_registry

    def resolve(self, candidate: dict) -> CoordinateResolution:
        coordinate = candidate.get("coordinate", {})
        latitude = float(coordinate.get("latitude"))
        longitude = float(coordinate.get("longitude"))
        status = coordinate.get("coordinate_status") or "Verified"
        issue = self._coordinate_issue(latitude, longitude, status)
        review_item = None
        if issue:
            status = "Review Required"
            action = (
                f"核验 {candidate['property_name']} Google Maps/OSM 坐标，"
                "补充官方地址或地图链接并记录来源日期。"
            )
            if not is_concrete_review_action(action):
                action = "核验 Google Maps 坐标并记录来源链接和日期。"
            review_item = ReviewItem(reason=issue, next_action=action)

        return CoordinateResolution(
            latitude=latitude,
            longitude=longitude,
            geocode_precision=coordinate.get("geocode_precision", "Unknown"),
            map_source=coordinate.get("map_source", "source registry"),
            map_source_date=coordinate.get("map_source_date"),
            coordinate_status=status,
            review_item=review_item,
        )

    def _coordinate_issue(self, latitude: float, longitude: float, status: str) -> str | None:
        bbox = self.country_registry.get("bbox", {})
        if status not in MAP_READY_STATUSES:
            return f"坐标状态为 {status}，需人工核验后才可用于地图展示。"
        if not (
            float(bbox["min_latitude"])
            <= latitude
            <= float(bbox["max_latitude"])
            and float(bbox["min_longitude"])
            <= longitude
            <= float(bbox["max_longitude"])
        ):
            return "候选物业坐标超出国家边界 bbox，不能直接进入地图展示。"
        return None


def is_map_ready_coordinate(coordinate_status: str) -> bool:
    return coordinate_status in MAP_READY_STATUSES
