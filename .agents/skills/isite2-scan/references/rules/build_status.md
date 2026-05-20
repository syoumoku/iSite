# iSite2 Build Status Rules

High-value proof and indoor build status are separate evidence chains.

Always output:
- Indoor System Presence.
- Indoor System Type.
- Indoor RAT.
- Build Evidence Status.
- Build source/date/operator when evidenced.

Allowed presence/status values must come from config/enums. If no direct evidence exists, use `No Public Evidence` or `Unknown` and create a Review Queue item.

Valid build-status evidence examples:
- Operator indoor coverage/5G deployment announcements.
- Venue network upgrade or DAS project notices.
- Public procurement, regulator, or infrastructure materials.

Invalid shortcuts:
- Do not infer an indoor system exists because the property is high value.
- Do not infer 5G indoor RAT from outdoor citywide 5G launch.
- Do not merge build-source evidence into general value evidence without field-level tagging.
