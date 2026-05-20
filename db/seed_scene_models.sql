-- Optional seed table for scene rule metadata. For MVP, rules are read from config/scenes.yaml.
-- If you prefer DB-driven rule updates, create scene_rules and load this data as JSONB.

CREATE TABLE IF NOT EXISTS scene_rules (
  scene_key TEXT PRIMARY KEY,
  label_zh TEXT NOT NULL,
  scene_form TEXT NOT NULL,
  rule_payload JSONB NOT NULL,
  version TEXT NOT NULL DEFAULT '0.1',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO scene_rules (scene_key, label_zh, scene_form, rule_payload)
VALUES
('airport_terminal', '机场', 'Indoor', '{"primary_indicators":["annual_passenger_throughput","terminal_role","international_passenger_share"],"default_solution":"pRRU"}'),
('stadium', '体育场', 'Semi-open', '{"primary_indicators":["seat_count","event_days","international_events"],"default_solution":"hRRU"}'),
('luxury_hotel_mice', '酒店_MICE', 'Indoor', '{"primary_indicators":["hotel_class","brand","keys","meeting_ballroom_area"],"default_solution":"pRRU"}')
ON CONFLICT (scene_key) DO NOTHING;
