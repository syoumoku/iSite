from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from isite2.connectors.extraction import extract_indicators
from isite2.connectors.firecrawl import FirecrawlPublicEvidenceProvider
from isite2.connectors.http import HttpPublicEvidenceProvider
from isite2.connectors.models import FetchedPage, GeocodeResult, SearchResult
from isite2.connectors.serpapi import SerpApiSearchProvider
from isite2.growth.discovery_priority import discovery_task_priority
from isite2.growth.evidence_intake import (
    CandidateDraft,
    EvidenceIntakeProvider,
    SeedCatalogEvidenceIntakeProvider,
    load_effective_source_registry,
)
from isite2.growth.evidence_store import DiscoveryTaskLease, EvidenceCurationStore
from isite2.growth.property_identity import (
    KNOWN_PROPERTY,
    NEW_OPPORTUNITY,
    POSSIBLE_DUPLICATE,
    KnownOpportunityIndex,
    known_opportunity_index_from_registry,
)
from isite2.rules.candidate_quality import BLOCKED_QUALITY, evaluate_candidate_quality
from isite2.rules.config_loader import load_discovery_sources


@dataclass
class HybridDiscoveryStats:
    run_id: str | None = None
    seeded_tasks: int = 0
    leased_tasks: int = 0
    searched_count: int = 0
    fetched_count: int = 0
    failed_count: int = 0
    firecrawl_search_count: int = 0
    firecrawl_fetch_count: int = 0
    firecrawl_credits_used: float = 0
    firecrawl_warnings: list[str] = field(default_factory=list)
    known_property_skipped_count: int = 0
    known_url_skipped_count: int = 0
    possible_duplicate_review_count: int = 0
    new_opportunity_count: int = 0
    existing_property_evidence_update_count: int = 0
    firecrawl_requests_saved_estimate: int = 0
    countries: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


class HybridPublicDiscoveryProvider(EvidenceIntakeProvider):
    """Real public discovery provider with seed catalog backfill.

    The provider leases database-backed tasks when a store is supplied, searches
    public adapters, fetches compliant pages, extracts one field-level indicator,
    geocodes the entity, and returns CandidateDraft objects for raw evidence
    persistence by the caller.
    """

    def __init__(
        self,
        *,
        store: EvidenceCurationStore | None = None,
        public_provider: HttpPublicEvidenceProvider | None = None,
        firecrawl_provider: FirecrawlPublicEvidenceProvider | None = None,
        serpapi_provider: SerpApiSearchProvider | None = None,
        seed_provider: EvidenceIntakeProvider | None = None,
        max_searches_per_cycle: int = 20,
        max_fetches_per_cycle: int = 40,
        search_result_limit: int = 5,
        prefilter_map_before_fetch: bool = True,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        recheck_after_seconds: int = 86_400,
        discovery_config: dict[str, Any] | None = None,
        target_countries: list[str] | None = None,
        new_opportunities_only: bool = True,
        force_rescan_existing: bool = False,
        known_index: KnownOpportunityIndex | None = None,
    ) -> None:
        self.store = store
        self.public_provider = public_provider or HttpPublicEvidenceProvider(
            cache=store.source_cache() if store is not None else None
        )
        self.firecrawl_provider = (
            firecrawl_provider
            if firecrawl_provider is not None
            else FirecrawlPublicEvidenceProvider.from_env(
                cache=store.source_cache() if store is not None else None
            )
        )
        self.serpapi_provider = serpapi_provider or SerpApiSearchProvider()
        self.seed_provider = seed_provider or SeedCatalogEvidenceIntakeProvider()
        self.max_searches_per_cycle = max_searches_per_cycle
        self.max_fetches_per_cycle = max_fetches_per_cycle
        self.search_result_limit = search_result_limit
        self.prefilter_map_before_fetch = prefilter_map_before_fetch
        self.worker_id = worker_id or f"discovery-{uuid4()}"
        self.lease_seconds = lease_seconds
        self.recheck_after_seconds = recheck_after_seconds
        self.discovery_config = discovery_config or load_discovery_sources()
        self.target_countries = target_countries
        self.registry = load_effective_source_registry()
        self.new_opportunities_only = new_opportunities_only
        self.force_rescan_existing = force_rescan_existing
        self.known_index = known_index or known_opportunity_index_from_registry(
            self.registry,
            session_factory=store.session_factory if store is not None else None,
        )
        self._bbox_cache: dict[str, dict[str, float]] = {}
        self.last_stats = HybridDiscoveryStats()

    def discover(self, regions: list[str], target_countries: list[str]) -> list[CandidateDraft]:
        stats = HybridDiscoveryStats()
        self.last_stats = stats
        selected_countries = self._target_countries(target_countries)
        leases = self._claim_tasks(regions, selected_countries, stats)
        stats.leased_tasks = len(leases)
        stats.run_id = (
            self.store.start_discovery_run(worker_id=self.worker_id)
            if self.store
            else None
        )

        drafts: list[CandidateDraft] = []
        seen_urls: set[str] = set()
        remaining_searches = self.max_searches_per_cycle
        remaining_fetches = self.max_fetches_per_cycle
        for task in leases:
            stats.countries.add(task.country)
            task_errors: list[str] = []
            for adapter in self._search_adapters():
                if remaining_searches <= 0:
                    break
                remaining_searches -= 1
                try:
                    results = adapter.search(task.query, limit=self.search_result_limit)
                    stats.searched_count += 1
                except Exception as exc:  # pragma: no cover - defensive around live providers
                    message = f"{task.country}/{task.scene_type} search failed: {exc}"
                    task_errors.append(message)
                    stats.errors.append(message)
                    stats.failed_count += 1
                    continue
                for result in results:
                    if remaining_fetches <= 0:
                        break
                    url = str(result.url)
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if self._skip_known_search_result(task, result, stats):
                        continue
                    remaining_fetches -= 1
                    geocode = self._prefetch_geocode_for_result(task, result, stats)
                    if self.prefilter_map_before_fetch and geocode is None:
                        continue
                    fetched_drafts = self._fetch_extract_geocode(
                        task,
                        result,
                        task_errors,
                        stats,
                        geocode=geocode,
                    )
                    drafts.extend(fetched_drafts)
            self._finish_task(task, stats.run_id, task_errors)

        self._sync_firecrawl_stats(stats)
        for seed in self.seed_provider.discover(regions, selected_countries):
            drafts.append(replace(seed, source_type="seed_catalog"))
        return drafts

    def _target_countries(self, target_countries: list[str]) -> list[str]:
        if self.target_countries is None:
            return target_countries
        allowed = {country.casefold() for country in self.target_countries}
        return [country for country in target_countries if country.casefold() in allowed]

    def finalize_run(
        self,
        *,
        discovered_count: int,
        new_count: int,
        changed_count: int,
        duplicate_count: int,
    ) -> None:
        if self.store is None or self.last_stats.run_id is None:
            return
        self.store.finish_discovery_run(
            run_id=self.last_stats.run_id,
            status="completed",
            searched_count=self.last_stats.searched_count,
            fetched_count=self.last_stats.fetched_count,
            discovered_count=discovered_count,
            new_count=new_count,
            changed_count=changed_count,
            duplicate_count=duplicate_count,
            failed_count=self.last_stats.failed_count,
            countries=sorted(self.last_stats.countries),
            errors=self.last_stats.errors,
            known_property_skipped_count=self.last_stats.known_property_skipped_count,
            known_url_skipped_count=self.last_stats.known_url_skipped_count,
            possible_duplicate_review_count=self.last_stats.possible_duplicate_review_count,
            new_opportunity_count=self.last_stats.new_opportunity_count,
            existing_property_evidence_update_count=(
                self.last_stats.existing_property_evidence_update_count
            ),
            firecrawl_requests_saved_estimate=(
                self.last_stats.firecrawl_requests_saved_estimate
            ),
        )

    def _claim_tasks(
        self,
        regions: list[str],
        target_countries: list[str],
        stats: HybridDiscoveryStats,
    ) -> list[DiscoveryTaskLease]:
        if self.store is not None:
            stats.seeded_tasks = self.store.seed_discovery_tasks(
                regions=regions,
                target_countries=target_countries,
                discovery_config=self.discovery_config,
            )
            return self.store.claim_discovery_tasks(
                worker_id=self.worker_id,
                limit=self.max_searches_per_cycle,
                lease_seconds=self.lease_seconds,
                recheck_after_seconds=self.recheck_after_seconds,
            )
        return _inline_tasks(regions, target_countries, self.discovery_config)[
            : self.max_searches_per_cycle
        ]

    def _search_adapters(self) -> list[Any]:
        adapters: list[Any] = []
        if getattr(self.firecrawl_provider, "enabled", False):
            adapters.append(self.firecrawl_provider)
        if getattr(self.serpapi_provider, "enabled", False):
            adapters.append(self.serpapi_provider)
        adapters.append(self.public_provider)
        return adapters

    def _fetch_extract_geocode(
        self,
        task: DiscoveryTaskLease,
        result: SearchResult,
        task_errors: list[str],
        stats: HybridDiscoveryStats,
        *,
        geocode: GeocodeResult | None = None,
    ) -> list[CandidateDraft]:
        if not _usable_search_result(task, result):
            return []
        bbox = self._country_bbox(task.country)
        geocode = geocode or self._safe_geocode(
            f"{_clean_property_title(result.title)}, {task.country}"
        )
        if geocode is None:
            return []
        if not _geocode_matches_country(task.country, bbox, geocode):
            return []
        try:
            page = self._fetch_provider_for_result(result).fetch_page(str(result.url))
        except Exception as exc:  # pragma: no cover - defensive around live providers
            message = f"{task.country}/{task.scene_type} fetch failed {result.url}: {exc}"
            task_errors.append(message)
            stats.errors.append(message)
            stats.failed_count += 1
            return []
        if not page.robots_allowed:
            message = f"{task.country}/{task.scene_type} robots disallowed {result.url}"
            task_errors.append(message)
            stats.errors.append(message)
            stats.failed_count += 1
            return []
        stats.fetched_count += 1
        property_name = _clean_property_title(result.title)
        preferred = self._preferred_indicators(task)
        extractions = extract_indicators(page, property_name, preferred)
        source_date = page.source_date or page.fetched_at.date().isoformat()
        drafts = [
            self._annotate_identity_match(
                CandidateDraft(
                    region=task.region,
                    country=task.country,
                    city=geocode.city or "",
                    property_name=extraction.property_name,
                    scene_type=task.scene_type,
                    annual_visits=_annual_visits_proxy(task.scene_type, extraction.field_value),
                    latitude=geocode.latitude,
                    longitude=geocode.longitude,
                    geocode_precision=geocode.geocode_precision,
                    map_source=geocode.map_source,
                    map_source_date=source_date,
                    field_group=extraction.field_group,
                    indicator_name=extraction.field_group,
                    field_value=extraction.field_value,
                    source_name=page.source_name,
                    source_tier=_source_tier_value(page),
                    source_url=str(page.source_url),
                    source_date=source_date,
                    evidence_type="Direct",
                    bbox=bbox,
                    source_type=_discovered_source_type(task.source_type, page),
                    content_text=page.content_text,
                    discovery_task_id=task.id,
                ),
                stats,
            )
            for extraction in extractions
        ]
        return drafts

    def _skip_known_search_result(
        self,
        task: DiscoveryTaskLease,
        result: SearchResult,
        stats: HybridDiscoveryStats,
    ) -> bool:
        if not self.new_opportunities_only or self.force_rescan_existing:
            return False
        if self.known_index.knows_source_url(str(result.url)):
            stats.known_url_skipped_count += 1
            stats.firecrawl_requests_saved_estimate += 1
            return True
        match = self.known_index.match(
            country=task.country,
            city="",
            property_name=_clean_property_title(result.title),
            scene_type=task.scene_type,
            source_url=None,
        )
        if match.status == KNOWN_PROPERTY:
            stats.known_property_skipped_count += 1
            stats.firecrawl_requests_saved_estimate += 1
            return True
        return False

    def _annotate_identity_match(
        self,
        draft: CandidateDraft,
        stats: HybridDiscoveryStats,
    ) -> CandidateDraft:
        match = self.known_index.match(
            country=draft.country,
            city=draft.city,
            property_name=draft.property_name,
            scene_type=draft.scene_type,
            latitude=draft.latitude,
            longitude=draft.longitude,
            source_url=draft.source_url,
        )
        matched_property_name = (
            match.record.identity.property_name
            if match.record is not None
            else None
        )
        matched_property_id = match.record.property_id if match.record is not None else None
        if match.status == KNOWN_PROPERTY:
            stats.existing_property_evidence_update_count += 1
        elif match.status == POSSIBLE_DUPLICATE:
            stats.possible_duplicate_review_count += 1
        elif match.status == NEW_OPPORTUNITY:
            stats.new_opportunity_count += 1
        return replace(
            draft,
            identity_match_status=match.status,
            matched_property_id=matched_property_id,
            matched_property_name=matched_property_name,
            identity_match_reason=match.reason or None,
        )

    def _prefetch_geocode_for_result(
        self,
        task: DiscoveryTaskLease,
        result: SearchResult,
        stats: HybridDiscoveryStats,
    ) -> GeocodeResult | None:
        if not self.prefilter_map_before_fetch:
            return None
        property_name = _clean_property_title(result.title)
        geocode = self._safe_geocode(f"{property_name}, {task.country}")
        bbox = self._country_bbox(task.country)
        if geocode is None:
            stats.firecrawl_requests_saved_estimate += 1
            return None
        if not _geocode_matches_country(task.country, bbox, geocode):
            stats.firecrawl_requests_saved_estimate += 1
            return None
        quality = evaluate_candidate_quality(
            country=task.country,
            city=geocode.city or "",
            property_name=property_name,
            scene_type=task.scene_type,
            geocode_precision=geocode.geocode_precision,
            source_urls=[str(result.url)],
            source_names=[result.source_name],
        )
        if quality.status == BLOCKED_QUALITY:
            stats.firecrawl_requests_saved_estimate += 1
            return None
        return geocode

    def _preferred_indicators(self, task: DiscoveryTaskLease) -> list[str]:
        if task.source_type == "operator_build_status":
            return [
                "indoor_coverage_deployment",
                "das_deployment",
                "indoor_5g_upgrade",
            ]
        scene_config = self.discovery_config.get("scenes", {}).get(task.scene_type, {})
        return scene_config.get("preferred_indicators", []) or ["unknown"]

    def _fetch_provider_for_result(self, result: SearchResult) -> Any:
        if (
            getattr(self.firecrawl_provider, "enabled", False)
            and str(result.source_name).casefold().startswith("firecrawl")
        ):
            return self.firecrawl_provider
        return self.public_provider

    def _safe_geocode(self, query: str) -> GeocodeResult | None:
        try:
            return self.public_provider.geocode(query)
        except Exception:
            return None

    def _country_bbox(self, country: str) -> dict[str, float]:
        if country in self._bbox_cache:
            return self._bbox_cache[country]
        registry_bbox = (
            self.registry.get("countries", {})
            .get(country, {})
            .get("bbox", {})
        )
        if registry_bbox:
            self._bbox_cache[country] = registry_bbox
            return registry_bbox
        geocode = self._safe_geocode(country)
        if geocode and geocode.boundingbox:
            south, north, west, east = geocode.boundingbox
            bbox = {
                "min_latitude": south,
                "max_latitude": north,
                "min_longitude": west,
                "max_longitude": east,
            }
            self._bbox_cache[country] = bbox
            return bbox
        self._bbox_cache[country] = {}
        return {}

    def _finish_task(
        self,
        task: DiscoveryTaskLease,
        run_id: str | None,
        task_errors: list[str],
    ) -> None:
        if self.store is None or run_id is None:
            return
        failure_class = _failure_class(task_errors)
        self.store.complete_discovery_task(
            task_id=task.id,
            run_id=run_id,
            status="failed" if task_errors else "completed",
            error="; ".join(task_errors[:3]) if task_errors else None,
            failure_class=failure_class,
        )

    def _sync_firecrawl_stats(self, stats: HybridDiscoveryStats) -> None:
        provider_stats = getattr(self.firecrawl_provider, "stats", None)
        if provider_stats is None:
            return
        stats.firecrawl_search_count = int(getattr(provider_stats, "search_requests", 0))
        stats.firecrawl_fetch_count = int(getattr(provider_stats, "scrape_requests", 0))
        stats.firecrawl_credits_used = float(getattr(provider_stats, "credits_used", 0) or 0)
        stats.firecrawl_warnings = list(getattr(provider_stats, "warnings", []) or [])


def _inline_tasks(
    regions: list[str],
    target_countries: list[str],
    discovery_config: dict[str, Any],
) -> list[DiscoveryTaskLease]:
    leases: list[DiscoveryTaskLease] = []
    source_priorities = {
        source_type: int(config.get("priority", 100))
        for source_type, config in discovery_config.get("source_types", {}).items()
    }
    for country in target_countries:
        region = regions[0] if regions else "Unknown"
        for scene_type, scene_config in discovery_config.get("scenes", {}).items():
            scene_label = scene_config.get("scene_label", scene_type)
            for source_type, templates in scene_config.get("query_templates", {}).items():
                for template_index, template in enumerate(templates or []):
                    query = template.format(
                        country=country,
                        scene_type=scene_type,
                        scene_label=scene_label,
                    )
                    leases.append(
                        DiscoveryTaskLease(
                            id=str(uuid4()),
                            region=region,
                            country=country,
                            scene_type=scene_type,
                            source_type=source_type,
                            query_template=template,
                            query=query,
                            priority=discovery_task_priority(
                                scene_config=scene_config,
                                source_priorities=source_priorities,
                                source_type=source_type,
                                template_index=template_index,
                            ),
                        )
                    )
    return sorted(leases, key=lambda task: task.priority)


def _clean_property_title(title: str) -> str:
    title = re.sub(r"\s+-\s+Wikipedia$", "", title, flags=re.IGNORECASE)
    title = re.split(r"\s+[|]\s+|\s+-\s+", title, maxsplit=1)[0]
    return re.sub(r"\s+", " ", title).strip()


def _usable_search_result(task: DiscoveryTaskLease, result: SearchResult) -> bool:
    title = result.title.casefold()
    url = str(result.url).casefold()
    blocked_tokens = (
        "list of ",
        "lists of ",
        "category:",
        "template:",
        "disambiguation",
        "index of",
    )
    if any(token in title for token in blocked_tokens):
        return False
    if any(token in url for token in ("category:", "template:", "disambiguation")):
        return False
    if task.scene_type == "airport_terminal" and "airport" not in title and "airport" not in url:
        return False
    if task.scene_type == "stadium" and not any(
        token in title or token in url for token in ("stadium", "arena")
    ):
        return False
    quality = evaluate_candidate_quality(
        country=task.country,
        city="",
        property_name=_clean_property_title(result.title),
        scene_type=task.scene_type,
        geocode_precision=None,
        source_urls=[str(result.url)],
        source_names=[result.source_name],
    )
    if quality.status == BLOCKED_QUALITY:
        return False
    return True


def _geocode_matches_country(
    country: str,
    bbox: dict[str, float],
    geocode: GeocodeResult,
) -> bool:
    if geocode.country and geocode.country.casefold() != country.casefold():
        return False
    if bbox:
        return (
            float(bbox["min_latitude"])
            <= geocode.latitude
            <= float(bbox["max_latitude"])
            and float(bbox["min_longitude"])
            <= geocode.longitude
            <= float(bbox["max_longitude"])
        )
    return True


def _failure_class(errors: list[str]) -> str | None:
    if not errors:
        return None
    text = " ".join(errors).casefold()
    if "rate limit" in text:
        return "rate_limit"
    if "robots" in text:
        return "robots"
    if any(token in text for token in ("timeout", "connection", "network")):
        return "network"
    if any(token in text for token in ("status", "http", "403", "404", "500", "502", "503")):
        return "http_error"
    return "unknown"


def _source_tier_value(page: FetchedPage) -> str:
    source_tier = page.source_tier
    return getattr(source_tier, "value", str(source_tier))


def _discovered_source_type(task_source_type: str, page: FetchedPage) -> str:
    url = str(page.source_url).casefold()
    source_name = page.source_name.casefold()
    if url.endswith(".pdf") or "filetype:pdf" in url:
        return "annual_report_pdf"
    if "wikipedia.org" in url or "wikidata.org" in url:
        return "secondary_reference"
    if "openstreetmap" in url or "nominatim" in source_name:
        return "map_entity"
    return task_source_type


def _annual_visits_proxy(scene_type: str, field_value: str) -> float:
    number = _numeric_value(field_value)
    if number <= 0:
        return 0
    text = field_value.casefold()
    if "million" in text:
        number *= 1_000_000
    if scene_type in {"airport_terminal", "transport_hub", "cruise_port"}:
        return number * (365 if "daily" in text else 1)
    if scene_type in {"mall_mixed_use", "hospital", "university"}:
        return number
    if scene_type == "stadium":
        return number * 40
    if scene_type == "convention_center":
        return number * 120 if "event" not in text else number * 1_000
    if scene_type == "luxury_hotel_mice":
        return number * 600
    if scene_type == "office_government":
        return number * 4
    return number


def _numeric_value(text: str) -> float:
    match = re.search(r"\d[\d,.]*", text or "")
    if not match:
        return 0
    return float(match.group(0).replace(",", ""))
