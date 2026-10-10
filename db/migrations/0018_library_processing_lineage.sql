BEGIN;

-- An input manifest is published only after every selected observation has an
-- exact link to the immutable generated CSV row. Legacy runs have no manifest.
ALTER TABLE app.processing_runs ADD CONSTRAINT processing_runs_input_identity_unique
  UNIQUE (id, dataset_import_id, dataset_id);

CREATE TABLE app.library_processing_inputs (
  processing_run_id uuid PRIMARY KEY,
  dataset_import_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  point_count integer NOT NULL CHECK (point_count > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (processing_run_id, dataset_import_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_import_id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, dataset_id) REFERENCES app.datasets(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (library_id, user_id) REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, dataset_import_id, dataset_id, library_id)
);

CREATE TABLE app.library_run_observations (
  processing_run_id uuid NOT NULL,
  dataset_import_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  library_id uuid NOT NULL,
  source_id uuid NOT NULL,
  observation_id uuid NOT NULL,
  point_id bigint NOT NULL,
  PRIMARY KEY (processing_run_id, point_id),
  UNIQUE (processing_run_id, observation_id),
  FOREIGN KEY (processing_run_id, dataset_import_id, dataset_id, library_id)
    REFERENCES app.library_processing_inputs(processing_run_id, dataset_import_id, dataset_id, library_id) ON DELETE CASCADE,
  FOREIGN KEY (point_id, dataset_import_id, dataset_id)
    REFERENCES app.location_points(id, dataset_import_id, dataset_id) ON DELETE RESTRICT,
  FOREIGN KEY (observation_id, library_id, source_id)
    REFERENCES app.library_observations(id, library_id, source_id) ON DELETE RESTRICT
);

CREATE INDEX library_run_observations_anchor_idx
  ON app.library_run_observations (library_id, observation_id, processing_run_id);

CREATE VIEW app.library_place_observations AS
SELECT DISTINCT input.library_id, input.processing_run_id, version.place_id,
  input.observation_id, input.source_id
FROM app.visits AS visit
JOIN app.place_versions AS version ON version.id = visit.place_version_id
  AND version.processing_run_id = visit.processing_run_id
JOIN app.stationary_events AS event ON event.id = visit.stationary_event_id
  AND event.processing_run_id = visit.processing_run_id
JOIN app.motion_episode_points AS member ON member.motion_episode_id = event.motion_episode_id
  AND member.processing_run_id = event.processing_run_id
JOIN app.library_run_observations AS input ON input.processing_run_id = member.processing_run_id
  AND input.point_id = member.point_id;

COMMENT ON TABLE app.library_processing_inputs IS
  'Complete verified original-observation lineage; independent of publication and library membership changes.';
COMMENT ON VIEW app.library_place_observations IS
  'Exact visit evidence, never nearest-coordinate matching. Withdrawn observations remain auditable.';

COMMIT;
