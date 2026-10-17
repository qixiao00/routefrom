BEGIN;

CREATE TABLE app.library_archive_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('backup', 'restore')),
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'awaiting_review', 'processing', 'succeeded', 'failed')),
  stage text NOT NULL DEFAULT 'queued',
  progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  worker_token uuid,
  lease_until timestamptz,
  input_key text,
  input_sha256 text CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  input_size_bytes bigint CHECK (input_size_bytes >= 0),
  output_key text,
  output_sha256 text CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
  output_size_bytes bigint CHECK (output_size_bytes >= 0),
  plan jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(plan) = 'object'),
  approved_state_hash text CHECK (approved_state_hash ~ '^[0-9a-f]{64}$'),
  processing_job_id uuid,
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics) = 'object'),
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  FOREIGN KEY (library_id, user_id) REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (processing_job_id, library_id, user_id)
    REFERENCES app.library_jobs(id, library_id, user_id) ON DELETE RESTRICT,
  UNIQUE (id, library_id, user_id),
  CHECK (kind <> 'restore' OR (input_key IS NOT NULL AND input_sha256 IS NOT NULL AND input_size_bytes IS NOT NULL)),
  CHECK (status <> 'running' OR (worker_token IS NOT NULL AND lease_until IS NOT NULL)),
  CHECK (status <> 'processing' OR (kind = 'restore' AND processing_job_id IS NOT NULL)),
  CHECK (kind <> 'backup' OR status <> 'succeeded' OR
    (output_key IS NOT NULL AND output_sha256 IS NOT NULL AND output_size_bytes IS NOT NULL))
);
CREATE UNIQUE INDEX library_archive_jobs_one_running_idx ON app.library_archive_jobs(library_id)
  WHERE status = 'running';
CREATE INDEX library_archive_jobs_queue_idx ON app.library_archive_jobs(created_at, id)
  WHERE status = 'queued';
CREATE INDEX library_archive_jobs_owner_idx ON app.library_archive_jobs(library_id, user_id, created_at DESC);

-- Preparation is invisible to library membership and published trajectories.
-- Every attempt has a separate namespace; an expired worker cannot publish another attempt's rows.
CREATE TABLE app.library_restore_inputs (
  archive_job_id uuid NOT NULL,
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  attempt_token uuid NOT NULL,
  origin_file_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  dataset_import_id uuid,
  source_id uuid,
  membership text NOT NULL CHECK (membership IN ('active', 'withdrawn', 'unprocessed')),
  row_count bigint NOT NULL CHECK (row_count >= 0),
  target_key text NOT NULL,
  PRIMARY KEY (archive_job_id, attempt_token, origin_file_id),
  FOREIGN KEY (archive_job_id, library_id, user_id)
    REFERENCES app.library_archive_jobs(id, library_id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, dataset_id) REFERENCES app.datasets(user_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (dataset_import_id, dataset_id) REFERENCES app.dataset_imports(id, dataset_id) ON DELETE RESTRICT,
  CHECK (membership = 'unprocessed' OR (source_id IS NOT NULL AND dataset_import_id IS NOT NULL AND row_count > 0))
);
CREATE TABLE app.library_restore_batches (
  archive_job_id uuid NOT NULL,
  attempt_token uuid NOT NULL,
  origin_file_id uuid NOT NULL,
  batch_index integer NOT NULL CHECK (batch_index >= 0),
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'array' AND jsonb_array_length(payload) BETWEEN 1 AND 2000),
  PRIMARY KEY (archive_job_id, attempt_token, origin_file_id, batch_index),
  FOREIGN KEY (archive_job_id, attempt_token, origin_file_id)
    REFERENCES app.library_restore_inputs(archive_job_id, attempt_token, origin_file_id) ON DELETE CASCADE
);

-- Target IDs are deliberately not copied from archive IDs. The owner-scoped
-- writer resolves each target; dangling receipts retain a user's later deletion.
CREATE TABLE app.library_restore_receipts (
  library_id uuid NOT NULL,
  user_id uuid NOT NULL,
  object_kind text NOT NULL CHECK (object_kind IN ('note', 'view', 'workspace')),
  origin_id uuid NOT NULL,
  target_id uuid,
  imported_hash text NOT NULL CHECK (imported_hash ~ '^[0-9a-f]{64}$'),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (library_id, object_kind, origin_id),
  FOREIGN KEY (library_id, user_id) REFERENCES app.footprint_libraries(id, user_id) ON DELETE CASCADE
);

COMMIT;
