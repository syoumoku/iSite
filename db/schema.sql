CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS scan_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'created',
  rule_version TEXT NOT NULL DEFAULT '0.1',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS properties (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_name TEXT NOT NULL,
  country TEXT NOT NULL,
  city TEXT NOT NULL,
  scene_type TEXT NOT NULL,
  scene_form TEXT NOT NULL,
  latitude DOUBLE PRECISION NOT NULL,
  longitude DOUBLE PRECISION NOT NULL,
  geom GEOGRAPHY(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED,
  geocode_precision TEXT NOT NULL,
  map_source TEXT,
  map_source_date TEXT,
  google_maps_link TEXT,
  coordinate_status TEXT NOT NULL DEFAULT 'Verified',
  hero_image JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (country, city, canonical_name, scene_type)
);

CREATE TABLE IF NOT EXISTS property_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  source TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scan_candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_run_id UUID NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  property_id UUID REFERENCES properties(id),
  raw_name TEXT NOT NULL,
  country TEXT NOT NULL,
  city TEXT,
  scene_type TEXT NOT NULL,
  discovery_source TEXT,
  discovery_rank INTEGER,
  status TEXT NOT NULL DEFAULT 'discovered',
  candidate_quality_status TEXT NOT NULL DEFAULT 'ready',
  visibility JSONB,
  quality_issues JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  field_group TEXT NOT NULL,
  indicator_name TEXT,
  field_value TEXT NOT NULL,
  unit TEXT,
  evidence_type TEXT NOT NULL CHECK (evidence_type IN ('Direct', 'Proxy', 'Inferred')),
  source_name TEXT NOT NULL,
  source_tier TEXT NOT NULL CHECK (source_tier IN ('Tier 1', 'Tier 2', 'Tier 3')),
  source_url TEXT NOT NULL,
  source_date TEXT,
  fetched_at TIMESTAMPTZ,
  cross_check_status TEXT NOT NULL DEFAULT 'Not Checked',
  assumption_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scene_model_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  area_metric_name TEXT NOT NULL,
  area_metric_value NUMERIC,
  area_metric_unit TEXT,
  area_metric_status TEXT NOT NULL,
  primary_value_indicators JSONB NOT NULL DEFAULT '[]',
  secondary_value_indicators JSONB NOT NULL DEFAULT '[]',
  proxy_basis TEXT NOT NULL,
  proxy_level TEXT NOT NULL,
  annual_visits_raw NUMERIC,
  annual_visits_est NUMERIC,
  metric_availability_level TEXT,
  assumption_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS build_statuses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  indoor_system_presence TEXT NOT NULL,
  indoor_system_type TEXT NOT NULL,
  indoor_rat TEXT NOT NULL,
  build_evidence_status TEXT NOT NULL,
  build_source TEXT,
  build_source_date TEXT,
  operator_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS demand_estimates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  daily_visits NUMERIC,
  attach_rate NUMERIC,
  indoor_capture NUMERIC,
  busy_hour_factor NUMERIC,
  gb_per_user_busy_hour NUMERIC,
  busy_hour_users NUMERIC,
  busy_hour_traffic_gb NUMERIC,
  busy_hour_bandwidth_mbps NUMERIC,
  cannot_calculate_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inference_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  inferred_field TEXT NOT NULL,
  inferred_value TEXT NOT NULL,
  inference_basis TEXT NOT NULL,
  inference_chain TEXT NOT NULL,
  inference_confidence TEXT NOT NULL CHECK (inference_confidence IN ('Conservative', 'Moderate')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conclusions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  evidence_status TEXT NOT NULL,
  value_class TEXT NOT NULL,
  action_class TEXT NOT NULL,
  recommended_solution TEXT NOT NULL,
  reason_to_recommend TEXT NOT NULL,
  risk_review_reason TEXT,
  next_action TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_queue (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  reason TEXT NOT NULL,
  next_action TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  review_type TEXT NOT NULL DEFAULT 'general',
  severity TEXT NOT NULL DEFAULT 'medium',
  gate_name TEXT,
  field_path TEXT,
  blocking_surfaces JSONB,
  source_url TEXT,
  suggested_query TEXT,
  owner TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS output_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_run_id UUID NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  artifact_type TEXT NOT NULL,
  path TEXT NOT NULL,
  filter_snapshot JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_cache (
  source_url TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_tier TEXT NOT NULL CHECK (source_tier IN ('Tier 1', 'Tier 2', 'Tier 3')),
  source_date TEXT,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  content_hash TEXT NOT NULL,
  content_text TEXT NOT NULL,
  robots_allowed BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS raw_evidence_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  region TEXT NOT NULL,
  country TEXT NOT NULL,
  city TEXT,
  property_name TEXT,
  scene_type TEXT,
  source_type TEXT,
  source_url TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_tier TEXT NOT NULL CHECK (source_tier IN ('Tier 1', 'Tier 2', 'Tier 3')),
  source_date TEXT,
  field_group TEXT,
  indicator_name TEXT,
  field_value TEXT,
  evidence_type TEXT NOT NULL DEFAULT 'Direct',
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  geocode_precision TEXT,
  map_source TEXT,
  map_source_date TEXT,
  annual_visits NUMERIC,
  content_hash TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',
  curation_needed BOOLEAN NOT NULL DEFAULT true,
  curation_run_id TEXT,
  curated_at TIMESTAMPTZ,
  payload JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidate_drafts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  curation_run_id TEXT NOT NULL,
  raw_evidence_ids JSONB NOT NULL DEFAULT '[]',
  country TEXT,
  city TEXT,
  property_name TEXT,
  scene_type TEXT,
  source_type TEXT,
  status TEXT NOT NULL,
  issues JSONB NOT NULL DEFAULT '[]',
  candidate_payload JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS discovery_progress (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  region TEXT NOT NULL,
  country TEXT NOT NULL,
  scene_type TEXT NOT NULL,
  source_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  cycle_number INTEGER NOT NULL DEFAULT 1,
  no_new_cycles INTEGER NOT NULL DEFAULT 0,
  accepted_new_total INTEGER NOT NULL DEFAULT 0,
  accepted_new_last_cycle INTEGER NOT NULL DEFAULT 0,
  draft_review_total INTEGER NOT NULL DEFAULT 0,
  last_completed_at TIMESTAMPTZ,
  exhausted_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (country, scene_type, source_type)
);

CREATE TABLE IF NOT EXISTS discovery_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  region TEXT NOT NULL,
  country TEXT NOT NULL,
  scene_type TEXT NOT NULL,
  source_type TEXT NOT NULL,
  query_template TEXT NOT NULL,
  query TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 100,
  cycle_number INTEGER NOT NULL DEFAULT 1,
  next_due_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'queued',
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_run_id TEXT,
  last_error TEXT,
  failure_class TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS discovery_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  worker_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  searched_count INTEGER NOT NULL DEFAULT 0,
  fetched_count INTEGER NOT NULL DEFAULT 0,
  discovered_count INTEGER NOT NULL DEFAULT 0,
  new_count INTEGER NOT NULL DEFAULT 0,
  changed_count INTEGER NOT NULL DEFAULT 0,
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  countries JSONB NOT NULL DEFAULT '[]',
  errors JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS human_feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
  field_name TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT NOT NULL,
  reviewer TEXT,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rule_versions (
  version TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  config_snapshot JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inference_corrections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  inferred_field TEXT NOT NULL,
  old_value TEXT NOT NULL,
  corrected_value TEXT NOT NULL,
  evidence_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS freshness_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  due_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_reliability (
  source_name TEXT PRIMARY KEY,
  source_tier TEXT NOT NULL CHECK (source_tier IN ('Tier 1', 'Tier 2', 'Tier 3')),
  successful_extractions INTEGER NOT NULL DEFAULT 0,
  failed_extractions INTEGER NOT NULL DEFAULT 0,
  conflict_count INTEGER NOT NULL DEFAULT 0,
  score NUMERIC NOT NULL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS rag_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_kind TEXT NOT NULL,
  source_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  title TEXT NOT NULL,
  source_url TEXT,
  source_name TEXT,
  source_tier TEXT,
  source_date TEXT,
  fetched_at TIMESTAMPTZ,
  raw_evidence_id UUID,
  property_id UUID,
  scan_run_id UUID,
  country TEXT,
  city TEXT,
  scene_type TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_kind, source_id)
);

CREATE TABLE IF NOT EXISTS rag_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  embedding JSONB NOT NULL DEFAULT '[]',
  embedding_vector vector(64),
  embedding_model TEXT NOT NULL,
  embedding_dim INTEGER NOT NULL,
  token_count INTEGER NOT NULL DEFAULT 0,
  source_url TEXT,
  source_name TEXT,
  source_tier TEXT,
  source_date TEXT,
  fetched_at TIMESTAMPTZ,
  raw_evidence_id UUID,
  property_id UUID,
  scan_run_id UUID,
  country TEXT,
  city TEXT,
  scene_type TEXT,
  field_group TEXT,
  indicator_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_properties_geom ON properties USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_properties_country_scene ON properties(country, scene_type);
CREATE INDEX IF NOT EXISTS idx_evidence_property ON evidence_items(property_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_source_cache_fetched_at ON source_cache(fetched_at);
CREATE INDEX IF NOT EXISTS idx_raw_evidence_status ON raw_evidence_items(status);
CREATE INDEX IF NOT EXISTS idx_candidate_drafts_status ON candidate_drafts(status);
CREATE INDEX IF NOT EXISTS idx_discovery_tasks_status_due ON discovery_tasks(status, next_due_at);
CREATE INDEX IF NOT EXISTS idx_discovery_progress_status ON discovery_progress(status);
CREATE INDEX IF NOT EXISTS idx_freshness_tasks_status ON freshness_tasks(status);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_scope
  ON rag_chunks(scan_run_id, property_id, country, scene_type);
