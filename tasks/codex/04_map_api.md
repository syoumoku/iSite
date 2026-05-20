# Codex Task 04 — Map API

## Goal

Expose GeoJSON endpoints for the world map UI.

## Implement

- `/map/properties` with filters.
- GeoJSON FeatureCollection response.
- Feature properties include statuses, scene, recommendation, main metric, review count.
- Country summary endpoint.

## Acceptance

- Filters work for country, scene_type, evidence_status, action_class, indoor_system_presence.
- Lat/lon order in GeoJSON is `[longitude, latitude]`.
- No property with missing coordinates appears in map output; missing coordinates enter Review Queue.
