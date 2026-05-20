from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from isite2.growth.localized_search_strategy import (
    LocalizedSearchQuery,
    build_localized_queries,
)
from isite2.rules.config_loader import load_free_structured_sources


@dataclass(frozen=True)
class StructuredSourcePlan:
    source_id: str
    name: str
    connector: str
    stage: str
    cost_class: str
    network_required: bool
    trust_tier: int | None
    firecrawl_replacement: bool
    evidence_roles: list[str]
    hard_metric_scenes: dict[str, list[str]]

    def supports_scene(self, scene_type: str) -> bool:
        return self.source_id == "identity_index" or scene_type in self.hard_metric_scenes

    def indicators_for_scene(self, scene_type: str) -> list[str]:
        return list(self.hard_metric_scenes.get(scene_type, []))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredFirstTask:
    country: str
    scene_type: str
    source_id: str
    source_name: str
    connector: str
    stage: str
    cost_class: str
    source_role: str
    indicators: list[str]
    use_firecrawl: bool
    scrape: bool
    property_name: str | None = None
    city: str | None = None
    query: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredFirstPlan:
    countries: list[str]
    scenes: list[str]
    property_target_count: int
    structured_tasks: list[StructuredFirstTask]
    localized_queries: list[LocalizedSearchQuery]
    firecrawl_tasks: list[StructuredFirstTask]
    source_counts: dict[str, int]
    phase_counts: dict[str, int]
    estimated_naive_firecrawl_requests: int
    estimated_firecrawl_requests_saved: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "countries": self.countries,
            "scenes": self.scenes,
            "property_target_count": self.property_target_count,
            "structured_tasks": [task.as_dict() for task in self.structured_tasks],
            "localized_queries": [query.as_dict() for query in self.localized_queries],
            "firecrawl_tasks": [task.as_dict() for task in self.firecrawl_tasks],
            "source_counts": dict(self.source_counts),
            "phase_counts": dict(self.phase_counts),
            "estimated_naive_firecrawl_requests": self.estimated_naive_firecrawl_requests,
            "estimated_firecrawl_requests_saved": self.estimated_firecrawl_requests_saved,
        }


def load_structured_source_plans(
    config: dict[str, Any] | None = None,
) -> list[StructuredSourcePlan]:
    source_config = config or load_free_structured_sources()
    ordered_ids = list(source_config.get("execution_order", []))
    sources = source_config.get("sources", {})
    plans: list[StructuredSourcePlan] = []
    for source_id in ordered_ids:
        source = sources.get(source_id)
        if source is None:
            continue
        plans.append(
            StructuredSourcePlan(
                source_id=source_id,
                name=str(source.get("name", source_id)),
                connector=str(source.get("connector", "")),
                stage=str(source.get("stage", "")),
                cost_class=str(source.get("cost_class", "free")),
                network_required=bool(source.get("network_required", True)),
                trust_tier=source.get("trust_tier"),
                firecrawl_replacement=bool(source.get("firecrawl_replacement", False)),
                evidence_roles=list(source.get("evidence_roles", [])),
                hard_metric_scenes={
                    scene: list(indicators or [])
                    for scene, indicators in (source.get("hard_metric_scenes", {}) or {}).items()
                },
            )
        )
    return plans


def build_structured_first_plan(
    *,
    countries: list[str],
    scenes: list[str],
    target_properties: list[Mapping[str, Any]] | None = None,
    include_firecrawl_gap_fill: bool = False,
    free_sources: dict[str, Any] | None = None,
    localized_strategy: dict[str, Any] | None = None,
) -> StructuredFirstPlan:
    normalized_countries = _dedupe(countries)
    normalized_scenes = _dedupe(scenes)
    targets = [_normalize_target(target) for target in target_properties or []]
    source_plans = load_structured_source_plans(free_sources)

    structured_tasks: list[StructuredFirstTask] = []
    firecrawl_tasks: list[StructuredFirstTask] = []
    for source in source_plans:
        use_firecrawl = source.source_id == "firecrawl_gap_fill"
        if use_firecrawl and not include_firecrawl_gap_fill:
            continue
        if use_firecrawl:
            firecrawl_tasks.extend(
                _firecrawl_gap_tasks(
                    source=source,
                    targets=targets,
                    fallback_countries=normalized_countries,
                    fallback_scenes=normalized_scenes,
                    localized_strategy=localized_strategy,
                )
            )
            continue
        for country in normalized_countries:
            for scene_type in normalized_scenes:
                if not source.supports_scene(scene_type):
                    continue
                indicators = source.indicators_for_scene(scene_type)
                structured_tasks.append(
                    StructuredFirstTask(
                        country=country,
                        scene_type=scene_type,
                        source_id=source.source_id,
                        source_name=source.name,
                        connector=source.connector,
                        stage=source.stage,
                        cost_class=source.cost_class,
                        source_role="primary_metric" if indicators else "identity",
                        indicators=indicators,
                        use_firecrawl=False,
                        scrape=False,
                    )
                )

    localized_queries = _localized_queries_for_targets(
        countries=normalized_countries,
        scenes=normalized_scenes,
        targets=targets,
        localized_strategy=localized_strategy,
    )
    naive_requests = _estimate_naive_firecrawl_requests(
        countries=normalized_countries,
        scenes=normalized_scenes,
        targets=targets,
    )
    actual_firecrawl_requests = len(firecrawl_tasks)
    source_counts = Counter(task.source_id for task in structured_tasks + firecrawl_tasks)
    phase_counts = Counter(query.phase for query in localized_queries)
    return StructuredFirstPlan(
        countries=normalized_countries,
        scenes=normalized_scenes,
        property_target_count=len(targets),
        structured_tasks=structured_tasks,
        localized_queries=localized_queries,
        firecrawl_tasks=firecrawl_tasks,
        source_counts=dict(source_counts),
        phase_counts=dict(phase_counts),
        estimated_naive_firecrawl_requests=naive_requests,
        estimated_firecrawl_requests_saved=max(0, naive_requests - actual_firecrawl_requests),
    )


def _localized_queries_for_targets(
    *,
    countries: list[str],
    scenes: list[str],
    targets: list[dict[str, Any]],
    localized_strategy: dict[str, Any] | None,
) -> list[LocalizedSearchQuery]:
    queries: list[LocalizedSearchQuery] = []
    if targets:
        for target in targets:
            queries.extend(
                build_localized_queries(
                    country=target["country"],
                    city=target.get("city"),
                    property_name=target.get("property_name"),
                    scene_type=target["scene_type"],
                    strategy=localized_strategy,
                )
            )
        return queries

    for country in countries:
        for scene_type in scenes:
            queries.extend(
                build_localized_queries(
                    country=country,
                    scene_type=scene_type,
                    phases=["structured_source_discovery"],
                    strategy=localized_strategy,
                )
            )
    return queries


def _firecrawl_gap_tasks(
    *,
    source: StructuredSourcePlan,
    targets: list[dict[str, Any]],
    fallback_countries: list[str],
    fallback_scenes: list[str],
    localized_strategy: dict[str, Any] | None,
) -> list[StructuredFirstTask]:
    tasks: list[StructuredFirstTask] = []
    if targets:
        for target in targets:
            queries = build_localized_queries(
                country=target["country"],
                city=target.get("city"),
                property_name=target.get("property_name"),
                scene_type=target["scene_type"],
                phases=["firecrawl_gap_fill"],
                strategy=localized_strategy,
            )
            for query in queries:
                tasks.append(
                    StructuredFirstTask(
                        country=target["country"],
                        city=target.get("city"),
                        property_name=target.get("property_name"),
                        scene_type=target["scene_type"],
                        source_id=source.source_id,
                        source_name=source.name,
                        connector=source.connector,
                        stage=source.stage,
                        cost_class=source.cost_class,
                        source_role="paid_gap_fill",
                        indicators=query.expected_indicators,
                        use_firecrawl=True,
                        scrape=query.scrape,
                        query=query.query,
                    )
                )
        return tasks

    for country in fallback_countries:
        for scene_type in fallback_scenes:
            for query in build_localized_queries(
                country=country,
                scene_type=scene_type,
                property_name=f"{country} {scene_type}",
                phases=["firecrawl_gap_fill"],
                strategy=localized_strategy,
            ):
                tasks.append(
                    StructuredFirstTask(
                        country=country,
                        scene_type=scene_type,
                        source_id=source.source_id,
                        source_name=source.name,
                        connector=source.connector,
                        stage=source.stage,
                        cost_class=source.cost_class,
                        source_role="paid_gap_fill",
                        indicators=query.expected_indicators,
                        use_firecrawl=True,
                        scrape=query.scrape,
                        query=query.query,
                    )
                )
    return tasks


def _estimate_naive_firecrawl_requests(
    *,
    countries: list[str],
    scenes: list[str],
    targets: list[dict[str, Any]],
) -> int:
    if targets:
        return len(targets) * 3
    return max(1, len(countries) * len(scenes) * 3)


def _normalize_target(target: Mapping[str, Any]) -> dict[str, Any]:
    country = str(target.get("country", "")).strip()
    scene_type = str(target.get("scene_type", "")).strip()
    if not country or not scene_type:
        raise ValueError("target_properties must include country and scene_type")
    return {
        "country": country,
        "scene_type": scene_type,
        "property_name": str(target.get("property_name", "")).strip() or None,
        "city": str(target.get("city", "")).strip() or None,
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result

