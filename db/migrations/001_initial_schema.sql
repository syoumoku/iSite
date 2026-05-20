-- MVP migration entrypoint.
-- Apply with psql against PostgreSQL/PostGIS:
--   psql "$DATABASE_URL" -f db/migrations/001_initial_schema.sql
\i ../schema.sql
