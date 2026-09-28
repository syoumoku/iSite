# Firecrawl Search Strategy

This document records the search strategy validated by the Egypt scan. The machine-readable source of truth is `config/firecrawl_search_strategy.yaml`; `config/discovery_sources.yaml` contains the runtime discovery templates used by hybrid discovery.

## Operating Flow

1. Run scene-level discovery with country templates.
2. Normalize candidate name, city, scene, coordinate, hero image, and objective metric.
3. Run candidate-level metric extraction for weak or high-value candidates.
4. Run second-source strengthening when the candidate has only one Tier 3 source, proxy-only evidence, or conflicting metrics.
5. Run build-status searches separately for indoor DAS, indoor coverage, and 5G upgrade evidence.
6. Keep unresolved conflicts in Review Queue with concrete next actions.

## Firecrawl Defaults

- Use `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search ...`; do not run bare `firecrawl ...` from automation. The wrapper defaults to local Docker Firecrawl and records cloud credits as zero. Cloud mode requires an explicit `--deployment cloud`.
- Use `--limit 3` for targeted candidate searches.
- Use `--scrape` so the result contains markdown for extraction.
- Save outputs under `.firecrawl/{country_slug}/{phase}/` or the current run-specific folder.
- Reuse cached outputs before re-searching the same query.
- Before live search, save `.firecrawl/{country_slug_or_run}/localization_profile.json` with official language(s), selected query language priority, primary local search engine/channel, profile source/date, local TLDs, and localized scene-metric terms.
- Search in the official/local language first. English or international-directory queries are fallback or cross-checks unless the country/official source commonly publishes the target metric in English.
- Every search manifest row must include `language` and `search_channel`; if Firecrawl cannot directly select the local search engine, record the channel as `firecrawl_search_with_localized_query` or the web/browser engine used to seed the URL.

## Evidence Rules

- A surfaced candidate needs a property-level name, coordinate, and objective scene metric. Hero image is an independent enrichment field and may remain empty when no authentic usable image is available.
- Evidence gate passes with at least one Tier 1/2 source or two independent Tier 3 sources.
- Official identity pages strengthen entity confidence but do not prove numeric metrics unless the metric is explicit.
- Total development area is `mixed_use_area + Proxy`, not retail `gla`, unless GLA is explicitly stated.
- Property value evidence never proves indoor build status.

## Query Phases

- `discovery`: broad candidate pool by country and scene.
- `metric_extraction`: candidate-specific metric search.
- `second_source_strengthening`: candidate-specific second source for Tier 3 or proxy gaps.
- `media_map_enrichment`: public image and map/entity enrichment.
- `build_status_search`: operator/venue/telecom announcements for DAS, indoor coverage, and 5G.

## Deep Candidate Expansion

When ordinary country-level discovery is saturated but a scene still needs more candidates, reuse the two-stage escalation in `config/firecrawl_search_strategy.yaml` instead of inventing a country-specific script.

### Stage 1: Expand the source surface

1. Reuse retained search manifests and directory pages first.
2. Expand from government/regulator and official operator sources to industry associations, vertical directories, developer/architect/engineer/contractor project pages, event or tourism sources, and only then international aggregators.
3. Run local-language and local-domain variants before English fallback. Record `language`, `search_channel`, domain class, `adopted`, and `skipped_reason`.
4. A broader website set only increases discovery coverage. It never lowers the property-identity, coordinate, operating-status, or objective-metric gates.

### Stage 2: Search city by city

1. Build a deduplicated city list from the capital/primary metro, major economic and population centers, regional capitals, airport/port/tourism/event nodes, and cities with low active scene coverage.
2. Build a `city × scene × localized primary metric × source hint` matrix only for scenes still below target.
3. Load `KnownOpportunityIndex`, known URLs, city aliases, and the country bbox before issuing requests. Exact known properties and exact queries inside their cooldown window are skipped.
4. Search first and retain every manifest. Scrape only shortlisted property/detail pages after identity and metric-likelihood prefiltering.
5. Each local Firecrawl agent chain may run at most one subprocess at a time. More city rows are queued, not fanned out as unbounded processes.

Completion is measured against the active repository after duplicate and quality gates, not against search-result or importer parsed counts. If the target cannot be met without terminal subdivisions, non-operational/planned properties, generic city/port aliases, invalid coordinates, or missing quantitative metrics, stop and report the shortfall plus executable evidence actions.

## Examples

Stadium second-source strengthening:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "استاد برج العرب السعة عدد المقاعد" --limit 3 --scrape --json -o .firecrawl/egypt/second_source/borg-el-arab-stadium-ar.json
```

Mall metric extraction:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "مول مصر المساحة التأجيرية الرسمية" --limit 3 --scrape --json -o .firecrawl/egypt/metric/mall-of-egypt-ar.json
```

Independent build-status search:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "مول مصر تغطية داخلية 5G مشغل اتصالات" --limit 3 --scrape --json -o .firecrawl/egypt/build_status/mall-of-egypt-ar.json
```

## Code Hook

Use `isite2.growth.firecrawl_search_strategy.build_firecrawl_queries` for phase-specific query objects, or `build_gap_closure_queries` for Review Queue gap closure. These functions return structured objects with phase, scene, query, expected indicators, preferred channels, fallback channels, limit, and scrape settings.
