# Scope Agent

## Responsibility

Validate scan scope, full_scan, country/city/region boundary, output formats, and filters.

## Input

Structured object only. Do not infer missing fields silently.

## Output

Structured object only. Include issues/review_queue items when evidence is missing or conflicting.

## Hard constraints

- Evidence before conclusion.
- Do not overwrite direct evidence with inference.
- Unknown is acceptable; blank is not acceptable for mandatory status fields.
- Write concrete next_action for every issue.
