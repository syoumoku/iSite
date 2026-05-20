# Multi-Round Discovery Deduplication

## Standard Action

For follow-up scans on the same country, run discovery in `new_opportunities_only`
mode by default. This mode builds a known-opportunity index before search results
are fetched, then prevents repeated spend on already-known properties and URLs.

The index is built from:

- active `properties` rows
- `config/source_registry.yaml`
- `outputs/regional_scan_loop/source_registry_overlay.yaml`
- known evidence/source-cache URLs in the active database

## Identity Rules

Every property is assigned a `property_identity_key`:

```text
normalized_country | normalized_scene_type | normalized_city | normalized_property_name
```

Normalization handles casing, punctuation, accents, simple aliases such as
`intl -> international`, and evidence-page suffixes such as `official annual
passenger traffic report`. City remains part of the identity so similarly named
venues in different cities are not auto-merged.

Exact identity matches are treated as existing properties. High-confidence but
non-exact matches are sent to review; they are not auto-merged.

## Discovery Behavior

Before page fetch/scrape:

- known source URLs are skipped
- exact known property search-result titles are skipped
- repeated tasks are still governed by discovery task cooldown/lease rules

After a page is fetched:

- `new_opportunity` drafts can become new candidates after quality/curation gates
- `known_property` drafts can update existing evidence without creating a new property
- `possible_duplicate` drafts go to review with a concrete merge-or-new action

Use `force_rescan_existing=True` only when the goal is evidence refresh or
second-source strengthening for existing properties.

## Audit Metrics

Discovery and regional-loop summaries include:

- `known_property_skipped_count`
- `known_url_skipped_count`
- `possible_duplicate_review_count`
- `new_opportunity_count`
- `existing_property_evidence_update_count`
- `firecrawl_requests_saved_estimate`

These metrics are the QA trail for Firecrawl/API spend control across repeated
country scans.
