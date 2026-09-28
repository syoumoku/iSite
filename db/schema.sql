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
  city_id TEXT,
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

CREATE TABLE IF NOT EXISTS city_canonical_units (
  city_id TEXT PRIMARY KEY,
  country TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  official_name TEXT,
  aliases JSONB NOT NULL DEFAULT '[]',
  grouping_basis TEXT NOT NULL,
  admin_area_1 TEXT,
  admin_area_2 TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  source_authority TEXT,
  source_url TEXT,
  source_date TEXT,
  source_hash TEXT,
  mapping_version TEXT,
  active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS city_locality_mappings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  country TEXT NOT NULL,
  locality_name TEXT NOT NULL,
  locality_normalized TEXT NOT NULL,
  city_id TEXT NOT NULL REFERENCES city_canonical_units(city_id),
  mapping_method TEXT NOT NULL,
  admin_area_1 TEXT,
  admin_area_2 TEXT,
  source_authority TEXT,
  source_url TEXT,
  source_date TEXT,
  source_hash TEXT,
  mapping_version TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(country, locality_normalized)
);

CREATE TABLE IF NOT EXISTS property_city_assignments (
  property_id UUID PRIMARY KEY REFERENCES properties(id) ON DELETE CASCADE,
  country TEXT NOT NULL,
  city_id TEXT REFERENCES city_canonical_units(city_id),
  canonical_city TEXT NOT NULL DEFAULT '',
  source_city TEXT NOT NULL,
  locality TEXT,
  admin_area_1 TEXT,
  admin_area_2 TEXT,
  mapping_status TEXT NOT NULL,
  mapping_method TEXT NOT NULL,
  grouping_basis TEXT,
  source_authority TEXT,
  source_url TEXT,
  source_date TEXT,
  source_hash TEXT,
  mapping_version TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS property_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  source TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_property_aliases_property ON property_aliases(property_id);

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
  evidence_type TEXT NOT NULL CHECK (evidence_type IN ('Direct', 'Proxy', 'Inferred', 'Context')),
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

CREATE TABLE IF NOT EXISTS traffic_estimates_v2 (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  scan_run_id UUID REFERENCES scan_runs(id) ON DELETE SET NULL,
  model_version TEXT NOT NULL,
  estimate_method TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  selected_metric_key TEXT,
  selected_metric_value DOUBLE PRECISION,
  selected_metric_unit TEXT,
  selected_evidence_ids JSONB NOT NULL DEFAULT '[]',
  annual_visits_p10 DOUBLE PRECISION,
  annual_visits_p50 DOUBLE PRECISION,
  annual_visits_p90 DOUBLE PRECISION,
  typical_day_visits_p10 DOUBLE PRECISION,
  typical_day_visits_p50 DOUBLE PRECISION,
  typical_day_visits_p90 DOUBLE PRECISION,
  peak_day_visits_p10 DOUBLE PRECISION,
  peak_day_visits_p50 DOUBLE PRECISION,
  peak_day_visits_p90 DOUBLE PRECISION,
  busy_hour_users_p10 DOUBLE PRECISION,
  busy_hour_users_p50 DOUBLE PRECISION,
  busy_hour_users_p90 DOUBLE PRECISION,
  busy_hour_traffic_gb_p10 DOUBLE PRECISION,
  busy_hour_traffic_gb_p50 DOUBLE PRECISION,
  busy_hour_traffic_gb_p90 DOUBLE PRECISION,
  busy_hour_bandwidth_mbps_p10 DOUBLE PRECISION,
  busy_hour_bandwidth_mbps_p50 DOUBLE PRECISION,
  busy_hour_bandwidth_mbps_p90 DOUBLE PRECISION,
  confidence TEXT NOT NULL,
  activation_status TEXT NOT NULL DEFAULT 'not_evaluated',
  activation_reason TEXT,
  v1_annual_visits_est DOUBLE PRECISION,
  v1_annual_visits_raw DOUBLE PRECISION,
  v1_demand_snapshot JSONB NOT NULL DEFAULT '{}',
  parameter_snapshot JSONB NOT NULL DEFAULT '{}',
  qa_flags JSONB NOT NULL DEFAULT '[]',
  cannot_calculate_reason TEXT,
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (property_id, scan_run_id, model_version, input_hash)
);

CREATE TABLE IF NOT EXISTS complaint_observations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  country TEXT NOT NULL,
  city TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_domain TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sanitized_text TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  category TEXT NOT NULL,
  severity_weight DOUBLE PRECISION NOT NULL,
  match_status TEXT NOT NULL,
  classification_method TEXT NOT NULL,
  classification_confidence DOUBLE PRECISION,
  source_manifest_path TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (property_id, source_url, content_hash)
);

CREATE TABLE IF NOT EXISTS property_complaint_rollups (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  period_days INTEGER NOT NULL DEFAULT 365,
  input_hash TEXT NOT NULL,
  valid_complaint_count INTEGER NOT NULL,
  weighted_complaint_count DOUBLE PRECISION NOT NULL,
  source_count INTEGER NOT NULL,
  category_counts JSONB NOT NULL DEFAULT '{}',
  pressure_level TEXT NOT NULL,
  pressure_percentile DOUBLE PRECISION,
  confidence TEXT NOT NULL,
  latest_observed_at TIMESTAMPTZ,
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (property_id, period_days, input_hash)
);

CREATE TABLE IF NOT EXISTS network_performance_observations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  country TEXT NOT NULL,
  service_type TEXT NOT NULL,
  period TEXT NOT NULL,
  quadkey TEXT NOT NULL,
  tile_latitude DOUBLE PRECISION NOT NULL,
  tile_longitude DOUBLE PRECISION NOT NULL,
  avg_download_mbps DOUBLE PRECISION NOT NULL,
  avg_upload_mbps DOUBLE PRECISION NOT NULL,
  avg_latency_ms DOUBLE PRECISION NOT NULL,
  avg_loaded_latency_down_ms DOUBLE PRECISION,
  avg_loaded_latency_up_ms DOUBLE PRECISION,
  tests INTEGER NOT NULL,
  devices INTEGER NOT NULL,
  match_method TEXT NOT NULL,
  distance_m DOUBLE PRECISION NOT NULL,
  confidence TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_checksum TEXT,
  source_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  license TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (property_id, service_type, period)
);

CREATE TABLE IF NOT EXISTS property_network_performance_rollups (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  country TEXT NOT NULL,
  service_type TEXT NOT NULL,
  period TEXT NOT NULL,
  observation_id UUID NOT NULL REFERENCES network_performance_observations(id)
    ON DELETE CASCADE,
  performance_class TEXT NOT NULL,
  confidence TEXT NOT NULL,
  download_percentile DOUBLE PRECISION,
  loaded_latency_percentile DOUBLE PRECISION,
  trend TEXT,
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (property_id, service_type, period)
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

CREATE TABLE IF NOT EXISTS localized_text_cache (
  id TEXT PRIMARY KEY,
  source_text_hash TEXT NOT NULL,
  text_kind TEXT NOT NULL,
  source_locale TEXT NOT NULL,
  target_locale TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  source_text TEXT NOT NULL,
  translated_text TEXT NOT NULL,
  provider_name TEXT,
  provider_model TEXT,
  confidence DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'success',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_text_hash, text_kind, source_locale, target_locale, schema_version)
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

CREATE TABLE IF NOT EXISTS public_api_property_index (
  property_id TEXT PRIMARY KEY,
  scan_run_id TEXT NOT NULL,
  property_name TEXT NOT NULL,
  aliases JSONB NOT NULL DEFAULT '[]',
  search_text_normalized TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL,
  city TEXT NOT NULL,
  city_id TEXT,
  city_assignment JSONB,
  scene_type TEXT NOT NULL,
  longitude DOUBLE PRECISION,
  latitude DOUBLE PRECISION,
  google_maps_link TEXT,
  geocode_precision TEXT,
  map_source TEXT,
  coordinate_status TEXT,
  evidence_status TEXT NOT NULL,
  value_class TEXT NOT NULL,
  action_class TEXT NOT NULL,
  recommended_solution TEXT NOT NULL,
  annual_visits_est DOUBLE PRECISION,
  annual_visits_p10 DOUBLE PRECISION,
  annual_visits_p50 DOUBLE PRECISION,
  annual_visits_p90 DOUBLE PRECISION,
  traffic_model_version TEXT,
  proxy_level TEXT NOT NULL,
  busy_hour_traffic_gb DOUBLE PRECISION,
  complaint_pressure TEXT,
  network_validation_priority TEXT,
  network_data_freshness TEXT,
  feature_flags JSONB NOT NULL DEFAULT '{}',
  indoor_system_presence TEXT NOT NULL,
  indoor_rat TEXT NOT NULL,
  candidate_quality_status TEXT NOT NULL,
  main_table_ready BOOLEAN NOT NULL DEFAULT true,
  map_ready BOOLEAN NOT NULL DEFAULT true,
  export_ready BOOLEAN NOT NULL DEFAULT true,
  map_coordinate_ready BOOLEAN NOT NULL DEFAULT false,
  has_review_issue BOOLEAN NOT NULL DEFAULT false,
  review_count INTEGER NOT NULL DEFAULT 0,
  source_count INTEGER NOT NULL DEFAULT 0,
  source_urls JSONB NOT NULL DEFAULT '[]',
  main_metric_text TEXT NOT NULL DEFAULT '',
  visibility JSONB NOT NULL DEFAULT '{}',
  quality_issues JSONB NOT NULL DEFAULT '[]',
  sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS public_api_property_packets (
  id TEXT PRIMARY KEY,
  locale TEXT NOT NULL,
  property_id TEXT NOT NULL,
  scan_run_id TEXT NOT NULL,
  country TEXT NOT NULL,
  city TEXT NOT NULL,
  city_id TEXT,
  scene_type TEXT NOT NULL,
  evidence_status TEXT NOT NULL,
  value_class TEXT NOT NULL,
  action_class TEXT NOT NULL,
  recommended_solution TEXT NOT NULL,
  indoor_system_presence TEXT NOT NULL,
  indoor_rat TEXT NOT NULL,
  proxy_level TEXT NOT NULL,
  candidate_quality_status TEXT NOT NULL,
  main_table_ready BOOLEAN NOT NULL DEFAULT true,
  map_ready BOOLEAN NOT NULL DEFAULT true,
  export_ready BOOLEAN NOT NULL DEFAULT true,
  has_review_issue BOOLEAN NOT NULL DEFAULT false,
  sort_order INTEGER NOT NULL,
  packet_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS public_api_map_features (
  id TEXT PRIMARY KEY,
  locale TEXT NOT NULL,
  property_id TEXT NOT NULL,
  scan_run_id TEXT NOT NULL,
  country TEXT NOT NULL,
  city TEXT NOT NULL,
  city_id TEXT,
  scene_type TEXT NOT NULL,
  evidence_status TEXT NOT NULL,
  value_class TEXT NOT NULL,
  action_class TEXT NOT NULL,
  recommended_solution TEXT NOT NULL,
  indoor_system_presence TEXT NOT NULL,
  indoor_rat TEXT NOT NULL,
  proxy_level TEXT NOT NULL,
  candidate_quality_status TEXT NOT NULL,
  map_ready BOOLEAN NOT NULL DEFAULT true,
  map_coordinate_ready BOOLEAN NOT NULL DEFAULT true,
  has_review_issue BOOLEAN NOT NULL DEFAULT false,
  sort_order INTEGER NOT NULL,
  feature_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS public_api_review_queue_rows (
  id TEXT PRIMARY KEY,
  review_id TEXT NOT NULL,
  locale TEXT NOT NULL,
  property_id TEXT NOT NULL,
  scan_run_id TEXT NOT NULL,
  country TEXT NOT NULL,
  city TEXT NOT NULL,
  city_id TEXT,
  scene_type TEXT NOT NULL,
  evidence_status TEXT NOT NULL,
  value_class TEXT NOT NULL,
  action_class TEXT NOT NULL,
  indoor_system_presence TEXT NOT NULL,
  candidate_quality_status TEXT NOT NULL,
  status TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  row_json JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_properties_geom ON properties USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_properties_country_scene ON properties(country, scene_type);
CREATE INDEX IF NOT EXISTS idx_properties_country_city_id ON properties(country, city_id);
CREATE INDEX IF NOT EXISTS idx_city_canonical_units_country_name
  ON city_canonical_units(country, canonical_name);
CREATE INDEX IF NOT EXISTS idx_city_locality_mapping_city
  ON city_locality_mappings(city_id);
CREATE INDEX IF NOT EXISTS idx_property_city_assignments_city
  ON property_city_assignments(country, city_id);
CREATE INDEX IF NOT EXISTS idx_property_city_assignments_status
  ON property_city_assignments(mapping_status);
CREATE INDEX IF NOT EXISTS idx_scan_candidates_scan_run_property_created
  ON scan_candidates(scan_run_id, property_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scan_candidates_country_scene_status
  ON scan_candidates(country, scene_type, candidate_quality_status);
CREATE INDEX IF NOT EXISTS idx_evidence_property ON evidence_items(property_id);
CREATE INDEX IF NOT EXISTS idx_evidence_property_run ON evidence_items(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_scene_property_run
  ON scene_model_results(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_build_property_run ON build_statuses(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_demand_property_run ON demand_estimates(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_traffic_v2_property_run
  ON traffic_estimates_v2(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_traffic_v2_model_method
  ON traffic_estimates_v2(model_version, estimate_method);
CREATE INDEX IF NOT EXISTS idx_complaint_property_observed
  ON complaint_observations(property_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_complaint_country_category
  ON complaint_observations(country, category);
CREATE INDEX IF NOT EXISTS idx_complaint_rollup_property
  ON property_complaint_rollups(property_id);
CREATE INDEX IF NOT EXISTS idx_complaint_rollup_pressure
  ON property_complaint_rollups(pressure_level, confidence);
CREATE INDEX IF NOT EXISTS idx_network_perf_property_type_period
  ON network_performance_observations(property_id, service_type, period);
CREATE INDEX IF NOT EXISTS idx_network_perf_country_type_period
  ON network_performance_observations(country, service_type, period);
CREATE INDEX IF NOT EXISTS idx_network_rollup_property
  ON property_network_performance_rollups(property_id);
CREATE INDEX IF NOT EXISTS idx_network_rollup_country_type_class
  ON property_network_performance_rollups(country, service_type, performance_class);
CREATE INDEX IF NOT EXISTS idx_inference_property_run
  ON inference_records(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_conclusions_property_run ON conclusions(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_review_property_run ON review_queue(property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_review_status_property_run
  ON review_queue(status, property_id, scan_run_id);
CREATE INDEX IF NOT EXISTS idx_source_cache_fetched_at ON source_cache(fetched_at);
CREATE INDEX IF NOT EXISTS idx_localized_text_cache_lookup
  ON localized_text_cache(target_locale, text_kind, source_text_hash, schema_version);
CREATE INDEX IF NOT EXISTS idx_raw_evidence_status ON raw_evidence_items(status);
CREATE INDEX IF NOT EXISTS idx_candidate_drafts_status ON candidate_drafts(status);
CREATE INDEX IF NOT EXISTS idx_discovery_tasks_status_due ON discovery_tasks(status, next_due_at);
CREATE INDEX IF NOT EXISTS idx_discovery_progress_status ON discovery_progress(status);
CREATE INDEX IF NOT EXISTS idx_freshness_tasks_status ON freshness_tasks(status);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_scope
  ON rag_chunks(scan_run_id, property_id, country, scene_type);
CREATE INDEX IF NOT EXISTS idx_public_api_property_index_country_scene
  ON public_api_property_index(country, scene_type);
CREATE INDEX IF NOT EXISTS idx_public_api_property_index_city_scene
  ON public_api_property_index(country, city, scene_type);
CREATE INDEX IF NOT EXISTS idx_public_api_property_index_status
  ON public_api_property_index(candidate_quality_status, main_table_ready, map_ready);
CREATE INDEX IF NOT EXISTS idx_public_api_property_index_filters
  ON public_api_property_index(evidence_status, value_class);
CREATE INDEX IF NOT EXISTS idx_public_api_property_index_search
  ON public_api_property_index(search_text_normalized);
CREATE INDEX IF NOT EXISTS idx_public_api_property_packets_property
  ON public_api_property_packets(property_id);
CREATE INDEX IF NOT EXISTS idx_public_api_property_packets_locale_property
  ON public_api_property_packets(locale, property_id);
CREATE INDEX IF NOT EXISTS idx_public_api_property_packets_locale_country_scene
  ON public_api_property_packets(locale, country, scene_type);
CREATE INDEX IF NOT EXISTS idx_public_api_property_packets_locale_city_scene
  ON public_api_property_packets(locale, country, city, scene_type);
CREATE INDEX IF NOT EXISTS idx_public_api_property_packets_locale_status
  ON public_api_property_packets(locale, candidate_quality_status, main_table_ready);
CREATE INDEX IF NOT EXISTS idx_public_api_map_features_property
  ON public_api_map_features(property_id);
CREATE INDEX IF NOT EXISTS idx_public_api_map_features_locale_country_scene
  ON public_api_map_features(locale, country, scene_type);
CREATE INDEX IF NOT EXISTS idx_public_api_map_features_locale_status
  ON public_api_map_features(locale, candidate_quality_status, map_ready, map_coordinate_ready);
CREATE INDEX IF NOT EXISTS idx_public_api_review_rows_property
  ON public_api_review_queue_rows(property_id);
CREATE INDEX IF NOT EXISTS idx_public_api_review_rows_locale_status
  ON public_api_review_queue_rows(locale, status);
CREATE INDEX IF NOT EXISTS idx_public_api_review_rows_locale_country_scene
  ON public_api_review_queue_rows(locale, country, scene_type);
CREATE INDEX IF NOT EXISTS idx_public_api_review_rows_locale_filters
  ON public_api_review_queue_rows(locale, status, country, scene_type);
