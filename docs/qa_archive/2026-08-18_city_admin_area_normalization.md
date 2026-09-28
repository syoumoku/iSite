# 2026-08-18 City/Admin-Area Normalization

## Incident

Country scans mixed main cities with communes, districts, wilayas, counties, and other
administrative areas. Algeria exposed the issue most clearly: Bab Ezzouar and Hydra appeared as
peers of Algiers, while Es Senia and Bir El Djir appeared as peers of Oran. This fragmented city
counts, filters, identity keys, and output labels.

## Control

`QA-ENTITY-002` requires a versioned official-source city registry, deterministic exact or
coordinate mapping, preservation of the source locality, and a zero-unresolved/zero-collision
country gate. Fuzzy matches and coordinate/name conflicts cannot auto-assign.

## Implementation

- Rule: `src/isite2/growth/city_normalization.py`
- Registry: `config/city_admin_sources.yaml`
- Migration and audit artifact: `scripts/run_city_normalization_refresh.py`
- Persistence: `city_canonical_units`, `city_locality_mappings`, and
  `property_city_assignments`
- Regression: `tests/test_city_normalization.py`, `tests/test_repository_sqlalchemy.py`, and
  `tests/test_api.py`

Algeria's initial dry-run covered 498 properties: 52 raw labels became 26 canonical cities, with
498 verified mappings, zero unresolved records, and zero identity collisions.
