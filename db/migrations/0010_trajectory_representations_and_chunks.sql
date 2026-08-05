BEGIN;

CREATE TABLE app.trajectory_variants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stage_run_id uuid NOT NULL,
  variant_kind text NOT NULL
    CHECK (variant_kind IN ('cleaned_gps', 'smoothed_gps', 'map_matched')),
  confidence numeric(9, 8) CHECK (confidence BETWEEN 0 AND 1),
  map_snapshot_id uuid REFERENCES app.map_data_snapshots(id) ON DELETE SET NULL,
  preferred_for_display boolean NOT NULL DEFAULT false,
  preferred_for_distance boolean NOT NULL DEFAULT false,
  fallback_reason text,
  importance_algorithm text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, variant_kind),
  UNIQUE (id, processing_run_id),
  UNIQUE (id, dataset_id)
);

CREATE UNIQUE INDEX trajectory_variants_one_display_preference_idx
  ON app.trajectory_variants (processing_run_id)
  WHERE preferred_for_display;

CREATE UNIQUE INDEX trajectory_variants_one_distance_preference_idx
  ON app.trajectory_variants (processing_run_id)
  WHERE preferred_for_distance;

CREATE TABLE app.trajectory_segments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trajectory_variant_id uuid NOT NULL REFERENCES app.trajectory_variants(id) ON DELETE CASCADE,
  processing_run_id uuid NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  observed_range tstzrange NOT NULL,
  path geometry(LineString, 4326),
  point_count integer NOT NULL CHECK (point_count > 0),
  distance_meters double precision NOT NULL CHECK (distance_meters >= 0),
  has_gap_before boolean NOT NULL DEFAULT false,
  has_gap_after boolean NOT NULL DEFAULT false,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (trajectory_variant_id, processing_run_id)
    REFERENCES app.trajectory_variants(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (trajectory_variant_id, sequence_number),
  UNIQUE (id, trajectory_variant_id),
  UNIQUE (id, processing_run_id),
  CHECK (NOT isempty(observed_range)),
  CHECK (lower_inc(observed_range) AND NOT upper_inc(observed_range)),
  CHECK (
    path IS NULL
    OR (GeometryType(path) = 'LINESTRING' AND ST_SRID(path) = 4326)
  )
);

CREATE TABLE app.trajectory_vertices (
  trajectory_variant_id uuid NOT NULL REFERENCES app.trajectory_variants(id) ON DELETE CASCADE,
  trajectory_segment_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  segment_sequence_number integer NOT NULL CHECK (segment_sequence_number >= 0),
  recorded_at timestamptz NOT NULL,
  position geometry(Point, 4326) NOT NULL,
  source_point_id bigint,
  is_interpolated boolean NOT NULL DEFAULT false,
  is_semantic_anchor boolean NOT NULL DEFAULT false,
  anchor_reasons text[] NOT NULL DEFAULT ARRAY[]::text[],
  importance_meters double precision,
  PRIMARY KEY (trajectory_variant_id, sequence_number),
  FOREIGN KEY (trajectory_segment_id, trajectory_variant_id)
    REFERENCES app.trajectory_segments(id, trajectory_variant_id) ON DELETE CASCADE,
  FOREIGN KEY (trajectory_variant_id, dataset_id)
    REFERENCES app.trajectory_variants(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (source_point_id, dataset_id)
    REFERENCES app.location_points(id, dataset_id) ON DELETE RESTRICT,
  UNIQUE (trajectory_segment_id, segment_sequence_number),
  CHECK (GeometryType(position) = 'POINT' AND ST_SRID(position) = 4326),
  CHECK (NOT is_interpolated OR source_point_id IS NULL),
  CHECK (
    (is_semantic_anchor AND importance_meters IS NULL)
    OR (
      NOT is_semantic_anchor
      AND importance_meters IS NOT NULL
      AND importance_meters >= 0
    )
  )
);

CREATE TABLE app.trajectory_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trajectory_variant_id uuid NOT NULL REFERENCES app.trajectory_variants(id) ON DELETE CASCADE,
  trajectory_segment_id uuid NOT NULL,
  chunk_number integer NOT NULL CHECK (chunk_number >= 0),
  time_range tstzrange NOT NULL,
  first_vertex_sequence integer NOT NULL CHECK (first_vertex_sequence >= 0),
  last_vertex_sequence integer NOT NULL CHECK (last_vertex_sequence >= first_vertex_sequence),
  vertex_count integer NOT NULL CHECK (vertex_count > 0),
  estimated_bytes integer NOT NULL CHECK (estimated_bytes > 0),
  has_leading_overlap boolean NOT NULL DEFAULT false,
  path geometry(LineString, 4326),
  bounds geometry(Polygon, 4326),
  min_importance_meters double precision,
  max_importance_meters double precision,
  binary_object_key text,
  content_hash text,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (trajectory_segment_id, trajectory_variant_id)
    REFERENCES app.trajectory_segments(id, trajectory_variant_id) ON DELETE CASCADE,
  UNIQUE (trajectory_variant_id, chunk_number),
  CHECK (NOT isempty(time_range)),
  CHECK (lower_inc(time_range) AND NOT upper_inc(time_range)),
  CHECK (min_importance_meters IS NULL OR min_importance_meters >= 0),
  CHECK (max_importance_meters IS NULL OR max_importance_meters >= 0),
  CHECK (
    min_importance_meters IS NULL
    OR max_importance_meters IS NULL
    OR min_importance_meters <= max_importance_meters
  ),
  CHECK (content_hash IS NULL OR content_hash ~ '^[0-9A-Fa-f]{64}$')
);

CREATE TABLE app.trip_track_segments (
  trip_id uuid NOT NULL,
  processing_run_id uuid NOT NULL,
  trajectory_segment_id uuid NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  included_range tstzrange NOT NULL,
  PRIMARY KEY (trip_id, trajectory_segment_id, included_range),
  FOREIGN KEY (trip_id, processing_run_id)
    REFERENCES app.trips(id, processing_run_id) ON DELETE CASCADE,
  FOREIGN KEY (trajectory_segment_id, processing_run_id)
    REFERENCES app.trajectory_segments(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (trip_id, sequence_number),
  CHECK (NOT isempty(included_range)),
  CHECK (lower_inc(included_range) AND NOT upper_inc(included_range))
);

CREATE TABLE app.mobility_leg_segments (
  mobility_leg_id uuid NOT NULL,
  processing_run_id uuid NOT NULL,
  trajectory_segment_id uuid NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  included_range tstzrange NOT NULL,
  PRIMARY KEY (mobility_leg_id, trajectory_segment_id, included_range),
  FOREIGN KEY (mobility_leg_id, processing_run_id)
    REFERENCES app.mobility_legs(id, processing_run_id) ON DELETE CASCADE,
  FOREIGN KEY (trajectory_segment_id, processing_run_id)
    REFERENCES app.trajectory_segments(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (mobility_leg_id, sequence_number),
  CHECK (NOT isempty(included_range)),
  CHECK (lower_inc(included_range) AND NOT upper_inc(included_range))
);

CREATE INDEX trajectory_segments_variant_range_gix
  ON app.trajectory_segments USING gist (trajectory_variant_id, observed_range);

CREATE INDEX trajectory_segments_path_gix
  ON app.trajectory_segments USING gist (path);

CREATE INDEX trajectory_vertices_variant_time_idx
  ON app.trajectory_vertices (trajectory_variant_id, recorded_at, sequence_number);

CREATE INDEX trajectory_vertices_position_gix
  ON app.trajectory_vertices USING gist (position);

CREATE INDEX trajectory_chunks_variant_range_gix
  ON app.trajectory_chunks USING gist (trajectory_variant_id, time_range);

CREATE INDEX trajectory_chunks_bounds_gix
  ON app.trajectory_chunks USING gist (bounds);

COMMIT;
