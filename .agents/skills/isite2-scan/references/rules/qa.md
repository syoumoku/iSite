# iSite2 QA Rules

Minimum QA checks:
- Candidate pool is not truncated by Top N.
- Every conclusion can trace to evidence, date, tier, and inference chain where applicable.
- Scene model matches scene type and uses scene-specific indicators.
- Build status is independent from value status.
- Inference does not overwrite direct evidence.
- Main output avoids repeated generic definitions.
- Review Queue items are concrete actions, not vague "research later" placeholders.
- Coordinates are valid, map-ready status is explicit, and uncertain coordinates are excluded from map-ready outputs.
- API, DB, schema, config, and tests stay synchronized after field changes.

Concrete Review Queue examples:
- "补查机场年报 2024 旅客吞吐量。"
- "查询运营商室分公告并确认 RAT。"
- "核验 Google Maps / OSM 坐标是否为具体楼宇入口或场馆中心。"
