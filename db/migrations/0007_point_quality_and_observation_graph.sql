BEGIN;

CREATE TABLE app.point_assessments (
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  point_id bigint NOT NULL,
  stage_run_id uuid NOT NULL,
  quality_status text NOT NULL
    CHECK (quality_status IN ('valid', 'suspect', 'excluded')),
  anomaly_probability numeric(9, 8) NOT NULL CHECK (anomaly_probability BETWEEN 0 AND 1),
  path_weight numeric(9, 8) NOT NULL CHECK (path_weight BETWEEN 0 AND 1),
  stay_weight numeric(9, 8) NOT NULL CHECK (stay_weight BETWEEN 0 AND 1),
  mode_weight numeric(9, 8) NOT NULL CHECK (mode_weight BETWEEN 0 AND 1),
  exclusion_supported boolean NOT NULL DEFAULT false,
  reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
  features jsonb NOT NULL DEFAULT '{}'::jsonb,
  explanation jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (processing_run_id, point_id),
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (point_id, dataset_id)
    REFERENCES app.location_points(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  CHECK (quality_status <> 'excluded' OR exclusion_supported)
);

CREATE TABLE app.observation_edges (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stage_run_id uuid NOT NULL,
  from_point_id bigint NOT NULL,
  to_point_id bigint NOT NULL,
  edge_kind text NOT NULL CHECK (edge_kind IN ('adjacent', 'bypass', 'break')),
  elapsed_seconds double precision NOT NULL,
  displacement_meters double precision NOT NULL CHECK (displacement_meters >= 0),
  calculated_speed_mps double precision
    CHECK (calculated_speed_mps IS NULL OR calculated_speed_mps >= 0),
  continuity_probability numeric(9, 8) NOT NULL
    CHECK (continuity_probability BETWEEN 0 AND 1),
  reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (from_point_id, dataset_id)
    REFERENCES app.location_points(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (to_point_id, dataset_id)
    REFERENCES app.location_points(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, from_point_id, to_point_id),
  CHECK (from_point_id <> to_point_id),
  CHECK (
    (edge_kind = 'break' AND continuity_probability = 0)
    OR edge_kind <> 'break'
  )
);

CREATE TABLE app.observation_gaps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  logical_id uuid NOT NULL DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stage_run_id uuid NOT NULL,
  before_point_id bigint NOT NULL,
  after_point_id bigint NOT NULL,
  gap_range tstzrange NOT NULL,
  cause text NOT NULL CHECK (cause IN (
    'source_sampling_gap', 'excluded_block', 'continuity_failure',
    'clock_discontinuity', 'unknown'
  )),
  context_before text,
  context_after text,
  same_place_probability numeric(9, 8) NOT NULL
    CHECK (same_place_probability BETWEEN 0 AND 1),
  confidence numeric(9, 8) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (before_point_id, dataset_id)
    REFERENCES app.location_points(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (after_point_id, dataset_id)
    REFERENCES app.location_points(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, logical_id),
  UNIQUE (id, processing_run_id),
  UNIQUE (processing_run_id, before_point_id, after_point_id),
  CHECK (NOT isempty(gap_range)),
  CHECK (lower_inc(gap_range) AND NOT upper_inc(gap_range)),
  CHECK (lower(gap_range) < upper(gap_range))
);

CREATE INDEX point_assessments_run_quality_idx
  ON app.point_assessments (processing_run_id, quality_status, point_id);

CREATE INDEX observation_edges_run_from_idx
  ON app.observation_edges (processing_run_id, from_point_id);

CREATE INDEX observation_gaps_run_range_gix
  ON app.observation_gaps USING gist (processing_run_id, gap_range);

COMMIT;
