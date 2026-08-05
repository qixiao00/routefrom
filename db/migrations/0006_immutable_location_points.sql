BEGIN;

ALTER TABLE app.location_points
  ADD COLUMN dataset_import_id uuid,
  ADD COLUMN geo_time_epoch_ms bigint,
  ADD COLUMN day_time_epoch_ms bigint,
  ADD COLUMN source_latitude double precision,
  ADD COLUMN source_longitude double precision,
  ADD COLUMN source_altitude double precision,
  ADD COLUMN source_course double precision,
  ADD COLUMN source_horizontal_accuracy double precision,
  ADD COLUMN source_vertical_accuracy double precision,
  ADD COLUMN source_speed double precision,
  ADD COLUMN network_name text,
  ADD COLUMN normalization_flags text[] NOT NULL DEFAULT ARRAY[]::text[];

UPDATE app.location_points AS point
SET
  dataset_import_id = import.id,
  geo_time_epoch_ms = floor(extract(epoch FROM point.recorded_at) * 1000)::bigint,
  day_time_epoch_ms = floor(extract(epoch FROM date_trunc('day', point.recorded_at)) * 1000)::bigint,
  source_latitude = point.raw_latitude,
  source_longitude = point.raw_longitude,
  source_altitude = point.altitude_meters,
  source_course = point.course_degrees,
  source_horizontal_accuracy = point.horizontal_accuracy_meters,
  source_vertical_accuracy = point.vertical_accuracy_meters,
  source_speed = point.recorded_speed_mps
FROM app.dataset_imports AS import
WHERE import.dataset_id = point.dataset_id
  AND import.import_number = 1;

DROP INDEX IF EXISTS app.location_points_valid_time_idx;

ALTER TABLE app.location_points
  DROP CONSTRAINT IF EXISTS location_points_dataset_id_source_row_number_key,
  DROP COLUMN calculated_speed_mps,
  DROP COLUMN quality_flags,
  DROP COLUMN is_valid,
  ALTER COLUMN dataset_import_id SET NOT NULL,
  ALTER COLUMN geo_time_epoch_ms SET NOT NULL,
  ALTER COLUMN day_time_epoch_ms SET NOT NULL,
  ALTER COLUMN source_latitude SET NOT NULL,
  ALTER COLUMN source_longitude SET NOT NULL,
  ADD CONSTRAINT location_points_import_dataset_fk
    FOREIGN KEY (dataset_import_id, dataset_id)
    REFERENCES app.dataset_imports(id, dataset_id) ON DELETE CASCADE,
  ADD CONSTRAINT location_points_source_latitude_check
    CHECK (source_latitude BETWEEN -90 AND 90),
  ADD CONSTRAINT location_points_source_longitude_check
    CHECK (source_longitude BETWEEN -180 AND 180),
  ADD CONSTRAINT location_points_import_row_unique
    UNIQUE (dataset_import_id, source_row_number),
  ADD CONSTRAINT location_points_id_dataset_unique UNIQUE (id, dataset_id);

COMMENT ON TABLE app.location_points IS
  'Immutable imported observations. Cleaning and user corrections must never update these rows.';

COMMENT ON COLUMN app.location_points.position IS
  'Canonical WGS84 observation. Source coordinate fields and sentinels remain separately auditable.';

CREATE INDEX location_points_import_time_idx
  ON app.location_points (dataset_import_id, recorded_at, id);

CREATE INDEX location_points_dataset_time_brin
  ON app.location_points USING brin (recorded_at)
  WITH (pages_per_range = 64);

COMMIT;
