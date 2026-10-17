"""Prepare immutable imports without a library lock, then append atomically."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from routefrom_pipeline.backup_manifest import BackupError
from routefrom_pipeline.database.archive_jobs import (
    archive_progress,
    fence_archive_job,
    request_restore_review,
)
from routefrom_pipeline.database.imports import import_linggan_csv
from routefrom_pipeline.database.library import _incoming
from routefrom_pipeline.database.restore_plan import (
    build_restore_plan,
    destination_state,
    restore_human_state,
    semantic_hash,
)
from routefrom_pipeline.ingest.library import IDENTITY_VERSION
from routefrom_pipeline.ingest.linggan import iter_linggan_csv
from routefrom_pipeline.private_storage import private_csv_path


def _write_copy(source, target: Path, expected_size, expected_hash):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            while block := source.read(65536):
                size += len(block)
                if size > expected_size:
                    raise BackupError("original changed while preparing restore")
                digest.update(block)
                output.write(block)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise BackupError("original changed while preparing restore")
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _valid_original(root, key, user_id, file):
    try:
        if not key.startswith(f"raw/{user_id}/"):
            return False
        path = private_csv_path(root, key)
        if path.stat().st_size != file["size"]:
            return False
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest() == file["sha256"]
    except (OSError, ValueError):
        return False


def prepare_restore(connection, job, root, archive_path, backup, mapping):
    """Only staging rows/canonical imports become durable here, never membership or labels."""
    stages = []
    raw_copies = []
    try:
        with connection.transaction():
            fence_archive_job(connection, job)
            connection.execute(
                "DELETE FROM app.library_restore_inputs WHERE archive_job_id=%s", (job.id,)
            )
        with zipfile.ZipFile(archive_path) as archive:
            for index, file in enumerate(backup.manifest["files"]):
                archive_progress(
                    connection,
                    job,
                    "prepare_originals",
                    25 + int(40 * index / max(1, len(backup.manifest["files"]))),
                )
                stage_key = f"staging/{job.user_id}/{uuid4()}.csv"
                stage = private_csv_path(root, stage_key)
                with archive.open(file["member"]) as source:
                    _write_copy(source, stage, file["size"], file["sha256"])
                stages.append(stage)
                with connection.transaction():
                    fence_archive_job(connection, job)
                    dataset = connection.execute(
                        """INSERT INTO app.datasets(user_id,original_filename,source_object_key,source_sha256,source_size_bytes,uploaded_at)
                      VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,source_sha256) DO UPDATE SET source_sha256=EXCLUDED.source_sha256
                      RETURNING id,source_object_key,source_storage""",
                        (
                            job.user_id,
                            file["name"],
                            stage_key,
                            file["sha256"],
                            file["size"],
                            file["uploadedAt"],
                        ),
                    ).fetchone()
                if dataset[2] == "local" and _valid_original(root, dataset[1], job.user_id, file):
                    target_key = dataset[1]
                else:
                    target_key = f"raw/{job.user_id}/{uuid4()}.csv"
                    with stage.open("rb") as source:
                        _write_copy(
                            source, private_csv_path(root, target_key), file["size"], file["sha256"]
                        )
                    raw_copies.append(target_key)
                imported = None
                if file["status"] != "unprocessed":
                    imported = import_linggan_csv(
                        connection, dataset_id=dataset[0], source_path=stage
                    )
                    if imported.profile.row_count != backup.point_counts[file["id"]]:
                        raise BackupError("canonical import differs from verified original")
                source_id = UUID(mapping[file["sourceId"]]) if file["sourceId"] else None
                with connection.transaction():
                    fence_archive_job(connection, job)
                    connection.execute(
                        """INSERT INTO app.library_restore_inputs
                      (archive_job_id,library_id,user_id,attempt_token,origin_file_id,dataset_id,dataset_import_id,source_id,membership,row_count,target_key)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            job.id,
                            job.library_id,
                            job.user_id,
                            job.token,
                            file["id"],
                            dataset[0],
                            imported.dataset_import_id if imported else None,
                            source_id,
                            file["status"],
                            backup.point_counts.get(file["id"], 0),
                            target_key,
                        ),
                    )
                if imported:
                    iterator = iter_linggan_csv(stage)
                    batch_index = 0
                    while points := tuple(itertools.islice(iterator, 1000)):
                        payload = _incoming(points, source_id)
                        with connection.transaction():
                            fence_archive_job(connection, job)
                            connection.execute(
                                "INSERT INTO app.library_restore_batches VALUES (%s,%s,%s,%s,%s::jsonb)",
                                (job.id, job.token, file["id"], batch_index, payload),
                            )
                        batch_index += 1
                with stage.open("rb") as checked:
                    if hashlib.file_digest(checked, "sha256").hexdigest() != file["sha256"]:
                        raise BackupError("staged original changed during preparation")
        with Path(archive_path).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != backup.archive_sha256:
                raise BackupError("archive changed during restore preparation")
        return stages, raw_copies
    except BaseException:
        cleanup_restore_files(connection, root, stages, raw_copies)
        raise


def cleanup_restore_files(connection, root, stages, raw_copies):
    # These exact paths were created exclusively by this attempt. Never remove
    # an original or another attempt's output; retain uncertain files on DB failure.
    for path in stages:
        path.unlink(missing_ok=True)
    for key in raw_copies:
        try:
            with connection.transaction():
                used = connection.execute(
                    "SELECT 1 FROM app.datasets WHERE source_storage='local' AND source_object_key=%s LIMIT 1",
                    (key,),
                ).fetchone()
            if not used:
                private_csv_path(root, key).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001,S112 -- retain files when ownership cannot be checked
            continue


_OBSERVATIONS = """INSERT INTO app.library_observations(library_id,source_id,identity_version,fingerprint,geo_time_epoch_ms,recorded_at,position,payload)
SELECT input.library_id,input.source_id,%s,incoming.fingerprint,incoming.epoch_ms,incoming.recorded_at,
  ST_SetSRID(ST_Point(incoming.longitude,incoming.latitude),4326),incoming.payload
FROM app.library_restore_inputs input JOIN app.library_restore_batches batch
  ON batch.archive_job_id=input.archive_job_id AND batch.attempt_token=input.attempt_token AND batch.origin_file_id=input.origin_file_id
CROSS JOIN LATERAL jsonb_to_recordset(batch.payload) incoming(fingerprint text,epoch_ms bigint,recorded_at timestamptz,longitude double precision,latitude double precision,payload jsonb)
WHERE input.archive_job_id=%s AND input.attempt_token=%s AND input.membership<>'unprocessed'
ON CONFLICT(library_id,source_id,identity_version,fingerprint) DO NOTHING"""

_ORIGINS = """INSERT INTO app.library_observation_origins(library_id,source_id,observation_id,library_file_id,dataset_id,dataset_import_id,source_point_id)
SELECT input.library_id,input.source_id,obs.id,file.id,input.dataset_id,input.dataset_import_id,point.id
FROM app.library_restore_inputs input JOIN app.library_restore_batches batch
  ON batch.archive_job_id=input.archive_job_id AND batch.attempt_token=input.attempt_token AND batch.origin_file_id=input.origin_file_id
CROSS JOIN LATERAL jsonb_to_recordset(batch.payload) incoming(fingerprint text,row_number integer)
JOIN app.library_observations obs ON obs.library_id=input.library_id AND obs.source_id=input.source_id AND obs.fingerprint=incoming.fingerprint AND obs.identity_version=%s
JOIN app.library_files file ON file.library_id=input.library_id AND file.user_id=input.user_id AND file.dataset_id=input.dataset_id AND file.source_id=input.source_id AND file.dataset_import_id=input.dataset_import_id
JOIN app.location_points point ON point.dataset_id=input.dataset_id AND point.dataset_import_id=input.dataset_import_id AND point.source_row_number=incoming.row_number
WHERE input.archive_job_id=%s AND input.attempt_token=%s AND input.membership<>'unprocessed'
ON CONFLICT DO NOTHING"""


def apply_prepared_restore(connection, job, backup, plan, state, mapping, definitions):
    with connection.transaction():
        owned = connection.execute(
            "SELECT id FROM app.footprint_libraries WHERE id=%s AND user_id=%s FOR UPDATE",
            (job.library_id, job.user_id),
        ).fetchone()
        if not owned:
            raise BackupError("destination library disappeared")
        fence_archive_job(connection, job)
        if semantic_hash(destination_state(connection, job)) != plan["stateHash"]:
            return False
        if plan["librarySettings"]["action"] == "restore_defaults":
            connection.execute(
                "UPDATE app.footprint_libraries SET name=%s WHERE id=%s AND user_id=%s",
                (backup.manifest["library"]["name"], job.library_id, job.user_id),
            )
            connection.execute(
                "UPDATE app.users SET timezone=%s WHERE id=%s",
                (backup.manifest["library"]["timezone"], job.user_id),
            )
        for source in definitions:
            if not source["existing"]:
                connection.execute(
                    "INSERT INTO app.library_sources(id,library_id,source_key,name,created_at) VALUES (%s,%s,%s,%s,%s)",
                    (
                        source["id"],
                        job.library_id,
                        source["key"],
                        source["name"],
                        source["createdAt"],
                    ),
                )
        inputs = connection.execute(
            """SELECT origin_file_id,dataset_id,dataset_import_id,source_id,membership,row_count,target_key
          FROM app.library_restore_inputs WHERE archive_job_id=%s AND attempt_token=%s ORDER BY origin_file_id""",
            (job.id, job.token),
        ).fetchall()
        if len(inputs) != len(backup.manifest["files"]):
            raise BackupError("restore preparation is incomplete")
        originals = {file["id"]: file for file in backup.manifest["files"]}
        added_files = 0
        for origin_id, dataset_id, import_id, source_id, membership, _, target_key in inputs:
            original = originals[str(origin_id)]
            connection.execute(
                "UPDATE app.datasets SET source_object_key=%s,source_storage='local' WHERE id=%s AND user_id=%s",
                (target_key, dataset_id, job.user_id),
            )
            if membership == "unprocessed":
                known = connection.execute(
                    "SELECT 1 FROM app.library_jobs WHERE library_id=%s AND user_id=%s AND dataset_id=%s AND kind='import' LIMIT 1",
                    (job.library_id, job.user_id, dataset_id),
                ).fetchone()
                if not known:
                    connection.execute(
                        """INSERT INTO app.library_jobs(library_id,user_id,dataset_id,kind,status,stage,error_message)
                      VALUES (%s,%s,%s,'import','failed','restored_original','从备份保留的未处理原件，未自动参与足迹计算；可检查后重试。')""",
                        (job.library_id, job.user_id, dataset_id),
                    )
                continue
            existing = connection.execute(
                "SELECT source_id,dataset_import_id FROM app.library_files WHERE library_id=%s AND user_id=%s AND dataset_id=%s",
                (job.library_id, job.user_id, dataset_id),
            ).fetchone()
            if existing and (existing[0] != source_id or existing[1] != import_id):
                raise BackupError("existing original belongs to a different source or import")
            if not existing:
                connection.execute(
                    """INSERT INTO app.library_files(library_id,user_id,source_id,dataset_id,dataset_import_id,status,added_at,withdrawn_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        job.library_id,
                        job.user_id,
                        source_id,
                        dataset_id,
                        import_id,
                        membership,
                        original["addedAt"],
                        original["withdrawnAt"],
                    ),
                )
                added_files += 1
        connection.execute(_OBSERVATIONS, (IDENTITY_VERSION, job.id, job.token))
        connection.execute(_ORIGINS, (IDENTITY_VERSION, job.id, job.token))
        for _, dataset_id, _, _, membership, expected, _ in inputs:
            if membership != "unprocessed":
                actual = connection.execute(
                    "SELECT count(*) FROM app.library_observation_origins WHERE library_id=%s AND dataset_id=%s",
                    (job.library_id, dataset_id),
                ).fetchone()[0]
                if actual != expected:
                    raise BackupError("restored original is missing source rows")
        decisions, metrics = restore_human_state(connection, job, backup, mapping, state)
        if added_files:
            connection.execute(
                "UPDATE app.footprint_libraries SET revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                (job.library_id,),
            )
        # Preserve already published human decisions if an archive disagrees.
        current = connection.execute(
            """SELECT snapshot.decisions FROM app.library_snapshots snapshot
          JOIN app.footprint_libraries library ON library.active_snapshot_id=snapshot.id
          WHERE library.id=%s AND library.user_id=%s""",
            (job.library_id, job.user_id),
        ).fetchone()
        resolutions = {str(epoch): fingerprint for epoch, fingerprint in decisions.items()}
        if current:
            resolutions.update(current[0])
        # Preserve explicit choices already waiting in the owner's queue too,
        # in the same deterministic order used by backup export.
        for pending in state["jobs"]:
            resolutions.update(pending["resolutions"])
        processing = connection.execute(
            """INSERT INTO app.library_jobs(library_id,user_id,kind,resolutions)
          VALUES (%s,%s,'reprocess',%s::jsonb) RETURNING id""",
            (job.library_id, job.user_id, json.dumps(resolutions)),
        ).fetchone()[0]
        metrics.update(addedFiles=added_files, originalFiles=len(inputs))
        fence_archive_job(connection, job)
        connection.execute(
            """UPDATE app.library_archive_jobs SET status='processing',stage='process_restored_data',progress=85,
          processing_job_id=%s,metrics=%s::jsonb,lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s""",
            (processing, json.dumps(metrics), job.id),
        )
        connection.execute(
            "DELETE FROM app.library_restore_inputs WHERE archive_job_id=%s", (job.id,)
        )
        return True


def restore_verified_backup(connection, job, root, archive_path, backup):
    plan, state, mapping, definitions = build_restore_plan(connection, job, backup)
    if job.approved_state_hash != plan["stateHash"]:
        request_restore_review(connection, job, plan)
        return
    stages, raw_copies = prepare_restore(connection, job, root, archive_path, backup, mapping)
    try:
        archive_progress(connection, job, "merge_restored_data", 75)
        if not apply_prepared_restore(connection, job, backup, plan, state, mapping, definitions):
            refreshed, _, _, _ = build_restore_plan(connection, job, backup)
            request_restore_review(connection, job, refreshed)
    finally:
        cleanup_restore_files(connection, root, stages, raw_copies)
