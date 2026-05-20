# iSite2 Product Architecture Summary

## Product objective

Build a self-growing AI product that continuously scans public evidence for high-value buildings and indoor coverage opportunities, persists findings into a database, and produces Excel/PPT/map outputs.

## Product loop

1. Scheduled scope generation: countries, regions, cities, priority scenes.
2. Candidate discovery: build and refresh candidate pools.
3. Entity resolution: normalize building identity and coordinates.
4. Evidence collection: source-tiered field facts with dates and links.
5. Scene modeling: scene-specific primary indicators and proxy rules.
6. Build-status detection: independent indoor system evidence chain.
7. Demand estimation: formulas and scenario parameters.
8. Inference: conservative missing-field inference with trace records.
9. Conclusion: evidence status, value class, action class, recommended solution.
10. Output: Excel, PPT cards, country/city summary, map features.
11. QA and growth: review queue, human feedback, rule tuning, rescan scheduling.

## Architecture layers

- UI: world map, filters, property drawer, evidence panel, insight-card preview.
- API: scan run, property search, map features, evidence, review queue, output generation.
- Orchestrator: stateful pipeline and agent dispatch.
- Agent adapters: LLM, search, map/geocode, crawler, output generator.
- Rules engine: scene models, gates, evidence status, inference policy, demand formulas.
- Storage: PostgreSQL/PostGIS for structured data; object storage for artifacts and cached pages.
