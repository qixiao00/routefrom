BEGIN;

DO $$
BEGIN
  IF to_regclass('app.trip_segments') IS NOT NULL
     AND to_regclass('app.legacy_trip_segments') IS NULL THEN
    ALTER TABLE app.trip_segments RENAME TO legacy_trip_segments;
  END IF;
  IF to_regclass('app.trips') IS NOT NULL
     AND to_regclass('app.legacy_trips') IS NULL THEN
    ALTER TABLE app.trips RENAME TO legacy_trips;
  END IF;
  IF to_regclass('app.track_segments') IS NOT NULL
     AND to_regclass('app.legacy_track_segments') IS NULL THEN
    ALTER TABLE app.track_segments RENAME TO legacy_track_segments;
  END IF;
END
$$;

CREATE TABLE app.trips (
  id uuid CONSTRAINT semantic_trips_pkey PRIMARY KEY DEFAULT gen_random_uuid(),
  logical_id uuid NOT NULL DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stage_run_id uuid NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  boundary_state text NOT NULL
    CHECK (boundary_state IN ('closed', 'open_start', 'open_end', 'open_both')),
  start_visit_id uuid,
  end_visit_id uuid,
  trip_range tstzrange NOT NULL,
  confirmed_duration_seconds double precision NOT NULL
    CHECK (confirmed_duration_seconds >= 0),
  unknown_duration_seconds double precision NOT NULL CHECK (unknown_duration_seconds >= 0),
  confirmed_distance_meters double precision NOT NULL CHECK (confirmed_distance_meters >= 0),
  confidence numeric(9, 8) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (start_visit_id, processing_run_id)
    REFERENCES app.visits(id, processing_run_id) ON DELETE RESTRICT,
  FOREIGN KEY (end_visit_id, processing_run_id)
    REFERENCES app.visits(id, processing_run_id) ON DELETE RESTRICT,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, logical_id),
  UNIQUE (processing_run_id, sequence_number),
  UNIQUE (id, processing_run_id),
  CHECK (NOT isempty(trip_range)),
  CHECK (lower_inc(trip_range) AND NOT upper_inc(trip_range)),
  CHECK (
    (boundary_state = 'closed' AND start_visit_id IS NOT NULL AND end_visit_id IS NOT NULL)
    OR (boundary_state = 'open_start' AND start_visit_id IS NULL AND end_visit_id IS NOT NULL)
    OR (boundary_state = 'open_end' AND start_visit_id IS NOT NULL AND end_visit_id IS NULL)
    OR (boundary_state = 'open_both' AND start_visit_id IS NULL AND end_visit_id IS NULL)
  )
);

CREATE TABLE app.transport_modes (
  code text PRIMARY KEY,
  parent_code text REFERENCES app.transport_modes(code) ON DELETE RESTRICT,
  display_name text NOT NULL,
  level smallint NOT NULL CHECK (level BETWEEN 0 AND 2),
  sort_order smallint NOT NULL,
  is_active boolean NOT NULL DEFAULT true
);

INSERT INTO app.transport_modes (code, parent_code, display_name, level, sort_order) VALUES
  ('unknown', NULL, '未知', 0, 0),
  ('pedestrian', NULL, '步行类', 1, 10),
  ('walk', 'pedestrian', '步行', 2, 11),
  ('run', 'pedestrian', '跑步', 2, 12),
  ('cycle', NULL, '骑行类', 1, 20),
  ('bicycle', 'cycle', '自行车', 2, 21),
  ('e_bike', 'cycle', '电动自行车', 2, 22),
  ('road_vehicle', NULL, '道路机动车', 1, 30),
  ('car', 'road_vehicle', '汽车', 2, 31),
  ('bus', 'road_vehicle', '公交车', 2, 32),
  ('motorcycle', 'road_vehicle', '摩托车', 2, 33),
  ('scooter', 'road_vehicle', '踏板车', 2, 34),
  ('rail', NULL, '轨道交通', 1, 40),
  ('metro', 'rail', '地铁', 2, 41),
  ('train', 'rail', '火车', 2, 42),
  ('air', NULL, '航空', 1, 50),
  ('boat', NULL, '船舶', 1, 60);

CREATE TABLE app.mobility_legs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  logical_id uuid NOT NULL DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stage_run_id uuid NOT NULL,
  trip_id uuid NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  observed_range tstzrange NOT NULL,
  start_position geometry(Point, 4326) NOT NULL,
  end_position geometry(Point, 4326) NOT NULL,
  selected_mode_code text NOT NULL REFERENCES app.transport_modes(code),
  selected_mode_level text NOT NULL CHECK (selected_mode_level IN ('root', 'parent', 'detail')),
  confidence numeric(9, 8) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  feature_schema_version text NOT NULL,
  features jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (trip_id, processing_run_id)
    REFERENCES app.trips(id, processing_run_id) ON DELETE CASCADE,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, logical_id),
  UNIQUE (trip_id, sequence_number),
  UNIQUE (id, processing_run_id),
  CHECK (NOT isempty(observed_range)),
  CHECK (lower_inc(observed_range) AND NOT upper_inc(observed_range))
);

CREATE TABLE app.leg_mode_scores (
  mobility_leg_id uuid NOT NULL,
  processing_run_id uuid NOT NULL,
  mode_code text NOT NULL REFERENCES app.transport_modes(code),
  probability numeric(12, 11) NOT NULL CHECK (probability BETWEEN 0 AND 1),
  contributions jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (mobility_leg_id, mode_code),
  FOREIGN KEY (mobility_leg_id, processing_run_id)
    REFERENCES app.mobility_legs(id, processing_run_id) ON DELETE CASCADE
);

CREATE FUNCTION app.validate_leg_mode_scores()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  target_leg_id uuid;
  probability_sum numeric;
  score_count integer;
  selected_code text;
  selected_level text;
BEGIN
  IF TG_TABLE_NAME = 'mobility_legs' THEN
    target_leg_id := NEW.id;
  ELSIF TG_OP = 'DELETE' THEN
    target_leg_id := OLD.mobility_leg_id;
  ELSE
    target_leg_id := NEW.mobility_leg_id;
  END IF;

  SELECT selected_mode_code, selected_mode_level
  INTO selected_code, selected_level
  FROM app.mobility_legs
  WHERE id = target_leg_id;

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  SELECT count(*), COALESCE(sum(probability), 0)
  INTO score_count, probability_sum
  FROM app.leg_mode_scores
  WHERE mobility_leg_id = target_leg_id;

  IF score_count = 0 OR abs(probability_sum - 1) >= 0.000001 THEN
    RAISE EXCEPTION 'mode probabilities for leg % must sum to 1', target_leg_id;
  END IF;
  IF EXISTS (
    SELECT 1
    FROM app.leg_mode_scores AS score
    WHERE score.mobility_leg_id = target_leg_id
      AND EXISTS (
        SELECT 1
        FROM app.transport_modes AS child
        WHERE child.parent_code = score.mode_code
      )
  ) THEN
    RAISE EXCEPTION 'mode probabilities for leg % must be mutually exclusive leaves', target_leg_id;
  END IF;
  IF selected_level IN ('root', 'detail') AND NOT EXISTS (
    SELECT 1
    FROM app.leg_mode_scores
    WHERE mobility_leg_id = target_leg_id
      AND mode_code = selected_code
  ) THEN
    RAISE EXCEPTION 'selected detail/root mode % has no score for leg %', selected_code, target_leg_id;
  END IF;
  RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER mobility_legs_scores_complete_trigger
AFTER INSERT OR UPDATE ON app.mobility_legs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app.validate_leg_mode_scores();

CREATE CONSTRAINT TRIGGER leg_mode_scores_sum_trigger
AFTER INSERT OR UPDATE OR DELETE ON app.leg_mode_scores
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app.validate_leg_mode_scores();

CREATE TABLE app.map_data_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  dataset_name text NOT NULL,
  snapshot_version text NOT NULL,
  source_uri text,
  content_hash text CHECK (content_hash IS NULL OR content_hash ~ '^[0-9A-Fa-f]{64}$'),
  coverage geometry(MultiPolygon, 4326),
  imported_at timestamptz NOT NULL DEFAULT now(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (provider, dataset_name, snapshot_version)
);

ALTER TABLE app.place_versions
  ADD CONSTRAINT place_versions_map_snapshot_fk
  FOREIGN KEY (map_snapshot_id) REFERENCES app.map_data_snapshots(id) ON DELETE SET NULL;

CREATE TABLE app.inferred_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  observation_gap_id uuid,
  trip_id uuid,
  connection_kind text NOT NULL
    CHECK (connection_kind IN ('gap', 'inferred_origin', 'inferred_destination')),
  hypothesis_kind text NOT NULL
    CHECK (hypothesis_kind IN ('same_place', 'straight_line_context', 'map_matched_route')),
  connection_range tstzrange NOT NULL,
  start_position geometry(Point, 4326) NOT NULL,
  end_position geometry(Point, 4326) NOT NULL,
  path geometry(LineString, 4326),
  hypothesized_mode_code text REFERENCES app.transport_modes(code),
  confidence numeric(9, 8) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  displayable boolean NOT NULL DEFAULT false,
  map_snapshot_id uuid REFERENCES app.map_data_snapshots(id) ON DELETE SET NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (observation_gap_id, processing_run_id)
    REFERENCES app.observation_gaps(id, processing_run_id) ON DELETE CASCADE,
  FOREIGN KEY (trip_id, processing_run_id)
    REFERENCES app.trips(id, processing_run_id) ON DELETE CASCADE,
  CHECK (GeometryType(start_position) = 'POINT' AND ST_SRID(start_position) = 4326),
  CHECK (GeometryType(end_position) = 'POINT' AND ST_SRID(end_position) = 4326),
  CHECK (path IS NULL OR (GeometryType(path) = 'LINESTRING' AND ST_SRID(path) = 4326)),
  CHECK (NOT isempty(connection_range)),
  CHECK (lower_inc(connection_range) AND NOT upper_inc(connection_range)),
  CHECK (connection_kind <> 'gap' OR observation_gap_id IS NOT NULL)
);

CREATE TABLE app.entity_corrections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  entity_type text NOT NULL
    CHECK (entity_type IN ('place', 'stationary_event', 'visit', 'trip', 'mobility_leg')),
  entity_logical_id uuid NOT NULL,
  base_processing_run_id uuid NOT NULL,
  patch jsonb NOT NULL,
  locked_fields text[] NOT NULL DEFAULT ARRAY[]::text[],
  status text NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'revoked', 'superseded')),
  reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  FOREIGN KEY (base_processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, dataset_id)
    REFERENCES app.datasets(user_id, id) ON DELETE CASCADE
);

CREATE INDEX semantic_trips_run_range_gix
  ON app.trips USING gist (processing_run_id, trip_range);

CREATE INDEX mobility_legs_trip_sequence_idx
  ON app.mobility_legs (trip_id, sequence_number);

CREATE INDEX mobility_legs_mode_range_idx
  ON app.mobility_legs (processing_run_id, selected_mode_code, lower(observed_range));

CREATE INDEX inferred_connections_run_range_gix
  ON app.inferred_connections USING gist (processing_run_id, connection_range);

CREATE UNIQUE INDEX entity_corrections_one_active_lock_idx
  ON app.entity_corrections (user_id, dataset_id, entity_type, entity_logical_id)
  WHERE status = 'active';

COMMIT;
