# Codex Task 03 — Excel Output

## Goal

Generate an Excel file matching `config/output_templates.yaml`.

## Implement

- Output service using openpyxl.
- Required sheets.
- Main table fixed columns in exact order.
- Scene sheets with same headers as main table.
- Evidence table rows per field group.
- Inference rows for every inferred/proxy field.
- Proxy model rows from scene config.
- Review queue rows with concrete next actions.
- Method sheet explaining task scope and status definitions.

## Acceptance

- Header order exactly matches config.
- No generic formula explanation is repeated in each main row.
- G column `物业点重要证据` uses direct main indicator when available and does not use busy-hour proxy.
