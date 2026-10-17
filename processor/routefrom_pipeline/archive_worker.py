"""Archive jobs share the library worker's serialization and fenced publication."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from routefrom_pipeline.backup_archive import verify_backup
from routefrom_pipeline.backup_manifest import BackupError
from routefrom_pipeline.database.archive_jobs import (
    ArchiveLeaseLost,
    archive_progress,
    claim_archive_job,
    fail_archive_job,
    fence_archive_job,
    sync_processing_jobs,
)
from routefrom_pipeline.database.backups import export_library_backup
from routefrom_pipeline.database.restore_library import restore_verified_backup
from routefrom_pipeline.private_storage import private_archive_path


@contextmanager
def _heartbeat(connection, job, enabled):
    stop = threading.Event()

    def renew():
        import psycopg

        try:
            with psycopg.connect(
                connection.info.dsn, autocommit=True, connect_timeout=15
            ) as monitor:
                while not stop.wait(30):
                    row = monitor.execute(
                        """UPDATE app.library_archive_jobs SET lease_until=clock_timestamp()+interval '15 minutes'
                      WHERE id=%s AND library_id=%s AND user_id=%s AND worker_token=%s
                        AND status='running' AND lease_until>clock_timestamp() RETURNING id""",
                        (job.id, job.library_id, job.user_id, job.token),
                    ).fetchone()
                    if not row:
                        break
        except psycopg.Error:
            return  # publication still checks the exact token and real clock

    thread = threading.Thread(target=renew, daemon=True) if enabled else None
    if thread:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join(timeout=1)


def process_archive_job(connection, job, root: Path):
    if job.kind == "backup":
        archive_progress(connection, job, "write_archive", 25)
        key = f"archives/{job.user_id}/{uuid4()}.zip"
        output = private_archive_path(root, key)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            result = export_library_backup(
                connection,
                user_id=job.user_id,
                library_id=job.library_id,
                data_root=root,
                output=output,
            )
            with connection.transaction():
                fence_archive_job(connection, job)
                connection.execute(
                    """UPDATE app.library_archive_jobs SET status='succeeded',stage='complete',progress=100,
                  output_key=%s,output_sha256=%s,output_size_bytes=%s,metrics=%s::jsonb,
                  completed_at=clock_timestamp(),updated_at=clock_timestamp(),lease_until=NULL WHERE id=%s""",
                    (
                        key,
                        result.archive_sha256,
                        output.stat().st_size,
                        json.dumps(
                            {
                                "files": len(result.manifest["files"]),
                                "notes": len(result.manifest["notes"]),
                                "views": len(result.manifest["views"]),
                            }
                        ),
                        job.id,
                    ),
                )
        except BaseException:
            # This attempt's unique output has never been advertised if fenced publication failed.
            with connection.transaction():
                referenced = connection.execute(
                    "SELECT 1 FROM app.library_archive_jobs WHERE output_key=%s LIMIT 1", (key,)
                ).fetchone()
            if not referenced:
                output.unlink(missing_ok=True)
            raise
        return
    if not job.input_key or not job.input_key.startswith(f"archives/{job.user_id}/"):
        raise BackupError("archive does not belong to this owner")
    archive = private_archive_path(root, job.input_key)
    if archive.stat().st_size != job.input_size_bytes:
        raise BackupError("uploaded archive size changed")
    backup = verify_backup(archive)
    if backup.archive_sha256 != job.input_sha256:
        raise BackupError("uploaded archive digest changed")
    restore_verified_backup(connection, job, root, archive, backup)


def run_once(connection, root: Path, *, library_id=None, renew_lease=False):
    synchronized = sync_processing_jobs(connection, library_id)
    job = claim_archive_job(connection, library_id)
    if not job:
        return bool(synchronized)
    try:
        with _heartbeat(connection, job, renew_lease):
            process_archive_job(connection, job, root)
    except ArchiveLeaseLost:
        pass
    except BackupError:
        fail_archive_job(connection, job, "invalid_backup")
    except Exception:  # noqa: BLE001 -- queue boundary never exposes credentials or private filesystem paths
        fail_archive_job(connection, job, "archive_operation_failed")
    return True
