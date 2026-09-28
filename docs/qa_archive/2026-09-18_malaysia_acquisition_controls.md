# Malaysia Acquisition QA, 2026-09-18

## Controls

Mapped to existing QA-COMPLIANCE-001 (manual source-access audit),
QA-FIRE-001 (local wrapper and retained results), QA-EVIDENCE-001
(canonical primary metrics), and QA-PIPELINE-001 (scoped downstream refresh).
No new control or application-code exception is introduced.

## Acquisition Incident

The office research branch checked the IGB Commercial REIT robots policy
after retrieving a PDF under its restricted `/pdf/` path. This was a
preflight failure, not an acceptable acquisition sequence. The branch also
briefly overlapped two local Firecrawl wrapper subprocesses.

The affected PDF and extraction artifacts are quarantined and excluded from
candidate evidence. Further requests to the restricted path stopped. The
branch returned to sequential wrapper execution. No login, CAPTCHA, or
paywall bypass was attempted. Cloud Firecrawl usage was zero.

Artifact: `.web_evidence/malaysia_deep_scan_20260918/offices/compliance_incident.json`.
The audit must retain this incident; do not label the entire acquisition run
incident-free. Future acquisition must finish robots preflight before body
retrieval and await each wrapper subprocess before starting another.

## Intake Contract

Thirty-two area evidence rows initially used the generic field group `area`.
The existing cleaner correctly required further resolution. Source-backed
rows were resubmitted with their canonical indicator as field group (`gla`,
`office_nla`, `meeting_area`, or `exhibition_area`). No numeric values or
quality gates were weakened. Original rejected raw evidence remains retained.

An Ipoh Parade note described rejecting outside estimates, which triggered
the existing conservative blocked-token check. The note was narrowed to the
actual operator NLA, exact unit conversion, and undated-source limitation.
The source value did not change. This is an intake wording correction, not
authorization to suppress uncertainty in evidence.

Artifacts: `batch1/qualified_input.json`, `batch1/canonical_metric_input.json`,
and `batch1/curation/` under the run directory above.

## Release Gate

Before publication, verify that no quarantined URL occurs in admitted
evidence, preserve partial-hall and historical-metric scope, and run scoped
derived, traffic, localization, public aggregation, and country report QA.
Only Malaysia delta may be published; unrelated properties must retain
their baseline hashes.
