BEGIN;

-- Existing datasets remain immutable file imports / processed snapshots.
-- A personal library groups files; it does not rename or overwrite datasets.
CREATE TABLE app.footprint_libraries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL UNIQUE REFERENCES app.users(id) ON DELETE CASCADE,
  name text NOT NULL DEFAULT '我的足迹',
  revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id)
);

CREATE TABLE app.library_sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL REFERENCES app.footprint_libraries(id) ON DELETE CASCADE,
  source_key text NOT NULL CHECK (length(source_key) BETWEEN 1 AND 128),
  name text NOT NULL,
  parser_name text NOT NULL DEFAULT 'linggan_footprint_csv',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (library_id, source_key),
  UNIQUE (id, library_id)
);

CREATE TABLE app.library_files (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  source_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  dataset_import_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn')),
  added_at timestamptz NOT NULL DEFAULT now(),
  withdrawn_at timestamptz,
  FOREIGN KEY (library_id, user_id)
    REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, dataset_id)
    REFERENCES app.datasets(user_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (source_id, library_id)
    REFERENCES app.library_sources(id, library_id) ON DELETE RESTRICT,
  FOREIGN KEY (dataset_import_id, dataset_id)
    REFERENCES app.dataset_imports(id, dataset_id) ON DELETE RESTRICT,
  UNIQUE (library_id, dataset_id),
  UNIQUE (id, library_id, source_id, dataset_id, dataset_import_id),
  CHECK ((status = 'withdrawn') = (withdrawn_at IS NOT NULL))
);

CREATE TABLE app.library_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL,
  source_id uuid NOT NULL,
  fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  identity_version text NOT NULL,
  geo_time_epoch_ms bigint NOT NULL,
  recorded_at timestamptz NOT NULL,
  position geometry(Point, 4326) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (source_id, library_id)
    REFERENCES app.library_sources(id, library_id) ON DELETE RESTRICT,
  UNIQUE (library_id, source_id, identity_version, fingerprint),
  UNIQUE (id, library_id, source_id),
  CHECK (recorded_at = timestamptz '1970-01-01 00:00:00+00'
      + geo_time_epoch_ms * interval '1 millisecond'),
  CHECK (GeometryType(position) = 'POINT' AND ST_SRID(position) = 4326),
  CHECK (ST_X(position) BETWEEN -180 AND 180 AND ST_Y(position) BETWEEN -90 AND 90)
);

ALTER TABLE app.location_points
  ADD CONSTRAINT location_points_id_import_dataset_unique
    UNIQUE (id, dataset_import_id, dataset_id);

CREATE TABLE app.library_observation_origins (
  library_id uuid NOT NULL,
  source_id uuid NOT NULL,
  observation_id uuid NOT NULL,
  library_file_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  dataset_import_id uuid NOT NULL,
  source_point_id bigint NOT NULL,
  FOREIGN KEY (observation_id, library_id, source_id)
    REFERENCES app.library_observations(id, library_id, source_id) ON DELETE CASCADE,
  FOREIGN KEY (library_file_id, library_id, source_id, dataset_id, dataset_import_id)
    REFERENCES app.library_files(id, library_id, source_id, dataset_id, dataset_import_id)
    ON DELETE CASCADE,
  FOREIGN KEY (source_point_id, dataset_import_id, dataset_id)
    REFERENCES app.location_points(id, dataset_import_id, dataset_id) ON DELETE RESTRICT,
  PRIMARY KEY (library_id, library_file_id, source_point_id)
);

CREATE INDEX library_files_active_idx ON app.library_files (library_id, added_at)
  WHERE status = 'active';
CREATE INDEX library_observations_time_idx
  ON app.library_observations (library_id, recorded_at, id);
CREATE INDEX library_observations_position_idx
  ON app.library_observations USING gist (position);
CREATE INDEX library_observation_origins_observation_idx
  ON app.library_observation_origins (library_id, observation_id);

CREATE VIEW app.active_library_observations AS
SELECT observation.*
FROM app.library_observations AS observation
WHERE EXISTS (
  SELECT 1 FROM app.library_observation_origins AS origin
  JOIN app.library_files AS file ON file.id = origin.library_file_id
  WHERE origin.library_id = observation.library_id
    AND origin.observation_id = observation.id AND file.status = 'active'
);

COMMENT ON TABLE app.library_observation_origins IS
  'Exact deduplication keeps every immutable source row. Withdrawal only changes file membership.';
COMMENT ON COLUMN app.footprint_libraries.revision IS
  'Writers lock the library and advance revision in the same transaction as membership changes.';

COMMIT;
