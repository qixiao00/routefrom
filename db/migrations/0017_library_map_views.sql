BEGIN;

-- User-authored state belongs to the library, never to a generated dataset/run.
CREATE TABLE app.library_workspace_state (
  library_id uuid PRIMARY KEY,
  user_id uuid NOT NULL,
  revision bigint NOT NULL CHECK (revision > 0),
  config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object' AND config->>'schemaVersion' = '1'),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (library_id, user_id) REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE
);

CREATE TABLE app.library_saved_views (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 80),
  config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object' AND config->>'schemaVersion' = '1'),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (library_id, user_id) REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE
);
CREATE INDEX library_saved_views_owner_created_idx ON app.library_saved_views(library_id, user_id, created_at DESC);

COMMIT;
