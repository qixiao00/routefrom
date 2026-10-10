"""Durable library queue: immutable import, full recompute and atomic publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from routefrom_pipeline.database.imports import import_linggan_csv
from routefrom_pipeline.database.library import attach_library_import, load_library_snapshot
from routefrom_pipeline.database.lineage import persist_library_lineage
from routefrom_pipeline.database.postgres import persist_processed_trace
from routefrom_pipeline.ingest.export_csv import write_linggan_csv
from routefrom_pipeline.pipeline import process_trace

if TYPE_CHECKING:
    import psycopg


class LeaseLostError(RuntimeError):
    pass


def private_path(root: Path, key: str) -> Path:
    if not re.fullmatch(r"(?:raw|generated)/[0-9a-f-]{36}/[0-9a-f-]{36}\.csv", key):
        raise ValueError("invalid private source key")
    root = root.resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise ValueError("source escapes the private data directory")
    return target


def claim_job(connection: psycopg.Connection, *, library_id=None):
    """Lock the library as well as the job to serialize concurrent claimers."""
    token = uuid4()
    with connection.transaction():
        row = connection.execute(
            """SELECT job.id, job.library_id, job.user_id, job.dataset_id,
                      job.kind, job.resolutions
               FROM app.library_jobs AS job
               JOIN app.footprint_libraries AS library ON library.id = job.library_id
               WHERE (job.status = 'queued' OR (job.status = 'running' AND job.lease_until < now()))
               AND (%s::uuid IS NULL OR job.library_id = %s)
               AND NOT EXISTS (
                 SELECT 1 FROM app.library_jobs AS running
                 WHERE running.library_id = job.library_id AND running.status = 'running'
                   AND running.id <> job.id)
               ORDER BY job.created_at, job.id
               FOR UPDATE OF library, job SKIP LOCKED LIMIT 1""",
            (library_id, library_id),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """UPDATE app.library_jobs SET status = 'running', worker_token = %s,
                 lease_until = now() + interval '15 minutes', attempts = attempts + 1,
                 stage = 'validate_source', progress = 5, error_message = NULL, updated_at = now()
               WHERE id = %s""", (token, row[0]),
        )
    return (*row, token)


def progress(connection: psycopg.Connection, job, stage: str, amount: int) -> None:
    with connection.transaction():
        found = connection.execute(
            """UPDATE app.library_jobs SET stage = %s, progress = %s,
                 updated_at = now(), lease_until = now() + interval '15 minutes'
               WHERE id = %s AND worker_token = %s AND status = 'running'
                 AND lease_until > now() RETURNING id""", (stage, amount, job[0], job[-1]),
        ).fetchone()
        if not found:
            raise LeaseLostError("job lease was replaced or expired")


@contextmanager
def heartbeat(connection: psycopg.Connection, job, *, enabled: bool):
    stop = threading.Event()

    def renew():
        import psycopg

        try:
            with psycopg.connect(connection.info.dsn, autocommit=True, connect_timeout=15) as monitor:
                while not stop.wait(30):
                    found = monitor.execute(
                        """UPDATE app.library_jobs SET lease_until = now() + interval '15 minutes',
                             updated_at = now() WHERE id = %s AND worker_token = %s
                             AND status = 'running' AND lease_until > now() RETURNING id""",
                        (job[0], job[-1]),
                    ).fetchone()
                    if not found:
                        break
        except psycopg.Error:
            # Fenced progress/publication checks still prevent stale publication.
            return

    thread = threading.Thread(target=renew, daemon=True) if enabled else None
    if thread:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join(timeout=1)


def process_job(connection: psycopg.Connection, job, root: Path) -> None:
    job_id, library_id, user_id, dataset_id, kind, resolutions, token = job
    metrics: dict[str, object] = {}
    if kind == "import":
        with connection.transaction():
            source = connection.execute(
                """SELECT source_object_key, source_storage FROM app.datasets
                   WHERE id = %s AND user_id = %s""", (dataset_id, user_id),
            ).fetchone()
        if not source or source[1] != "local":
            raise ValueError("registered private source was not found")
        source_path = private_path(root, source[0])
        imported = import_linggan_csv(connection, dataset_id=dataset_id, source_path=source_path)
        with connection.transaction():
            connection.execute(
                "SELECT id FROM app.footprint_libraries WHERE id = %s FOR UPDATE", (library_id,),
            )
            progress(connection, job, "attach_source", 20)
            attached = attach_library_import(connection, user_id=user_id, dataset_id=dataset_id,
                                             dataset_import_id=imported.dataset_import_id,
                                             source_path=source_path)
        metrics.update(source_rows=attached.source_row_count,
                       added_observations=attached.added_observation_count,
                       duplicate_rows=attached.duplicate_row_count)
    progress(connection, job, "merge_library", 30)
    revision, snapshot = load_library_snapshot(connection, user_id=user_id, library_id=library_id)
    with connection.transaction():
        previous = connection.execute(
            """SELECT snapshot.decisions FROM app.footprint_libraries AS library
               JOIN app.library_snapshots AS snapshot ON snapshot.id = library.active_snapshot_id
               WHERE library.id = %s AND library.user_id = %s""", (library_id, user_id),
        ).fetchone()
    resolutions = {**(previous[0] if previous else {}), **resolutions}
    candidates = {observation.fingerprint: observation for observation in snapshot.observations}
    decisions: dict[int, str] = {}
    remaining = []
    for conflict in snapshot.conflicts:
        requested = resolutions.get(str(conflict.epoch_ms))
        if requested in conflict.fingerprints:
            decisions[conflict.epoch_ms] = requested
        else:
            remaining.append({
                "epochMs": conflict.epoch_ms,
                "candidates": [{
                    "fingerprint": key,
                    "sourceId": candidates[key].source_id,
                    "longitude": candidates[key].point.wgs_longitude,
                    "latitude": candidates[key].point.wgs_latitude,
                    "accuracyMeters": candidates[key].point.horizontal_accuracy_meters,
                } for key in conflict.fingerprints],
            })
    metrics.update(conflict_count=len(snapshot.conflicts), remaining_conflict_count=len(remaining))
    if remaining:
        metrics["conflicts"] = remaining[:200]
        with connection.transaction():
            changed = connection.execute(
                """UPDATE app.library_jobs SET status = 'awaiting_resolution', stage = 'resolve_conflicts',
                     progress = 35, metrics = %s::jsonb, worker_token = NULL, lease_until = NULL,
                     updated_at = now() WHERE id = %s AND worker_token = %s AND status = 'running'
                     AND lease_until > now() RETURNING id""",
                (json.dumps(metrics), job_id, token),
            ).fetchone()
            if not changed:
                raise LeaseLostError("job lease was replaced")
        return
    points = snapshot.processing_points(decisions)
    run_id = merged_dataset = None
    if points:
        key = f"generated/{library_id}/{uuid4()}.csv"
        merged_path = private_path(root, key)
        merged_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(merged_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            write_linggan_csv(handle, points)
        with merged_path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        with connection.transaction():
            merged_dataset = connection.execute(
                """INSERT INTO app.datasets (user_id, original_filename, source_object_key,
                     source_sha256, source_size_bytes)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, source_sha256) DO UPDATE SET source_sha256 = EXCLUDED.source_sha256
                   RETURNING id""",
                (user_id, f"library-{revision}.csv", key, digest, merged_path.stat().st_size),
            ).fetchone()[0]
        canonical = import_linggan_csv(connection, dataset_id=merged_dataset, source_path=merged_path)
        progress(connection, job, "run_algorithms", 45)
        trace = process_trace(points)
        progress(connection, job, "persist_results", 80)
        result = persist_processed_trace(
            connection, dataset_id=merged_dataset, dataset_import_id=canonical.dataset_import_id,
            input_sha256=canonical.profile.sha256, trace=trace, activate=False,
            parameters={"library_id": str(library_id), "library_revision": revision,
                        "content_sha256": snapshot.content_sha256, "decisions": decisions},
        )
        run_id = result.processing_run_id
        persist_library_lineage(
            connection, library_id=library_id, user_id=user_id, processing_run_id=run_id,
            dataset_id=merged_dataset, dataset_import_id=canonical.dataset_import_id,
            observations=snapshot.processing_observations(decisions),
        )
    progress(connection, job, "publish", 95)
    with connection.transaction():
        current = connection.execute(
            "SELECT revision FROM app.footprint_libraries WHERE id = %s AND user_id = %s FOR UPDATE",
            (library_id, user_id),
        ).fetchone()
        owned = connection.execute(
            """SELECT id FROM app.library_jobs WHERE id = %s AND worker_token = %s
               AND status = 'running' AND lease_until > now() FOR UPDATE""", (job_id, token),
        ).fetchone()
        if not owned:
            raise LeaseLostError("job lease was replaced")
        fresh = current is not None and current[0] == revision
        snapshot_id = uuid4()
        # Supersede before insertion so there is at most one active snapshot.
        if fresh:
            connection.execute(
                "UPDATE app.library_snapshots SET status = 'superseded' WHERE library_id = %s AND status = 'active'",
                (library_id,),
            )
        connection.execute(
            """INSERT INTO app.library_snapshots (id, library_id, user_id, job_id, revision,
                 content_sha256, decisions, dataset_id, processing_run_id, status, point_count, published_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                       CASE WHEN %s THEN now() ELSE NULL END)""",
            (snapshot_id, library_id, user_id, job_id, revision, snapshot.content_sha256,
             json.dumps(resolutions), merged_dataset, run_id, "active" if fresh else "superseded",
             len(points), fresh),
        )
        if fresh:
            if run_id:
                connection.execute("SELECT app.activate_processing_run(%s)", (run_id,))
                connection.execute("UPDATE app.datasets SET status = 'ready' WHERE id = %s", (merged_dataset,))
            connection.execute(
                "UPDATE app.footprint_libraries SET active_snapshot_id = %s, updated_at = now() WHERE id = %s",
                (snapshot_id, library_id),
            )
        metrics.update(point_count=len(points), library_revision=revision,
                       snapshot_id=str(snapshot_id))
        connection.execute(
            """UPDATE app.library_jobs SET status = %s, stage = 'complete', progress = 100,
                 metrics = %s::jsonb, completed_at = now(), updated_at = now(), lease_until = NULL
               WHERE id = %s AND worker_token = %s""",
            ("succeeded" if fresh else "superseded", json.dumps(metrics), job_id, token),
        )


def run_once(connection: psycopg.Connection, root: Path, *, renew_lease: bool = False,
             library_id=None) -> bool:
    job = claim_job(connection, library_id=library_id)
    if job is None:
        return False
    try:
        with heartbeat(connection, job, enabled=renew_lease):
            process_job(connection, job, root)
    except LeaseLostError:
        pass
    except Exception as error:  # noqa: BLE001 -- isolate failures at the durable job boundary
        with connection.transaction():
            connection.execute(
                """UPDATE app.library_jobs SET status = 'failed', stage = 'failed',
                     error_message = %s, completed_at = now(), updated_at = now(), lease_until = NULL
                   WHERE id = %s AND worker_token = %s AND status = 'running'""",
                (str(error)[:1000], job[0], job[-1]),
            )
    return True


def main() -> int:
    import psycopg

    parser = argparse.ArgumentParser(description="Process the durable RouteFrom library queue.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--database-name")
    parser.add_argument("--library-id", help="optionally drain only this library's jobs")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("FOOTPRINT_DATA_DIR") or "../data"))
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL_DIRECT")
    if not url:
        parser.error("DATABASE_URL_DIRECT is required")
    options = {"dbname": args.database_name} if args.database_name else {}
    with psycopg.connect(url, connect_timeout=15, **options) as connection:
        while True:
            worked = run_once(connection, args.data_dir, renew_lease=True, library_id=args.library_id)
            if args.once:
                return 0
            if not worked:
                time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
