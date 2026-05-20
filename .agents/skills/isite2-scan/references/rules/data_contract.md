# iSite2 Data Contract Rules

Every agent handoff must be structured. Downstream code must never infer fields from narrative text.

Required field families:
- Entity: country, city, property name, scene type/form, coordinates, geocode precision, map source/date, coordinate status.
- Evidence: field group, indicator name, field value, source URL/name/tier/date, fetched date, evidence type, cross-check status.
- Scene model: primary indicators, secondary indicators, proxy basis/level, area or scale metric, annual visits raw/estimate, metric availability.
- Build status: indoor system presence, system type, RAT, build evidence status, source/date/operator.
- Demand: daily visits, busy-hour users, traffic, bandwidth, cannot-calculate reason.
- Inference: inferred field/value, basis, chain, confidence.
- Conclusion: evidence status, value class, action class, recommended solution, reason, risk/review note, next action.
- Review queue: reason, concrete next action, owner/status when available.

When adding a new persisted or API field, update Pydantic models, JSON schema, DB schema/migrations, OpenAPI, tests, and output templates together.
