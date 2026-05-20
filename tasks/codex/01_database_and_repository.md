# Codex Task 01 — Database and Repository Layer

## Goal

Implement PostgreSQL/PostGIS persistence for core entities.

## Implement

- SQLAlchemy models matching `db/schema.sql`.
- Repository interfaces:
  - ScanRunRepository
  - PropertyRepository
  - EvidenceRepository
  - SceneModelRepository
  - BuildStatusRepository
  - DemandRepository
  - InferenceRepository
  - ConclusionRepository
  - ReviewQueueRepository
  - OutputArtifactRepository
- Alembic migration from schema.
- DB integration tests using a test database or sqlite-compatible unit fallback where reasonable.

## Rules

- Evidence and inference are separate tables.
- Build status is separate from conclusion.
- Required status fields may be Unknown but must not be blank.
