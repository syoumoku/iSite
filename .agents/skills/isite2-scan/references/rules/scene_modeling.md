# iSite2 Scene Modeling Rules

Use scene-specific primary indicators. Do not collapse all properties into one cross-scene score.

Priority scenes:
- Airport terminal: passenger throughput, terminal/gateway role, hub status.
- Convention center: exhibition area, event count, peak delegate/visitor capacity.
- Stadium/arena: seats, event role, event-day demand; treat as semi-open.
- Luxury hotel/MICE: keys/rooms, ballroom or meeting area/capacity, MICE role.
- Mall/mixed-use: GLA, annual footfall, tenant/catchment role.
- Office/government: GFA/NLA, building grade, HQ/government role, worker density proxy.
- Transport hub: daily ridership, line count, interchange role.
- Hospital: beds, outpatient volume, grade/teaching role.
- University: enrollment/students, flagship/main campus role.
- Cruise port: passenger throughput, international cruise frequency, terminal role.

Proxy policy:
- `P0 Direct`: primary metric directly evidenced.
- `P1 Strong Proxy`: strong scene-specific proxy with credible source.
- `P2 Moderate Proxy`: weaker proxy usable for screening with Review Queue.
- `P3 Weak Proxy`: exploratory only; do not upgrade confidence.

Top N is display only. Full-scan candidate pools must not be truncated by scene, country, or source availability.
