# Kuwait city and primary-metric classification QA incident

## Context

A Kuwait deep scan added 41 candidates and synchronized them into the active database. Final QA compared the stored entities and scene-model outputs against the retained acquisition artifacts.

## Findings

1. The global city normalizer treated UN/LOCODE place records as canonical cities for Kuwait. A dry run exposed geographically misleading assignments, including central Kuwait City properties grouped under Shuwaikh or Ra's al Ard and Zahra properties grouped under Ahmadi.
2. The derived language-model decision labeled all 41 new records as having a hard primary metric. The deterministic scene model correctly retained 18 as low evidence because office height/floor count is not NLA and a convention venue's peak theatre capacity is not exhibition area.

## Containment

- The city-normalization dry run was not applied.
- Kuwait was not published.
- The deterministic scene-model classification remains authoritative.
- All search, scrape, image-attempt, curation, import, and derived artifacts were retained.

## Control mapping

- `QA-ENTITY-002`: canonical cities must use official deterministic mappings and preserve source localities.
- `QA-EVIDENCE-001`: scene primary metrics must remain canonical, quantitative, and source-traceable.
- `QA-PIPELINE-001`: final evidence sync must precede scoped derived refresh.

## Corrective actions

- Add Kuwait coordinate/locality regression fixtures before changing the city-normalization implementation.
- Require derived hard-primary labels to be reconciled against the deterministic canonical metric status before persistence or publication.
- Keep Kuwait publication blocked until a reviewed country-scoped city correction artifact passes.

Detailed run evidence is in `.web_evidence/kuwait_deep_scan_20260914/qa_summary.md`.
