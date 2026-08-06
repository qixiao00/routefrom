BEGIN;

ALTER TABLE app.import_jobs
  ADD COLUMN dataset_import_id uuid,
  ADD COLUMN processing_run_id uuid,
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
  ADD CONSTRAINT import_jobs_import_dataset_fk
    FOREIGN KEY (dataset_import_id, dataset_id)
    REFERENCES app.dataset_imports(id, dataset_id) ON DELETE CASCADE,
  ADD CONSTRAINT import_jobs_processing_run_dataset_fk
    FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE RESTRICT,
  ADD CONSTRAINT import_jobs_run_requires_import_check
    CHECK (processing_run_id IS NULL OR dataset_import_id IS NOT NULL);

CREATE INDEX import_jobs_import_idx
  ON app.import_jobs (dataset_import_id, created_at DESC)
  WHERE dataset_import_id IS NOT NULL;

CREATE INDEX import_jobs_processing_run_idx
  ON app.import_jobs (processing_run_id)
  WHERE processing_run_id IS NOT NULL;

COMMIT;
