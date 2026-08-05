BEGIN;

CREATE TABLE IF NOT EXISTS app.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_subject text UNIQUE,
  display_name text,
  timezone text NOT NULL DEFAULT 'Asia/Shanghai',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.datasets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  original_filename text NOT NULL,
  source_storage text NOT NULL DEFAULT 'local'
    CHECK (source_storage IN ('local', 'object')),
  source_object_key text NOT NULL,
  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9A-Fa-f]{64}$'),
  source_size_bytes bigint NOT NULL CHECK (source_size_bytes >= 0),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'validating', 'processing', 'ready', 'failed', 'archived')),
  point_count integer NOT NULL DEFAULT 0 CHECK (point_count >= 0),
  rejected_point_count integer NOT NULL DEFAULT 0 CHECK (rejected_point_count >= 0),
  recorded_from timestamptz,
  recorded_to timestamptz,
  parser_version text,
  processing_version text,
  failure_message text,
  uploaded_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz,
  CHECK (recorded_to IS NULL OR recorded_from IS NULL OR recorded_to >= recorded_from),
  UNIQUE (user_id, source_sha256),
  UNIQUE (user_id, id)
);

CREATE TABLE IF NOT EXISTS app.current_datasets (
  user_id uuid PRIMARY KEY REFERENCES app.users(id) ON DELETE CASCADE,
  dataset_id uuid NOT NULL,
  activated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (user_id, dataset_id)
    REFERENCES app.datasets(user_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app.import_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  stage text NOT NULL DEFAULT 'upload',
  progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  rows_processed integer NOT NULL DEFAULT 0 CHECK (rows_processed >= 0),
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS app.processing_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  algorithm text NOT NULL,
  algorithm_version text NOT NULL,
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'running'
    CHECK (status IN ('running', 'succeeded', 'failed')),
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS datasets_user_uploaded_idx
  ON app.datasets (user_id, uploaded_at DESC);

CREATE INDEX IF NOT EXISTS datasets_user_status_idx
  ON app.datasets (user_id, status);

CREATE INDEX IF NOT EXISTS import_jobs_dataset_created_idx
  ON app.import_jobs (dataset_id, created_at DESC);

CREATE INDEX IF NOT EXISTS processing_runs_dataset_algorithm_idx
  ON app.processing_runs (dataset_id, algorithm, started_at DESC);

COMMIT;
