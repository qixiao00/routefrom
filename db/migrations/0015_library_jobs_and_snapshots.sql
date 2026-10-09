BEGIN;

CREATE TABLE app.library_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  dataset_id uuid,
  kind text NOT NULL CHECK (kind IN ('import', 'reprocess')),
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'awaiting_resolution', 'succeeded', 'failed', 'superseded')),
  stage text NOT NULL DEFAULT 'queued',
  progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  worker_token uuid,
  lease_until timestamptz,
  resolutions jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(resolutions) = 'object'),
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  FOREIGN KEY (library_id, user_id)
    REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, dataset_id) REFERENCES app.datasets(user_id, id) ON DELETE RESTRICT,
  UNIQUE (id, library_id, user_id),
  CHECK (kind <> 'import' OR dataset_id IS NOT NULL),
  CHECK (status <> 'running' OR (worker_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE UNIQUE INDEX library_jobs_one_running_idx ON app.library_jobs (library_id)
  WHERE status = 'running';
CREATE UNIQUE INDEX library_jobs_pending_import_idx ON app.library_jobs (library_id, dataset_id)
  WHERE kind = 'import' AND status IN ('queued', 'running', 'awaiting_resolution');
CREATE INDEX library_jobs_queue_idx ON app.library_jobs (created_at, id) WHERE status = 'queued';
CREATE INDEX library_jobs_user_idx ON app.library_jobs (user_id, created_at DESC);

CREATE TABLE app.library_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  job_id uuid NOT NULL UNIQUE,
  revision bigint NOT NULL CHECK (revision >= 0),
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  decisions jsonb NOT NULL DEFAULT '{}'::jsonb,
  dataset_id uuid,
  processing_run_id uuid,
  status text NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate', 'active', 'superseded')),
  point_count bigint NOT NULL CHECK (point_count >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  FOREIGN KEY (library_id, user_id)
    REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (job_id, library_id, user_id)
    REFERENCES app.library_jobs(id, library_id, user_id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id, dataset_id) REFERENCES app.datasets(user_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE RESTRICT,
  UNIQUE (id, library_id),
  CHECK ((point_count = 0 AND dataset_id IS NULL AND processing_run_id IS NULL)
      OR (point_count > 0 AND dataset_id IS NOT NULL AND processing_run_id IS NOT NULL))
);
CREATE UNIQUE INDEX library_snapshots_one_active_idx ON app.library_snapshots (library_id)
  WHERE status = 'active';
ALTER TABLE app.footprint_libraries ADD COLUMN active_snapshot_id uuid,
  ADD CONSTRAINT footprint_libraries_active_snapshot_fk
    FOREIGN KEY (active_snapshot_id, id) REFERENCES app.library_snapshots(id, library_id)
    DEFERRABLE INITIALLY DEFERRED;

COMMIT;
