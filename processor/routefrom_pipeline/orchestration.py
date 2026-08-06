from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

from routefrom_pipeline.map_matching import MapMatcher, MapSnapshotRef
from routefrom_pipeline.database.imports import (
    Connection,
    ImportResult,
    import_linggan_csv,
)
from routefrom_pipeline.database.postgres import PersistenceResult, persist_processed_trace
from routefrom_pipeline.ingest.linggan import iter_linggan_csv
from routefrom_pipeline.pipeline import ProcessingConfig, process_trace


@dataclass(frozen=True, slots=True)
class PipelineExecutionResult:
    import_job_id: UUID
    dataset_import: ImportResult
    processing: PersistenceResult


def _create_job(connection: Connection, dataset_id: UUID) -> UUID:
    job_id = uuid4()
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO app.import_jobs (id, dataset_id, status, stage, progress)
            VALUES (%s, %s, 'queued', 'validate_source', 0)
            """,
            (job_id, dataset_id),
        )
        cursor.execute(
            """
            UPDATE app.datasets
            SET status = 'validating', failure_message = NULL
            WHERE id = %s
            """,
            (dataset_id,),
        )
    return job_id


def _mark_job_running(connection: Connection, job_id: UUID) -> None:
    with connection.transaction():
        connection.cursor().execute(
            """
            UPDATE app.import_jobs
            SET status = 'running', stage = 'validate_source', progress = 5,
                started_at = COALESCE(started_at, now()), updated_at = now()
            WHERE id = %s
            """,
            (job_id,),
        )


def _mark_job_imported(
    connection: Connection,
    job_id: UUID,
    imported: ImportResult,
) -> None:
    with connection.transaction():
        connection.cursor().execute(
            """
            UPDATE app.import_jobs
            SET dataset_import_id = %s, stage = 'run_algorithms', progress = 55,
                rows_processed = %s, updated_at = now()
            WHERE id = %s
            """,
            (imported.dataset_import_id, imported.profile.row_count, job_id),
        )


def _mark_job_persisting(connection: Connection, job_id: UUID) -> None:
    with connection.transaction():
        connection.cursor().execute(
            """
            UPDATE app.import_jobs
            SET stage = 'persist_results', progress = 85, updated_at = now()
            WHERE id = %s
            """,
            (job_id,),
        )


def _mark_job_succeeded(
    connection: Connection,
    job_id: UUID,
    processing: PersistenceResult,
) -> None:
    with connection.transaction():
        connection.cursor().execute(
            """
            UPDATE app.import_jobs
            SET processing_run_id = %s, status = 'succeeded', stage = 'complete',
                progress = 100, completed_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (processing.processing_run_id, job_id),
        )


def _mark_job_failed(
    connection: Connection,
    dataset_id: UUID,
    job_id: UUID,
    error: Exception,
) -> None:
    message = f"{type(error).__name__}: {error}"[:4_000]
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE app.import_jobs
            SET status = 'failed', error_message = %s, completed_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (message, job_id),
        )
        cursor.execute(
            """
            UPDATE app.datasets AS dataset
            SET
              status = CASE
                WHEN EXISTS (
                  SELECT 1 FROM app.processing_runs AS run
                  WHERE run.dataset_id = dataset.id
                    AND run.publication_status = 'active'
                ) THEN 'ready'
                ELSE 'failed'
              END,
              failure_message = %s
            WHERE dataset.id = %s
            """,
            (message, dataset_id),
        )


def run_linggan_pipeline(
    connection: Connection,
    *,
    dataset_id: UUID,
    source_path: str | Path,
    source_object_key: str | None = None,
    timezone: str = "Asia/Shanghai",
    config: ProcessingConfig = ProcessingConfig(),
    parameters: Mapping[str, object] | None = None,
    code_revision: str | None = None,
    activate: bool = True,
    import_batch_size: int = 2_000,
    map_matcher: MapMatcher | None = None,
    map_snapshot: MapSnapshotRef | None = None,
) -> PipelineExecutionResult:
    """Import, process, persist, and publish a Linggan export with job state.

    Import commits before algorithm execution. This is intentional: malformed
    algorithm input or a later processing failure must not erase immutable raw
    observations. Candidate derived results remain atomic in
    ``persist_processed_trace`` and activation only happens after every stage
    succeeds.
    """

    job_id = _create_job(connection, dataset_id)
    _mark_job_running(connection, job_id)
    try:
        imported = import_linggan_csv(
            connection,
            dataset_id=dataset_id,
            source_path=source_path,
            source_object_key=source_object_key,
            timezone=timezone,
            batch_size=import_batch_size,
        )
        _mark_job_imported(connection, job_id, imported)

        points = tuple(iter_linggan_csv(source_path))
        if not points:
            raise ValueError("Linggan CSV contains no location points")
        effective_config = dataclasses.replace(config, timezone=timezone)
        trace = process_trace(
            points,
            config=effective_config,
            map_matcher=map_matcher,
            map_snapshot=map_snapshot,
        )
        _mark_job_persisting(connection, job_id)

        processing_parameters: dict[str, object] = {
            "processing_config": dataclasses.asdict(effective_config),
            "source_parser": {
                "name": "linggan_footprint_csv",
                "timezone": timezone,
            },
            "map_snapshot": (
                dataclasses.asdict(map_snapshot) if map_snapshot is not None else None
            ),
        }
        if parameters:
            processing_parameters["requested"] = dict(parameters)
        processing = persist_processed_trace(
            connection,
            dataset_id=dataset_id,
            dataset_import_id=imported.dataset_import_id,
            input_sha256=imported.profile.sha256,
            trace=trace,
            parameters=processing_parameters,
            code_revision=code_revision,
            activate=activate,
        )
        _mark_job_succeeded(connection, job_id, processing)
        return PipelineExecutionResult(job_id, imported, processing)
    except Exception as error:
        try:
            _mark_job_failed(connection, dataset_id, job_id, error)
        except Exception as audit_error:
            error.add_note(f"failed to persist import job error state: {audit_error}")
        raise
