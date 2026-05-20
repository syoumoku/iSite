---
name: isite2-scan
description: Use for iSite2 high-value building scanning product work: multi-agent workflow, public evidence collection, scene modeling, indoor DAS opportunity assessment, Excel/PPT/map outputs, database/API/backend/frontend implementation.
---

# iSite2 Scan Skill for Codex

## When to use

Use this skill whenever the task involves iSite2, high-value building scanning, public evidence collection, scene modeling, indoor coverage/DAS opportunity identification, map UI, Excel/PPT output, or product/backend/frontend architecture for this workflow.

## Core workflow

Always preserve this pipeline:

1. Scope Agent: define scan boundary, full-scan flag, outputs, filters.
2. Discovery Agent: build full candidate pool by scene.
3. Entity Agent: normalize property name, city/country, lat/lon, map precision.
4. Evidence Agent: collect field-level facts, source tier, source date, cross-check status.
5. Scene Modeling Agent: select scene model, primary indicators, proxy basis, proxy level.
6. Build Status Agent: independently judge indoor system presence, type, RAT, evidence status.
7. Demand Agent: calculate daily visits, busy-hour users, busy-hour traffic, busy-hour bandwidth.
8. Inference Agent: conservatively fill missing fields and write inference records.
9. Conclusion Agent: output Evidence Status, Value Class, Action Class, Recommended Solution.
10. Output Agent: produce Excel, PPT card, Markdown, map feature outputs.
11. QA Agent: validate field completeness, evidence conflicts, inference misuse, output shape.

## Hard rules

- Full scan means candidate pool has no upper limit. Top N is display only.
- Evidence before judgment. No evidence, no strong conclusion.
- Scene model before conclusion. Do not use one cross-scene score as the main decision.
- Do not infer build status from property value. Build status is a separate evidence chain.
- Inference must be explicit and traceable.
- Main table should contain only property-specific results and short recommendation reasons.
- Review Queue must contain concrete next actions.

## Must read references

- `references/product_architecture.md`: product architecture summary.
- `references/rules/data_contract.md`: required field families and structured handoffs.
- `references/rules/evidence.md`: source tiers, field evidence, cross-check and freshness rules.
- `references/rules/scene_modeling.md`: scene-specific primary indicators and proxy policy.
- `references/rules/build_status.md`: independent indoor system evidence chain.
- `references/rules/demand.md`: demand formulas and parameter boundaries.
- `references/rules/conclusion.md`: evidence status, value class, action class, and solution policy.
- `references/rules/output.md`: Excel/PPT/map output shape and short-main-table policy.
- `references/rules/qa.md`: validation checklist and review queue requirements.
- `references/rules/rag_crawler.md`: Firecrawl, source cache, RAG, citations, and compliance.
- `references/iSite_skill_v3_2_5_3.md`: archived full uploaded skill; read only when a modular rule file is insufficient.
- `../../../AGENTS.md`: repository-level coding constraints.
- `../../../config/*.yaml`: executable rules and templates.
- `../../../schemas/*.schema.json`: data contracts.

## Progressive disclosure

Start with this file plus the one or two rule files relevant to the task. Do not load the archived full skill unless the task needs a detail missing from the modular rule library.
