BEGIN;

CREATE TABLE app.library_place_notes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  label text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 80),
  revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
  position geometry(Point, 4326) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (library_id, user_id) REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE,
  UNIQUE (id, library_id),
  CHECK (ST_X(position) BETWEEN -180 AND 180 AND ST_Y(position) BETWEEN -90 AND 90)
);

CREATE TABLE app.library_place_note_anchors (
  note_id uuid NOT NULL,
  library_id uuid NOT NULL,
  source_id uuid NOT NULL,
  observation_id uuid NOT NULL,
  PRIMARY KEY (note_id, observation_id),
  FOREIGN KEY (note_id, library_id) REFERENCES app.library_place_notes(id, library_id) ON DELETE CASCADE,
  FOREIGN KEY (observation_id, library_id, source_id)
    REFERENCES app.library_observations(id, library_id, source_id) ON DELETE RESTRICT
);
CREATE INDEX library_place_notes_owner_idx ON app.library_place_notes (library_id, user_id);
CREATE INDEX library_place_note_anchors_observation_idx
  ON app.library_place_note_anchors (library_id, observation_id);

COMMENT ON TABLE app.library_place_notes IS
  'Human labels survive file withdrawal, empty snapshots and algorithm recomputation. Never mutate algorithm places.';

COMMIT;
