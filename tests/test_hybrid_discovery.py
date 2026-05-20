from __future__ import annotations

import httpx
import yaml

from isite2.connectors.firecrawl import (
    FakeFirecrawlPublicEvidenceProvider,
    FirecrawlPublicEvidenceProvider,
)
from isite2.connectors.models import FetchedPage, GeocodeResult, SearchResult
from isite2.domain.enums import SourceTier
from isite2.growth.evidence_curation import run_pending_evidence_curation
from isite2.growth.evidence_discovery import discover_public_evidence
from isite2.growth.evidence_intake import CandidateDraft, EvidenceIntakeProvider
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.hybrid_discovery import HybridPublicDiscoveryProvider
from isite2.growth.property_identity import (
    KnownOpportunityIndex,
    KnownOpportunityRecord,
    build_property_identity,
)


class EmptySeedProvider(EvidenceIntakeProvider):
    def discover(self, regions: list[str], target_countries: list[str]) -> list[CandidateDraft]:
        return []


class FakeSerpApiProvider:
    enabled = True

    def __init__(
        self,
        url: str = "https://airport.example.dz/report",
        title: str = "Algiers International Airport official report",
    ) -> None:
        self.url = url
        self.title = title

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        return [
            SearchResult(
                title=self.title,
                url=self.url,
                source_name="SerpAPI",
                snippet="Official passenger traffic.",
            )
        ]


class NoSearchProvider:
    enabled = False

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        return []


class FakePublicProvider:
    def __init__(
        self,
        *,
        content_text: str = "The airport handled 8,000,000 passengers in 2024.",
        robots_allowed: bool = True,
        source_date: str = "2025-01-01",
    ) -> None:
        self.content_text = content_text
        self.robots_allowed = robots_allowed
        self.source_date = source_date
        self.fetch_count = 0

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        return []

    def fetch_page(self, url: str) -> FetchedPage:
        self.fetch_count += 1
        return FetchedPage(
            source_url=url,
            source_name="Algeria Airport Authority",
            source_tier=SourceTier.TIER_1,
            source_date=self.source_date,
            content_text=self.content_text,
            robots_allowed=self.robots_allowed,
        )

    def geocode(self, query: str) -> GeocodeResult:
        if query == "Algeria":
            return GeocodeResult(
                latitude=28.0,
                longitude=2.6,
                geocode_precision="country bbox",
                map_source="Fake geocoder",
                country="Algeria",
                boundingbox=[18.9, 37.1, -8.7, 12.0],
            )
        return GeocodeResult(
            latitude=36.69,
            longitude=3.21,
            geocode_precision="venue centroid",
            map_source="Fake geocoder",
            city="Algiers",
            country="Algeria",
        )


class WrongCountryGeocodeProvider(FakePublicProvider):
    def geocode(self, query: str) -> GeocodeResult:
        if query == "Algeria":
            return super().geocode(query)
        return GeocodeResult(
            latitude=51.47,
            longitude=-0.45,
            geocode_precision="airport",
            map_source="Fake geocoder",
            city="London",
            country="United Kingdom",
        )


DISCOVERY_CONFIG = {
    "source_types": {"official_government": {"priority": 1}},
    "scenes": {
        "airport_terminal": {
            "scene_label": "airport terminal",
            "preferred_indicators": ["annual_passenger_throughput"],
            "query_templates": {
                "official_government": [
                    "{country} airport authority passenger traffic annual report"
                ]
            },
        }
    },
}

FIRECRAWL_DISCOVERY_CONFIG = {
    "source_types": {"firecrawl_search": {"priority": 1}},
    "scenes": {
        "airport_terminal": {
            "scene_label": "airport terminal",
            "preferred_indicators": ["annual_passenger_throughput"],
            "query_templates": {
                "firecrawl_search": [
                    "{country} airport passenger traffic official annual report"
                ]
            },
        }
    },
}

MULTI_INDICATOR_DISCOVERY_CONFIG = {
    "source_types": {"official_government": {"priority": 1}},
    "scenes": {
        "airport_terminal": {
            "scene_label": "airport terminal",
            "preferred_indicators": [
                "annual_passenger_throughput",
                "passenger_throughput",
            ],
            "query_templates": {
                "official_government": [
                    "{country} airport authority passenger traffic annual report"
                ]
            },
        }
    },
}


def test_serpapi_official_result_writes_new_raw_evidence(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    provider = _provider(store)

    result = discover_public_evidence(
        regions=["Africa"],
        store=store,
        provider=provider,
        output_dir=tmp_path / "loop",
    )
    rows = store.list_raw_evidence(
        country="Algeria",
        scene_type="airport_terminal",
        source_type="official_government",
    )

    assert result.searched_count == 2
    assert result.fetched_count == 1
    assert result.new_count == 1
    assert rows[0]["status"] == "new"
    assert rows[0]["field_value"] == "8,000,000 passengers"
    assert rows[0]["source_type"] == "official_government"
    assert result.report_path and result.report_path.exists()


def test_city_page_search_result_is_not_discovered(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    provider = _provider(
        store,
        serpapi_provider=FakeSerpApiProvider(
            url="https://en.wikipedia.org/wiki/Beijing",
            title="Beijing",
        ),
        discovery_config={
            "source_types": {"secondary_reference": {"priority": 1}},
            "scenes": {
                "transport_hub": {
                    "scene_label": "transport hub",
                    "preferred_indicators": ["line_count"],
                    "query_templates": {
                        "secondary_reference": ["{country} transport hub passenger lines"],
                    },
                }
            },
        },
    )

    result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)

    assert result.discovered_count == 0
    assert result.fetched_count == 0


def test_known_property_search_result_is_skipped_before_fetch(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    public_provider = FakePublicProvider()
    known_index = KnownOpportunityIndex(
        [
            KnownOpportunityRecord(
                identity=build_property_identity(
                    country="Algeria",
                    city="Algiers",
                    property_name="Algiers International Airport",
                    scene_type="airport_terminal",
                ),
                source="test",
            )
        ]
    )
    provider = _provider(
        store,
        public_provider=public_provider,
        serpapi_provider=FakeSerpApiProvider(
            url="https://airport.example.dz/new-report",
            title="Algiers Intl Airport Official Annual Passenger Traffic Report",
        ),
        known_index=known_index,
    )

    result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)

    assert result.discovered_count == 0
    assert result.fetched_count == 0
    assert public_provider.fetch_count == 0
    assert result.known_property_skipped_count == 1
    assert result.firecrawl_requests_saved_estimate == 1


def test_wrong_country_map_prefilter_skips_before_fetch(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    public_provider = WrongCountryGeocodeProvider()
    provider = _provider(
        store,
        public_provider=public_provider,
        serpapi_provider=FakeSerpApiProvider(
            url="https://en.wikipedia.org/wiki/Heathrow_Airport",
            title="Heathrow Airport",
        ),
    )

    result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)

    assert result.discovered_count == 0
    assert result.fetched_count == 0
    assert public_provider.fetch_count == 0
    assert result.firecrawl_requests_saved_estimate == 1


def test_duplicate_url_unchanged_hash_is_suppressed(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")

    first = discover_public_evidence(regions=["Africa"], store=store, provider=_provider(store))
    second = discover_public_evidence(regions=["Africa"], store=store, provider=_provider(store))

    assert first.new_count == 1
    assert second.new_count == 0
    assert second.duplicate_unchanged_count == 1
    assert store.raw_status_counts() == {"new": 1}


def test_changed_content_hash_creates_changed_raw_evidence(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")

    discover_public_evidence(regions=["Africa"], store=store, provider=_provider(store))
    changed = discover_public_evidence(
        regions=["Africa"],
        store=store,
        provider=_provider(
            store,
            public_provider=FakePublicProvider(
                content_text="The airport handled 9,000,000 passengers in 2025."
            ),
        ),
    )

    assert changed.changed_count == 1
    assert store.raw_status_counts() == {"new": 2}


def test_robots_disallow_records_failure_without_raw_evidence(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    provider = _provider(store, public_provider=FakePublicProvider(robots_allowed=False))

    result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)

    assert result.failed_count == 1
    assert result.discovered_count == 0
    assert store.list_raw_evidence() == []
    latest_run = store.discovery_status()["latest_run"]
    assert latest_run["failed_count"] == 1


def test_discovery_task_leasing_prevents_duplicate_claims(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    store.seed_discovery_tasks(
        regions=["Africa"],
        target_countries=["Algeria"],
        discovery_config=DISCOVERY_CONFIG,
    )

    first = store.claim_discovery_tasks(worker_id="worker-a", limit=1)
    second = store.claim_discovery_tasks(worker_id="worker-b", limit=1)

    assert len(first) == 1
    assert second == []
    assert store.discovery_status()["task_backlog"] == {"leased": 1}


def test_discovery_task_priority_prefers_landmark_scenes_before_hospital_university(
    tmp_path,
) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    discovery_config = {
        "source_types": {"official_government": {"priority": 10}},
        "scenes": {
            "hospital": {
                "scene_label": "hospital",
                "scan_priority": 80,
                "query_templates": {
                    "official_government": ["{country} hospital official beds"],
                },
            },
            "transport_hub": {
                "scene_label": "transport hub",
                "scan_priority": 5,
                "query_templates": {
                    "official_government": ["{country} central station ridership"],
                },
            },
            "university": {
                "scene_label": "university campus",
                "scan_priority": 90,
                "query_templates": {
                    "official_government": ["{country} university enrollment"],
                },
            },
            "office_government": {
                "scene_label": "office tower government",
                "scan_priority": 40,
                "query_templates": {
                    "official_government": ["{country} office tower floor area"],
                },
            },
        },
    }

    store.seed_discovery_tasks(
        regions=["Africa"],
        target_countries=["Algeria"],
        discovery_config=discovery_config,
    )
    claimed = store.claim_discovery_tasks(worker_id="worker-a", limit=4)

    assert [task.scene_type for task in claimed] == [
        "transport_hub",
        "office_government",
        "hospital",
        "university",
    ]


def test_clean_cycles_without_new_candidates_mark_progress_exhausted(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")

    _complete_one_clean_cycle(store)
    first = store.settle_completed_progress_groups()
    store.seed_discovery_tasks(
        regions=["Africa"],
        target_countries=["Algeria"],
        discovery_config=DISCOVERY_CONFIG,
    )
    _complete_one_clean_cycle(store)
    second = store.settle_completed_progress_groups()

    progress = store.discovery_status()["progress"][0]
    assert first.settled_groups[0]["no_new_cycles"] == 1
    assert progress["status"] == "exhausted"
    assert progress["no_new_cycles"] == 2
    assert second.exhausted_groups


def test_rate_limit_failure_does_not_increment_no_new_cycle(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    store.seed_discovery_tasks(
        regions=["Africa"],
        target_countries=["Algeria"],
        discovery_config=DISCOVERY_CONFIG,
    )
    lease = store.claim_discovery_tasks(worker_id="worker-a", limit=1)[0]
    run_id = store.start_discovery_run(worker_id="worker-a")
    store.complete_discovery_task(
        task_id=lease.id,
        run_id=run_id,
        status="failed",
        error="rate limit exceeded for public evidence provider",
        failure_class="rate_limit",
    )

    settlement = store.settle_completed_progress_groups()

    progress = store.discovery_status()["progress"][0]
    assert settlement.settled_groups == []
    assert settlement.blocked_groups
    assert progress["status"] == "blocked_review"
    assert progress["no_new_cycles"] == 0


def test_accepted_candidate_resets_no_new_cycle_and_seeds_expansion_tasks(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    _complete_one_clean_cycle(store)
    store.settle_completed_progress_groups()
    _complete_one_clean_cycle(store)
    store.seed_expansion_tasks_from_candidates([_accepted_draft()], DISCOVERY_CONFIG)

    settlement = store.settle_completed_progress_groups(
        {
            ("africa", "algeria", "airport_terminal", "official_government"): {
                "accepted_new_count": 1,
                "draft_review_count": 0,
            }
        }
    )

    progress = [
        item
        for item in store.discovery_status()["progress"]
        if item["source_type"] == "official_government"
    ][0]
    assert settlement.settled_groups[0]["accepted_new_total"] == 1
    assert progress["no_new_cycles"] == 0
    assert progress["cycle_number"] == 3
    assert store.discovery_status()["task_backlog"]["queued"] >= 2


def test_list_page_lead_is_filtered_and_never_becomes_raw_evidence(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    provider = _provider(
        store,
        serpapi_provider=FakeSerpApiProvider(title="List of busiest airports by traffic"),
    )

    result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)

    assert result.discovered_count == 0
    assert store.list_raw_evidence() == []
    store.settle_completed_progress_groups()
    assert store.discovery_status()["progress"][0]["no_new_cycles"] == 1


def test_firecrawl_search_result_flows_through_raw_evidence_store(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    provider = _provider(
        store,
        serpapi_provider=NoSearchProvider(),
        firecrawl_provider=FakeFirecrawlPublicEvidenceProvider(),
        discovery_config=FIRECRAWL_DISCOVERY_CONFIG,
    )

    result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)
    rows = store.list_raw_evidence(source_type="firecrawl_search")

    assert result.searched_count == 2
    assert result.fetched_count == 1
    assert result.new_count == 1
    assert rows[0]["field_value"] == "8,000,000 passengers"
    assert rows[0]["source_type"] == "firecrawl_search"


def test_firecrawl_retryable_failure_records_error_without_raw_evidence(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    firecrawl_provider = FirecrawlPublicEvidenceProvider(
        api_key="fc-test",
        enabled=True,
        client=FailingFirecrawlClient(status_code=500, response_count=2),
        max_attempts=2,
        sleep_func=lambda _: None,
    )
    provider = _provider(
        store,
        serpapi_provider=NoSearchProvider(),
        firecrawl_provider=firecrawl_provider,
        discovery_config=FIRECRAWL_DISCOVERY_CONFIG,
        max_searches_per_cycle=1,
    )

    result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)

    assert result.failed_count == 1
    assert result.discovered_count == 0
    assert result.firecrawl_search_count == 0
    assert "500" in result.errors[0]
    assert store.list_raw_evidence() == []


def test_multi_indicator_extraction_merges_evidence_for_same_candidate(tmp_path) -> None:
    store = EvidenceCurationStore(database_url=f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    provider = _provider(
        store,
        public_provider=FakePublicProvider(
            content_text="The airport handled 8,000,000 passengers in 2024."
        ),
        discovery_config=MULTI_INDICATOR_DISCOVERY_CONFIG,
    )

    discovery_result = discover_public_evidence(regions=["Africa"], store=store, provider=provider)
    curation_result = run_pending_evidence_curation(
        store=store,
        output_dir=tmp_path / "loop",
        overlay_path=tmp_path / "overlay.yaml",
        draft_path=tmp_path / "drafts.json",
    )

    overlay = yaml.safe_load((tmp_path / "overlay.yaml").read_text(encoding="utf-8"))
    candidate = overlay["countries"]["Algeria"]["candidates"][0]
    field_groups = {item["field_group"] for item in candidate["evidence"]}

    assert discovery_result.new_count == 2
    assert curation_result is not None
    assert curation_result.accepted_count == 1
    assert curation_result.updated_count == 1
    assert field_groups == {"annual_passenger_throughput", "passenger_throughput"}


def _provider(
    store: EvidenceCurationStore,
    *,
    public_provider: FakePublicProvider | None = None,
    firecrawl_provider: (
        FakeFirecrawlPublicEvidenceProvider | FirecrawlPublicEvidenceProvider | None
    ) = None,
    serpapi_provider: FakeSerpApiProvider | NoSearchProvider | None = None,
    discovery_config: dict | None = None,
    max_searches_per_cycle: int = 2,
    known_index: KnownOpportunityIndex | None = None,
) -> HybridPublicDiscoveryProvider:
    return HybridPublicDiscoveryProvider(
        store=store,
        public_provider=public_provider or FakePublicProvider(),
        firecrawl_provider=firecrawl_provider,
        serpapi_provider=serpapi_provider or FakeSerpApiProvider(),
        seed_provider=EmptySeedProvider(),
        max_searches_per_cycle=max_searches_per_cycle,
        max_fetches_per_cycle=1,
        recheck_after_seconds=0,
        discovery_config=discovery_config or DISCOVERY_CONFIG,
        target_countries=["Algeria"],
        known_index=known_index,
    )


class FailingFirecrawlClient:
    def __init__(self, *, status_code: int, response_count: int) -> None:
        self.status_code = status_code
        self.responses_left = response_count

    def post(self, url: str, json: dict, headers: dict) -> httpx.Response:
        self.responses_left -= 1
        return httpx.Response(
            status_code=self.status_code,
            json={"error": "temporary provider failure"},
            request=httpx.Request("POST", url),
        )


def _complete_one_clean_cycle(store: EvidenceCurationStore) -> None:
    store.seed_discovery_tasks(
        regions=["Africa"],
        target_countries=["Algeria"],
        discovery_config=DISCOVERY_CONFIG,
    )
    lease = store.claim_discovery_tasks(worker_id="worker-a", limit=1)[0]
    run_id = store.start_discovery_run(worker_id="worker-a")
    store.complete_discovery_task(task_id=lease.id, run_id=run_id, status="completed")


def _accepted_draft() -> CandidateDraft:
    return CandidateDraft(
        region="Africa",
        country="Algeria",
        city="Algiers",
        property_name="Algiers International Airport",
        scene_type="airport_terminal",
        annual_visits=8_000_000,
        latitude=36.69,
        longitude=3.21,
        geocode_precision="venue centroid",
        map_source="Fake geocoder",
        map_source_date="2026-05-09",
        field_group="annual_passenger_throughput",
        indicator_name="annual_passenger_throughput",
        field_value="8,000,000 passengers",
        source_name="Algeria Airport Authority",
        source_tier="Tier 1",
        source_url="https://airport.example.dz/report",
        source_date="2026-05-09",
        source_type="official_government",
        bbox={
            "min_latitude": 18.9,
            "max_latitude": 37.1,
            "min_longitude": -8.7,
            "max_longitude": 12.0,
        },
    )
