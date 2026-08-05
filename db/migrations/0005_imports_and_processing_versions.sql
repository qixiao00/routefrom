BEGIN;

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE app.dataset_imports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  import_number integer NOT NULL CHECK (import_number > 0),
  parser_name text NOT NULL,
  parser_version text NOT NULL,
  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9A-Fa-f]{64}$'),
  source_object_key text NOT NULL,
  source_size_bytes bigint NOT NULL CHECK (source_size_bytes >= 0),
  schema_fingerprint text,
  row_count integer NOT NULL DEFAULT 0 CHECK (row_count >= 0),
  rejected_row_count integer NOT NULL DEFAULT 0 CHECK (rejected_row_count >= 0),
  status text NOT NULL DEFAULT 'succeeded'
    CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'superseded')),
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (dataset_id, import_number),
  UNIQUE (dataset_id, source_sha256),
  UNIQUE (id, dataset_id)
);

INSERT INTO app.dataset_imports (
  dataset_id,
  import_number,
  parser_name,
  parser_version,
  source_sha256,
  source_object_key,
  source_size_bytes,
  row_count,
  rejected_row_count,
  status,
  created_at,
  completed_at
)
SELECT
  dataset.id,
  1,
  'legacy_dataset_import',
  COALESCE(dataset.parser_version, 'legacy-v1'),
  dataset.source_sha256,
  dataset.source_object_key,
  dataset.source_size_bytes,
  dataset.point_count,
  dataset.rejected_point_count,
  CASE WHEN dataset.status = 'failed' THEN 'failed' ELSE 'succeeded' END,
  dataset.uploaded_at,
  COALESCE(dataset.activated_at, dataset.uploaded_at)
FROM app.datasets AS dataset
ON CONFLICT (dataset_id, source_sha256) DO NOTHING;

ALTER TABLE app.processing_runs
  ADD COLUMN dataset_import_id uuid,
  ADD COLUMN pipeline_version text,
  ADD COLUMN code_revision text,
  ADD COLUMN input_sha256 text,
  ADD COLUMN parameters_hash text,
  ADD COLUMN publication_status text NOT NULL DEFAULT 'candidate'
    CHECK (publication_status IN ('candidate', 'active', 'superseded', 'rejected')),
  ADD COLUMN supersedes_run_id uuid,
  ADD COLUMN activated_at timestamptz;

UPDATE app.processing_runs AS run
SET
  dataset_import_id = import.id,
  pipeline_version = run.algorithm_version,
  input_sha256 = import.source_sha256,
  parameters_hash = encode(digest(run.parameters::text, 'sha256'), 'hex')
FROM app.dataset_imports AS import
WHERE import.dataset_id = run.dataset_id
  AND import.import_number = 1;

ALTER TABLE app.processing_runs
  ALTER COLUMN dataset_import_id SET NOT NULL,
  ALTER COLUMN pipeline_version SET NOT NULL,
  ALTER COLUMN input_sha256 SET NOT NULL,
  ALTER COLUMN parameters_hash SET NOT NULL,
  ADD CONSTRAINT processing_runs_import_dataset_fk
    FOREIGN KEY (dataset_import_id, dataset_id)
    REFERENCES app.dataset_imports(id, dataset_id) ON DELETE CASCADE,
  ADD CONSTRAINT processing_runs_input_sha256_check
    CHECK (input_sha256 ~ '^[0-9A-Fa-f]{64}$'),
  ADD CONSTRAINT processing_runs_parameters_hash_check
    CHECK (parameters_hash ~ '^[0-9A-Fa-f]{64}$'),
  ADD CONSTRAINT processing_runs_id_dataset_unique UNIQUE (id, dataset_id);

ALTER TABLE app.processing_runs
  ADD CONSTRAINT processing_runs_active_must_succeed_check
  CHECK (publication_status <> 'active' OR status = 'succeeded');

ALTER TABLE app.processing_runs
  ADD CONSTRAINT processing_runs_supersedes_same_dataset_fk
  FOREIGN KEY (supersedes_run_id, dataset_id)
  REFERENCES app.processing_runs(id, dataset_id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX processing_runs_one_active_per_dataset_idx
  ON app.processing_runs (dataset_id)
  WHERE publication_status = 'active';

CREATE TABLE app.processing_stage_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL REFERENCES app.processing_runs(id) ON DELETE CASCADE,
  stage_name text NOT NULL,
  sequence_number smallint NOT NULL CHECK (sequence_number >= 0),
  algorithm_name text NOT NULL,
  algorithm_version text NOT NULL,
  model_version text,
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
  parameters_hash text NOT NULL CHECK (parameters_hash ~ '^[0-9A-Fa-f]{64}$'),
  status text NOT NULL DEFAULT 'running'
    CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (processing_run_id, stage_name),
  UNIQUE (processing_run_id, sequence_number),
  UNIQUE (id, processing_run_id)
);

CREATE TABLE app.entity_lineage (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  from_processing_run_id uuid NOT NULL,
  to_processing_run_id uuid NOT NULL,
  entity_type text NOT NULL
    CHECK (entity_type IN ('place', 'stationary_event', 'visit', 'trip', 'mobility_leg')),
  from_logical_id uuid NOT NULL,
  to_logical_id uuid,
  relationship text NOT NULL
    CHECK (relationship IN ('carried', 'split', 'merged', 'replaced', 'dropped')),
  confidence numeric(7, 6) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (from_processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (to_processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  CHECK (from_processing_run_id <> to_processing_run_id)
);

CREATE FUNCTION app.activate_processing_run(target_run_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  target_run app.processing_runs%ROWTYPE;
  stage_count integer;
  stages_ready boolean;
BEGIN
  SELECT * INTO target_run
  FROM app.processing_runs
  WHERE id = target_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'processing run % does not exist', target_run_id;
  END IF;
  IF target_run.status <> 'succeeded' THEN
    RAISE EXCEPTION 'processing run % has not succeeded', target_run_id;
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM app.dataset_imports
    WHERE id = target_run.dataset_import_id
      AND status = 'succeeded'
  ) THEN
    RAISE EXCEPTION 'dataset import for processing run % is not ready', target_run_id;
  END IF;

  SELECT
    count(*),
    COALESCE(bool_and(status IN ('succeeded', 'skipped')), false)
  INTO stage_count, stages_ready
  FROM app.processing_stage_runs
  WHERE processing_run_id = target_run_id;

  IF stage_count = 0 OR NOT stages_ready THEN
    RAISE EXCEPTION 'processing run % has incomplete stages', target_run_id;
  END IF;

  UPDATE app.processing_runs
  SET publication_status = 'superseded'
  WHERE dataset_id = target_run.dataset_id
    AND publication_status = 'active'
    AND id <> target_run_id;

  UPDATE app.processing_runs
  SET publication_status = 'active', activated_at = now()
  WHERE id = target_run_id;

  UPDATE app.datasets
  SET
    status = 'ready',
    processing_version = target_run.pipeline_version,
    activated_at = now()
  WHERE id = target_run.dataset_id;
END
$$;

CREATE INDEX dataset_imports_dataset_created_idx
  ON app.dataset_imports (dataset_id, created_at DESC);

CREATE INDEX processing_runs_dataset_publication_idx
  ON app.processing_runs (dataset_id, publication_status, started_at DESC);

CREATE INDEX processing_stage_runs_run_sequence_idx
  ON app.processing_stage_runs (processing_run_id, sequence_number);

CREATE INDEX entity_lineage_from_idx
  ON app.entity_lineage (from_processing_run_id, entity_type, from_logical_id);

CREATE INDEX entity_lineage_to_idx
  ON app.entity_lineage (to_processing_run_id, entity_type, to_logical_id);

COMMIT;
