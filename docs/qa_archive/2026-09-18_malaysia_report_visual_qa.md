# Malaysia Standard Report Visual QA

- Control: QA-OUTPUT-002 (manual visual gate), QA-REPORT-001.
- Release: country_delta_20260918T025659Z.
- Both published workbooks passed existing content audits and matched the audited download hashes. Each has 104 main rows and 48 recommendation rows.
- Post-publication rendering found default column widths and no wrapping in Main. Long property/city names are clipped in the initial view; the underlying values remain complete.
- Evidence: `.web_evidence/malaysia_deep_scan_20260918/report_preview/en.png`, `zh.png`, and `remote_smoke/summary.json`. Read-only inspection confirmed no explicit column dimensions or wrap alignment.
- Visual QA is not passed. Do not describe a successful structural audit as visual approval.
- Follow-up: fix shared standard-report formatting, render before activation, then regenerate and re-audit Malaysia reports through the existing report publisher. Do not manually replace published files or refresh evidence/derived fields for a formatting-only change.
- Browser navigation also timed out; HTTP/API/download checks passed but interactive UI smoke was not completed.

## 2026-09-21 Generator Correction

- The existing Excel generator now sets bounded content-aware column widths, wraps text, estimates row heights with wide-script characters, and freezes the header row. It preserves values, field order and recommendation fills.
- Regression: `tests/test_excel_output.py::test_excel_layout_preserves_full_names_and_recommendation_fill` covers English and Chinese reports, complete long names, wrapping and highlighted recommendations.
- QA-OUTPUT-002 still requires actual generated-report previews before release. Extremely long cells remain subject to Excel's 409-point row-height limit and must not be claimed visually approved solely on these tests.
- This correction applies to newly generated reports. It does not regenerate or replace previously published Malaysia reports or refresh any property evidence/derived values.
