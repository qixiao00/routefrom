BEGIN;

CREATE TABLE IF NOT EXISTS app.location_points (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  source_row_number integer NOT NULL CHECK (source_row_number > 0),
  recorded_at timestamptz NOT NULL,
  local_date date NOT NULL,
  timezone text NOT NULL DEFAULT 'Asia/Shanghai',
  position geometry(Point, 4326) NOT NULL,
  raw_latitude double precision NOT NULL CHECK (raw_latitude BETWEEN -90 AND 90),
  raw_longitude double precision NOT NULL CHECK (raw_longitude BETWEEN -180 AND 180),
  altitude_meters double precision,
  recorded_speed_mps double precision CHECK (recorded_speed_mps IS NULL OR recorded_speed_mps >= 0),
  calculated_speed_mps double precision CHECK (calculated_speed_mps IS NULL OR calculated_speed_mps >= 0),
  course_degrees double precision CHECK (course_degrees IS NULL OR course_degrees BETWEEN 0 AND 360),
  horizontal_accuracy_meters double precision
    CHECK (horizontal_accuracy_meters IS NULL OR horizontal_accuracy_meters >= 0),
  vertical_accuracy_meters double precision
    CHECK (vertical_accuracy_meters IS NULL OR vertical_accuracy_meters >= 0),
  network_type smallint,
  location_type smallint,
  quality_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
  is_valid boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dataset_id, source_row_number),
  CHECK (GeometryType(position) = 'POINT' AND ST_SRID(position) = 4326)
);

CREATE TABLE IF NOT EXISTS app.places (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  centroid geometry(Point, 4326) NOT NULL,
  boundary geometry(Polygon, 4326),
  name text,
  category text,
  address text,
  city text,
  region text,
  country_code text,
  timezone text,
  geocoding_provider text,
  geocoding_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_private boolean NOT NULL DEFAULT false,
  manually_edited boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.visits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  place_id uuid REFERENCES app.places(id) ON DELETE SET NULL,
  processing_run_id uuid REFERENCES app.processing_runs(id) ON DELETE SET NULL,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  duration_seconds integer NOT NULL CHECK (duration_seconds >= 0),
  centroid geometry(Point, 4326) NOT NULL,
  point_count integer NOT NULL CHECK (point_count > 0),
  confidence numeric(5, 4) CHECK (confidence BETWEEN 0 AND 1),
  manually_edited boolean NOT NULL DEFAULT false,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ended_at >= started_at)
);

CREATE TABLE IF NOT EXISTS app.track_segments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  processing_run_id uuid REFERENCES app.processing_runs(id) ON DELETE SET NULL,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  path geometry(LineString, 4326) NOT NULL,
  point_count integer NOT NULL CHECK (point_count >= 2),
  distance_meters double precision NOT NULL DEFAULT 0 CHECK (distance_meters >= 0),
  has_gap_before boolean NOT NULL DEFAULT false,
  has_gap_after boolean NOT NULL DEFAULT false,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ended_at >= started_at)
);

CREATE TABLE IF NOT EXISTS app.trips (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  processing_run_id uuid REFERENCES app.processing_runs(id) ON DELETE SET NULL,
  start_visit_id uuid REFERENCES app.visits(id) ON DELETE SET NULL,
  end_visit_id uuid REFERENCES app.visits(id) ON DELETE SET NULL,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  travelled_distance_meters double precision NOT NULL DEFAULT 0
    CHECK (travelled_distance_meters >= 0),
  straight_distance_meters double precision
    CHECK (straight_distance_meters IS NULL OR straight_distance_meters >= 0),
  inferred_transport_mode text NOT NULL DEFAULT 'unknown',
  transport_confidence numeric(5, 4) CHECK (transport_confidence BETWEEN 0 AND 1),
  manual_transport_mode text,
  has_data_gap boolean NOT NULL DEFAULT false,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ended_at >= started_at)
);

CREATE TABLE IF NOT EXISTS app.trip_segments (
  trip_id uuid NOT NULL REFERENCES app.trips(id) ON DELETE CASCADE,
  segment_id uuid NOT NULL REFERENCES app.track_segments(id) ON DELETE CASCADE,
  sequence_number smallint NOT NULL CHECK (sequence_number >= 0),
  PRIMARY KEY (trip_id, segment_id),
  UNIQUE (trip_id, sequence_number)
);

CREATE INDEX IF NOT EXISTS location_points_dataset_time_idx
  ON app.location_points (dataset_id, recorded_at);

CREATE INDEX IF NOT EXISTS location_points_dataset_date_idx
  ON app.location_points (dataset_id, local_date);

CREATE INDEX IF NOT EXISTS location_points_position_gix
  ON app.location_points USING gist (position);

CREATE INDEX IF NOT EXISTS location_points_geography_gix
  ON app.location_points USING gist ((position::geography));

CREATE INDEX IF NOT EXISTS location_points_valid_time_idx
  ON app.location_points (dataset_id, recorded_at) WHERE is_valid;

CREATE INDEX IF NOT EXISTS places_user_centroid_gix
  ON app.places USING gist (centroid);

CREATE INDEX IF NOT EXISTS visits_dataset_time_idx
  ON app.visits (dataset_id, started_at, ended_at);

CREATE INDEX IF NOT EXISTS visits_place_started_idx
  ON app.visits (place_id, started_at DESC);

CREATE INDEX IF NOT EXISTS visits_centroid_gix
  ON app.visits USING gist (centroid);

CREATE INDEX IF NOT EXISTS track_segments_dataset_time_idx
  ON app.track_segments (dataset_id, started_at, ended_at);

CREATE INDEX IF NOT EXISTS track_segments_path_gix
  ON app.track_segments USING gist (path);

CREATE INDEX IF NOT EXISTS trips_dataset_time_idx
  ON app.trips (dataset_id, started_at, ended_at);

CREATE INDEX IF NOT EXISTS trips_transport_mode_idx
  ON app.trips (dataset_id, inferred_transport_mode, started_at);

COMMIT;
