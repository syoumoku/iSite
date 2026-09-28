# City scope candidate count

## Symptom

The country-level city selector showed `0` in the right-hand count even when the
same row reported non-zero candidates.

## Root cause

The row subtitle used `candidate_count`, while the emphasized count used
`map_point_count`. A city with candidates that were not map-ready therefore
looked empty.

## Control

- `QA-UI-003` requires the city scope count to use `candidate_count`.
- The Playwright globe smoke test asserts the emphasized city count directly.
