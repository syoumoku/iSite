# Latest candidate tie-break drift

## Incident

The Algeria city-normalization release repeatedly returned 196 latest properties, but
the selected packet IDs and localization gaps changed between identical reads. Bulk
scan candidates shared the same `created_at`, while latest selection sorted only by
that timestamp. Database row order therefore decided which historical scan packet won.

## Impact

- Localization dry-runs produced different provider groups across repeated calls.
- A country-delta gate could validate a different packet set from the one exported or
  preaggregated later in the same release.
- The public gate blocked the attempted release; no remote data was changed.

## Control

`QA-PUBLISH-004` orders latest candidates by candidate time, scan-run start time,
scan-run creation time, scan-run ID, and candidate ID. The repository regression test
forces tied candidate timestamps and requires the later scan run to win.

## Verification

Two consecutive Algeria nine-field dry-runs returned the same property count, provider
group count, affected-property count, and affected-property ID hash before localization
repair continued.
