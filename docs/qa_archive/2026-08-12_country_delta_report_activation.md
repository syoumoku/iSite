# 2026-08-12 Country Delta Report Activation Failure

## Incident

- Release: `country_delta_20260812T021434Z`
- Scope: Thailand and Vietnam country delta.
- The PostgreSQL country replacement transaction completed and remote counts were
  verified, but the final public report-index activation raised `TypeError`.
- Data remained available; audited country report files were already uploaded to the
  release incoming directory, but their index had not yet been activated.

## Root Cause

`activate_public_reports()` passed `input_text=` to `_run()`, although `_run()` only
accepts a command list. The module already had `_ssh_input()` for sending a script to
SSH stdin, but the activation path did not use it.

## Correction

- Changed report activation to call `_ssh_input(ssh_target, "bash -se", script)`.
- Added a regression test that verifies the activation script is sent through SSH
  stdin and includes the release-specific incoming directory.
- Reused the uploaded, audited report artifacts to activate the Thailand and Vietnam
  report index; the data delta was not replayed.
- Rewrote and uploaded the completed manifest after verifying Thailand `291` and
  Vietnam `198` at the database layer.

## Control Mapping

- `QA-REPORT-001`: report artifacts and country fingerprints must activate atomically
  with the published country state.
- `QA-PUBLISH-003`: recovery must not replay or replace unrelated country rows.
- `QA-OPS-001`: verify remote counts, service health, disk, memory, UI/API, and report
  downloads after recovery.

## Regression

- `tests/test_public_country_delta.py::test_activate_public_reports_sends_script_over_ssh`
- `PYTHONPATH=.:src .venv/bin/pytest -q tests/test_public_country_delta.py tests/test_public_reports.py`
  completed with `14 passed`.
