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

- Use `firecrawl search`.
- Use `--limit 3` for targeted candidate searches.
- Use `--scrape` so the result contains markdown for extraction.
- Save outputs under `.firecrawl/{country_slug}/{phase}/` or the current run-specific folder.
- Reuse cached outputs before re-searching the same query.

## Evidence Rules

- A surfaced candidate needs a property-level name, coordinate, hero image, and objective scene metric.
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

## Examples

Stadium second-source strengthening:

```bash
firecrawl search "Borg El Arab Stadium capacity StadiumDB" --country EG --limit 3 --scrape --json -o .firecrawl/egypt/second_source/borg-el-arab-stadium.json
```

Mall metric extraction:

```bash
firecrawl search "Mall of Egypt gross leasable area official" --country EG --limit 3 --scrape --json -o .firecrawl/egypt/metric/mall-of-egypt.json
```

Independent build-status search:

```bash
firecrawl search "Mall of Egypt indoor 5G coverage operator announcement" --country EG --limit 3 --scrape --json -o .firecrawl/egypt/build_status/mall-of-egypt.json
```

## Code Hook

Use `isite2.growth.firecrawl_search_strategy.build_firecrawl_queries` for phase-specific query objects, or `build_gap_closure_queries` for Review Queue gap closure. These functions return structured objects with phase, scene, query, expected indicators, preferred channels, fallback channels, limit, and scrape settings.
