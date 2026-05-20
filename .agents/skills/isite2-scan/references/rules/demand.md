# iSite2 Demand Rules

Demand is an explanatory chain, not a universal score.

Required formula chain when annual visits or a defensible proxy is available:
- Daily visits = annual visits / active days.
- Busy-hour users = daily visits * attach rate * indoor capture * busy-hour factor.
- Busy-hour traffic GB = busy-hour users * GB per user busy hour.
- Busy-hour bandwidth Mbps = busy-hour traffic GB * 8192 / busy-hour seconds.

If inputs are missing or non-positive, do not fabricate. Fill `cannot_calculate_reason` and create a Review Queue action to obtain the missing primary metric.

Keep parameters in Method/Parameters/Model Detail, not repeated row by row in the main output.
