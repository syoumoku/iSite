from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from isite2.rules.config_loader import get_scene_rule, load_localized_search_strategy


@dataclass(frozen=True)
class LocalizedSearchQuery:
    phase: str
    country: str
    language: str
    scene_type: str
    query: str
    limit: int
    use_firecrawl: bool
    scrape: bool
    firecrawl_mode: str
    expected_indicators: list[str]
    preferred_domains: list[str]
    official_source_terms: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_FREE_PHASES = [
    "structured_source_discovery",
    "official_catalog_discovery",
    "metric_extraction",
]


def country_language_profile(
    country: str,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    search_strategy = strategy or load_localized_search_strategy()
    default_profile = dict(search_strategy.get("country_profiles", {}).get("default", {}))
    country_profile = search_strategy.get("country_profiles", {}).get(country, {})

    merged = {**default_profile, **country_profile}
    merged["languages"] = list(merged.get("languages") or ["en"])
    merged["official_source_terms"] = list(merged.get("official_source_terms") or [])
    merged["preferred_domains"] = list(merged.get("preferred_domains") or [])
    return merged


def localized_metric_terms(
    *,
    country: str,
    scene_type: str,
    language: str | None = None,
    strategy: dict[str, Any] | None = None,
) -> list[str]:
    search_strategy = strategy or load_localized_search_strategy()
    profile = country_language_profile(country, search_strategy)
    languages = [language] if language else profile["languages"]
    terms: list[str] = []
    seen: set[str] = set()

    for lang in languages + ["en"]:
        language_profile = search_strategy.get("language_profiles", {}).get(lang, {})
        scene_terms = language_profile.get("metric_terms", {}).get(scene_type, [])
        for term in scene_terms:
            normalized = str(term).casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            terms.append(str(term))
        if language and terms:
            break
    return terms


def build_localized_queries(
    *,
    country: str,
    scene_type: str,
    property_name: str | None = None,
    city: str | None = None,
    phases: list[str] | None = None,
    strategy: dict[str, Any] | None = None,
) -> list[LocalizedSearchQuery]:
    search_strategy = strategy or load_localized_search_strategy()
    profile = country_language_profile(country, search_strategy)
    selected_phases = phases or DEFAULT_FREE_PHASES
    defaults = search_strategy.get("defaults", {})
    phase_config = search_strategy.get("phases", {})
    templates_by_phase = search_strategy.get("query_templates", {})
    default_limit = int(defaults.get("search_limit", 5))
    gap_limit = int(defaults.get("firecrawl_gap_limit", 2))
    scrape = bool(defaults.get("scrape", False))
    firecrawl_mode = str(defaults.get("firecrawl_mode", "last_resort"))
    joiner = str(defaults.get("query_joiner", " OR "))
    expected_indicators = list(get_scene_rule(scene_type).get("primary_indicators", []))

    queries: list[LocalizedSearchQuery] = []
    seen: set[tuple[str, str, str]] = set()
    for phase in selected_phases:
        if phase not in phase_config:
            raise KeyError(f"unknown localized query phase: {phase}")
        if phase != "structured_source_discovery" and not property_name:
            continue
        use_firecrawl = bool(phase_config[phase].get("use_firecrawl", False))
        limit = gap_limit if use_firecrawl else default_limit
        for language in profile["languages"]:
            metric_terms = localized_metric_terms(
                country=country,
                scene_type=scene_type,
                language=language,
                strategy=search_strategy,
            )
            if not metric_terms:
                continue
            context = {
                "country": country,
                "city": city or "",
                "property_name": property_name or "",
                "scene_type": scene_type,
                "language": language,
                "metric_terms": joiner.join(metric_terms),
                "official_terms": joiner.join(profile["official_source_terms"]),
                "preferred_domain": " OR ".join(profile["preferred_domains"]),
            }
            for template in templates_by_phase.get(phase, []) or []:
                rendered = " ".join(template.format(**context).split())
                if not rendered:
                    continue
                dedupe_key = (phase, language, rendered.casefold())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                queries.append(
                    LocalizedSearchQuery(
                        phase=phase,
                        country=country,
                        language=language,
                        scene_type=scene_type,
                        query=rendered,
                        limit=limit,
                        use_firecrawl=use_firecrawl,
                        scrape=scrape,
                        firecrawl_mode=firecrawl_mode,
                        expected_indicators=expected_indicators,
                        preferred_domains=list(profile["preferred_domains"]),
                        official_source_terms=list(profile["official_source_terms"]),
                    )
                )
    return queries

