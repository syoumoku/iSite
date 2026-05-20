---
name: isite2-qa-auditor
description: >
  Run periodic QA for the iSite2 public-evidence property scanning project. Use
  when asked to audit candidate quality, evidence quality, active/overlay sync,
  scene metric validity, Firecrawl spend/retention, default-value pollution,
  duplicate/polluted candidates, or to summarize project quality reports.
---

# iSite2 QA Auditor

Use this skill for iSite2 quality checks. QA must be evidence-first, scene-aware,
and action-oriented. Do not treat a value-class or recommendation as valid unless
it traces to evidence, source, date, evidence tier, and inference chain.

## Operating Rules

- Discuss the QA scope and Firecrawl budget with the user before execution.
- Prefer existing project scripts and reports; do not add new scripts unless a
  repeated check cannot be covered by existing code or SQL.
- Use free/local checks first. Use Firecrawl only for targeted source
  verification, missing hard evidence, or scene-contamination sampling.
- If Firecrawl is used, write search/scrape outputs under `.firecrawl/` and keep
  credit/status files. Never spend credits without retained artifacts.
- Lead with blocking findings, then low-evidence risks, then changes made, then
  next actions.
- QA results must be concrete: every review action should say exactly what to
  verify or fetch.

## Standard QA Flow

1. **Inventory reports**
   - List `outputs/qa/*`.
   - List latest `outputs/regional_scan_loop/curation_*.md`.
   - List `outputs/regional_scan_loop/*cleanup*`, `*pollution*`, and
     `known_property_evidence_update_*`.
   - List `.firecrawl/**/credit*`, `.firecrawl/**/status*`,
     `.firecrawl/**/search*`, and `.firecrawl/**/directory_manifest.json`.

2. **Snapshot active DB**
   - Latest `scan_runs` row.
   - Active candidate count by scene.
   - Evidence status by scene.
   - Candidate quality status distribution.
   - Review queue count by type/severity.
   - Missing hero images.
   - City defaulted to country.
   - Placeholder/default/test source tokens such as `example.com`, `fixture`,
     `default`, `fallback`, `unknown`.
   - Structured field pollution: long natural-language text in metric names,
     country/city/source/coordinate fields.

3. **Check overlay gate**
   - Compare overlay registry candidate count, ready count, and blocked count.
   - Block active admission when true property image, scene identity, source URL,
     country/city, coordinate, or scene value proof is missing.
   - Overlay candidates that pass the quality gate must be synced to active.
   - Blocked candidates may stay in raw/review but must not appear in main table,
     map, or export.

4. **Check evidence semantics by scene**
   - Airport: annual passenger throughput, passenger throughput, terminal
     capacity, or international passenger share. `gateway_role`, `hub_role`, or
     `terminal_role` are auxiliary only and cannot be the main evidence metric.
   - Stadium: seat count, event days, event intensity, and venue role.
   - Convention: exhibition area, meeting area, annual events, plenary capacity,
     or peak event capacity.
   - Mall/mixed-use: GLA, annual footfall, flagship retail role, anchor brands,
     and urban catchment.
   - Office/government: first prove office/government/HQ identity and political
     or commercial value: government department, headquarters, financial/CBD
     role, Grade A, core tenants, institution importance. Floors, tower height,
     GFA/NLA, and visible scale are ranking signals, not standalone value proof.
   - Luxury hotel/MICE: first prove brand, star/luxury positioning,
     international chain, resort prestige, and MICE capability. Room count/keys
     is a ranking signal; meeting area and ballroom capacity strengthen MICE
     priority.
   - Hospital: beds, outpatient volume, hospital grade, referral/emergency role.
   - University: enrollment, campus population, core facility intensity.
   - Transport hub: daily ridership, interchange volume, line count, hub role.

5. **Check derived/proxy chain**
   - Direct evidence must not be overwritten by proxy.
   - Annual visits and busy-hour fields may be inferred when direct evidence is
     absent, but the inference chain must show formula, source metric, and
     confidence.
   - Non-annual period metrics cannot masquerade as annual values. Quarterly or
     monthly figures must be summed or annualized with trace.
   - Structured fields such as `area_metric_name` must be metric labels, not GPT
     narrative text.

6. **Check Firecrawl efficiency**
   - Confirm search results are retained before scrape.
   - Confirm directory manifests exist for directory-led sweeps.
   - Count known URL/property skips, new evidence, updated evidence, failed
     scrapes, and credits used.
   - Flag any credit spend without retained search/scrape artifacts.

7. **Report**
   - Write a concise markdown report to `outputs/qa/`.
   - Include latest active run id, candidate count, scene table, blocking
     findings, low-evidence counts, source-retention status, and next actions.
   - If fixes are made, rerun the relevant local validation and record the new
     active run or report path.

## Useful Local Commands

```bash
find outputs/qa -maxdepth 1 -type f | sort
find outputs/regional_scan_loop -maxdepth 1 -type f -name 'curation_*.md' | sort | tail
find .firecrawl -maxdepth 2 -type f \( -name '*credit*' -o -name '*status*' -o -name '*search*' -o -name '*manifest*' \) | sort
```

```bash
.venv/bin/python scripts/run_gpt_derived_info_refresh.py --provider rule --force --concurrency 1
.venv/bin/python -m pytest tests/test_candidate_quality.py tests/test_derived_refresh.py
```

Use SQL or small Python snippets for DB snapshots when needed. Keep outputs
summarized; the final QA response should not dump raw tables unless the user
asks.

## Red Flags

- Any `example.com`, fixture, default, fallback, or unknown source in active.
- City equals country or city clearly belongs to a different country.
- Candidate has no real image but appears in active.
- Airport role evidence used as main metric.
- Residential tower admitted as office/government.
- Hotel ranked only by rooms without brand/star/luxury/MICE evidence.
- GPT prose stored in structured fields.
- Firecrawl credits spent without saved search/scrape artifacts.
- Review queue says "research later" instead of a concrete verification action.
