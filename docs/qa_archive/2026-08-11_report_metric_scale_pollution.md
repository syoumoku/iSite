# 2026-08-11 Russia airport metric scale pollution

## Incident

The first production report backfill found internally consistent but implausible
airport metrics in the Russia workbook and PPT. Sheremetyevo and Vnukovo were
rendered as 43,712,000,000 and 16,095,000,000 annual passengers.

## Impact and containment

- The shared Codex OAuth audit failed the 38-country batch.
- Russia was removed and remained unavailable through the public report API.
- The other 37 countries were re-audited and activated only after a passing result.
- No property, evidence, inference, or derived field was changed during publication.

## Cause

The active packet was correct. The report recommendation parser interpreted the
dot in `43.712 million` as a thousands separator and then applied the million
suffix again. This produced a report-only 1,000x scale error even though the
stored `annual_visits_est` remained 49.9 million. Excel and PPT shared the same
bad report recommendation value, so cross-file equality checks alone could not
detect the pollution.

The public Excel publisher also invoked the combined Excel/PPT generator. That
coupled a workbook download to the PPT real-image preflight even though the
workbook contains no image dependency.

## Control

`QA-OUTPUT-003` adds a deterministic airport primary-metric plausibility ceiling
to both full-package and Excel-only report audits. Actual throughput values above
250 million annual passengers block activation even when Excel and PPT agree;
configured annual terminal-capacity metrics are checked separately and are not
misclassified as actual throughput. The report
parser now treats a single decimal separator followed by an explicit magnitude
suffix as a decimal magnitude, so `43.712 million` becomes `43,712,000`.

The same Russia audit found a second report-only selection error for President
Hotel. A Kremlin event page had been misclassified as `12,500 rooms`, while the
same property retained an OSM `rooms=204` fact. Recommendation selection now
rejects hotel `keys/room_count` above the existing single-property cap of 5,000,
falls back to the plausible evidence, and the Excel activation audit enforces the
same ceiling. The published Russia workbook therefore uses 204 rooms.

The false item had already been marked `retracted_quality_guard` in raw evidence,
but a stale copy remained in the overlay and active `evidence_items`. Containment
therefore removed that exact overlay/active item, retained the retracted raw row
for audit, and ran the existing property-scoped derived and localization refresh.
Publication used a one-property delta with an exact rollback, simulated first on
the current production dump. The Russia report was generated from that simulated
post-delta state so the other 114 Russia properties and all 120 other country
report entries remained byte-for-byte/index-entry unchanged.

`QA-OUTPUT-001` now enforces artifact-specific gates. Public country downloads
use `excel-only`: standard sheets, country scope, workbook integrity, recommendation
metrics, data fingerprint and audit status remain blocking; PPT images are not.
The full Excel/PPT package continues to require real property images.

No property, evidence, inference, or derived field is refreshed for this repair.
