# 2026-08-14 Public Report Activation Merge Failure

## Incident

During the `isite.cloud` Germany network + footfall launch, the full public snapshot `public_20260814T075836Z` restored the database and restarted `isite2-web-1` successfully, but the final public report activation step failed with:

`FileNotFoundError: /opt/isite2/public_reports/releases/<content_hash>/isite_thailand_standard_report_en_public_20260814T075836Z.xlsx`

The remote manifest already showed `restore_result=completed`, and live UI/API verification passed. The failure was isolated to report index activation.

## Root Cause

`public_report_activation_script` moved each incoming `public_reports/releases/<content_hash>` directory only when the target hash directory did not already exist. When a country's content hash was unchanged from a prior release, the existing target directory remained, but new timestamped Excel files in the incoming directory were skipped. The checksum validation then looked for the new timestamped file inside the old target directory and failed.

## Fix

Changed `src/isite2/public_reports.py` so activation merges incoming release directories into existing content-hash directories with `shutil.copy2`, then validates checksums and atomically replaces `index.json`.

Regression coverage:

- `tests/test_public_reports.py`
- `tests/test_public_snapshot_publish.py`

## QA Mapping

- `QA-REPORT-001`: public reports activate atomically with audited data fingerprints and hashes.
- `QA-OPS-001`: public deployment passes service, disk, memory, and container health gates.

## Verification

- Reran report activation for `/opt/isite2/public_reports/incoming/public_20260814T075836Z`: success.
- Remote `public_reports/index.json` exists and includes 123 reports.
- `Thailand` report entry exists after activation.
- `https://isite.cloud/ui/` returned HTTP 200.
- `https://isite.cloud/health` returned `{"status":"ok"}`.
- Germany network and footfall overlay APIs returned `ready`.
