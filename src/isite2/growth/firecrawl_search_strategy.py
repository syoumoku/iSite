from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from isite2.rules.config_loader import load_firecrawl_search_strategy


@dataclass(frozen=True)
class FirecrawlSearchQuery:
    phase: str
    scene_type: str
    query: str
    limit: int
    scrape: bool
    scan_priority: int
    priority_band: str
    expected_indicators: list[str]
    preferred_channels: list[str]
    fallback_channels: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PHASE_QUERY_KEYS = {
    "discovery": "discovery_queries",
    "metric_extraction": "candidate_metric_queries",
    "second_source_strengthening": "strengthening_queries",
    "build_status_search": "build_status_queries",
}


def build_firecrawl_queries(
    *,
    country: str,
    scene_type: str,
    property_name: str | None = None,
    city: str | None = None,
    phases: list[str] | None = None,
    strategy: dict[str, Any] | None = None,
) -> list[FirecrawlSearchQuery]:
    search_strategy = strategy or load_firecrawl_search_strategy()
    scene_strategy = search_strategy.get("scene_strategies", {}).get(scene_type)
    if scene_strategy is None:
        raise KeyError(f"unknown scene_type in firecrawl strategy: {scene_type}")

    selected_phases = phases or ["discovery"]
    default_limit = int(search_strategy.get("defaults", {}).get("limit", 3))
    default_scrape = bool(search_strategy.get("defaults", {}).get("scrape", True))
    context = {
        "country": country,
        "city": city or "",
        "property_name": property_name or "",
        "scene_type": scene_type,
    }

    queries: list[FirecrawlSearchQuery] = []
    for phase in selected_phases:
        query_key = PHASE_QUERY_KEYS.get(phase)
        if query_key is None:
            raise KeyError(f"unknown firecrawl query phase: {phase}")
        if phase != "discovery" and not property_name:
            continue
        for template in scene_strategy.get(query_key, []) or []:
            rendered = template.format(**context).strip()
            if not rendered:
                continue
            queries.append(
                FirecrawlSearchQuery(
                    phase=phase,
                    scene_type=scene_type,
                    query=rendered,
                    limit=default_limit,
                    scrape=default_scrape,
                    scan_priority=int(scene_strategy.get("scan_priority", 50)),
                    priority_band=str(scene_strategy.get("priority_band", "secondary_landmark")),
                    expected_indicators=list(scene_strategy.get("objective_indicators", [])),
                    preferred_channels=list(scene_strategy.get("preferred_channels", [])),
                    fallback_channels=list(scene_strategy.get("fallback_channels", [])),
                )
            )
    return queries


def build_gap_closure_queries(
    *,
    country: str,
    scene_type: str,
    property_name: str,
    city: str | None = None,
    weak_single_tier3: bool = False,
    proxy_only: bool = False,
    missing_metric: bool = False,
    missing_build_status: bool = False,
    strategy: dict[str, Any] | None = None,
) -> list[FirecrawlSearchQuery]:
    phases: list[str] = []
    if missing_metric:
        phases.append("metric_extraction")
    if weak_single_tier3 or proxy_only:
        phases.append("second_source_strengthening")
    if missing_build_status:
        phases.append("build_status_search")
    if not phases:
        phases.append("second_source_strengthening")

    return build_firecrawl_queries(
        country=country,
        scene_type=scene_type,
        property_name=property_name,
        city=city,
        phases=phases,
        strategy=strategy,
    )
