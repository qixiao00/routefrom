"""Owner-scoped archive queue, review acknowledgement and clock-based fencing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid4


class ArchiveLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveJob:
    id: UUID
    library_id: UUID
    user_id: UUID
    kind: str
    input_key: str | None
    input_sha256: str | None
    input_size_bytes: int | None
    approved_state_hash: str | None
    token: UUID


def sync_processing_jobs(connection, library_id=None):
    with connection.transaction():
        return connection.execute(
            """UPDATE app.library_archive_jobs AS archive SET
          status=CASE WHEN processing.status='succeeded' THEN 'succeeded' ELSE 'failed' END,
          stage=CASE WHEN processing.status='succeeded' THEN 'complete' ELSE 'processing_failed' END,
          progress=CASE WHEN processing.status='succeeded' THEN 100 ELSE archive.progress END,
          error_code=CASE WHEN processing.status='succeeded' THEN NULL ELSE 'processing_failed' END,
          completed_at=clock_timestamp(), updated_at=clock_timestamp()
          FROM app.library_jobs AS processing
          WHERE archive.processing_job_id=processing.id AND archive.library_id=processing.library_id
            AND archive.user_id=processing.user_id AND archive.status='processing'
            AND processing.status IN ('succeeded','failed','superseded')
            AND (%s::uuid IS NULL OR archive.library_id=%s) RETURNING archive.id""",
            (library_id, library_id),
        ).fetchall()


def claim_archive_job(connection, library_id=None):
    with connection.transaction():
        row = connection.execute(
            """SELECT job.id,job.library_id,job.user_id,job.kind,
            job.input_key,job.input_sha256,job.input_size_bytes,job.approved_state_hash
          FROM app.library_archive_jobs AS job
          JOIN app.footprint_libraries AS library ON library.id=job.library_id AND library.user_id=job.user_id
          WHERE (job.status='queued' OR (job.status='running' AND job.lease_until<clock_timestamp()))
            AND (%s::uuid IS NULL OR job.library_id=%s)
            AND NOT EXISTS (SELECT 1 FROM app.library_jobs AS processing WHERE processing.library_id=job.library_id AND processing.status='running')
            AND NOT EXISTS (SELECT 1 FROM app.library_archive_jobs AS other WHERE other.library_id=job.library_id
              AND other.id<>job.id AND (other.status='running' OR (job.kind='restore' AND other.status='processing')))
          ORDER BY job.created_at,job.id FOR UPDATE OF library,job SKIP LOCKED LIMIT 1""",
            (library_id, library_id),
        ).fetchone()
        if row is None:
            return None
        # LockRows may acquire a just-released parent lock after the initial
        # statement snapshot. A fresh guard is necessary across the two tables.
        if connection.execute(
            """SELECT 1 WHERE
          EXISTS(SELECT 1 FROM app.library_jobs WHERE library_id=%s AND status='running')
          OR EXISTS(SELECT 1 FROM app.library_archive_jobs WHERE library_id=%s AND id<>%s
            AND (status='running' OR (%s='restore' AND status='processing')))""",
            (row[1], row[1], row[0], row[3]),
        ).fetchone():
            return None
        token = uuid4()
        connection.execute(
            """UPDATE app.library_archive_jobs SET status='running',worker_token=%s,
          lease_until=clock_timestamp()+interval '15 minutes',attempts=attempts+1,
          stage='validate_archive',progress=5,error_code=NULL,updated_at=clock_timestamp() WHERE id=%s""",
            (token, row[0]),
        )
        return ArchiveJob(*row, token)


def fence_archive_job(connection, job: ArchiveJob):
    row = connection.execute(
        """SELECT id FROM app.library_archive_jobs
      WHERE id=%s AND library_id=%s AND user_id=%s AND worker_token=%s
        AND status='running' AND lease_until>clock_timestamp() FOR UPDATE""",
        (job.id, job.library_id, job.user_id, job.token),
    ).fetchone()
    if row is None:
        raise ArchiveLeaseLost("archive lease was replaced or expired")


def archive_progress(connection, job: ArchiveJob, stage: str, progress: int):
    with connection.transaction():
        fence_archive_job(connection, job)
        connection.execute(
            """UPDATE app.library_archive_jobs SET stage=%s,progress=%s,
          lease_until=clock_timestamp()+interval '15 minutes',updated_at=clock_timestamp() WHERE id=%s""",
            (stage, progress, job.id),
        )


def request_restore_review(connection, job: ArchiveJob, plan):
    with connection.transaction():
        fence_archive_job(connection, job)
        connection.execute(
            """UPDATE app.library_archive_jobs SET status='awaiting_review',stage='review_restore',
          progress=20,plan=%s::jsonb,approved_state_hash=NULL,worker_token=NULL,lease_until=NULL,
          updated_at=clock_timestamp() WHERE id=%s""",
            (json.dumps(plan, ensure_ascii=False), job.id),
        )


def approve_restore(connection, *, job_id, library_id, user_id, state_hash):
    with connection.transaction():
        row = connection.execute(
            """UPDATE app.library_archive_jobs SET status='queued',stage='queued',
          approved_state_hash=%s,updated_at=clock_timestamp()
          WHERE id=%s AND library_id=%s AND user_id=%s AND kind='restore' AND status='awaiting_review'
            AND plan->>'stateHash'=%s RETURNING id""",
            (state_hash, job_id, library_id, user_id, state_hash),
        ).fetchone()
        if row is None:
            acknowledged = connection.execute(
                """SELECT id FROM app.library_archive_jobs
              WHERE id=%s AND library_id=%s AND user_id=%s AND kind='restore'
                AND approved_state_hash=%s AND status IN ('queued','running','processing','succeeded')""",
                (job_id, library_id, user_id, state_hash),
            ).fetchone()
            if not acknowledged:
                raise ValueError("restore review was changed or does not belong to this owner")


def fail_archive_job(connection, job: ArchiveJob, code: str):
    with connection.transaction():
        connection.execute(
            """UPDATE app.library_archive_jobs SET status='failed',stage='failed',error_code=%s,
          completed_at=clock_timestamp(),updated_at=clock_timestamp(),lease_until=NULL
          WHERE id=%s AND library_id=%s AND user_id=%s AND worker_token=%s AND status='running'
            AND lease_until>clock_timestamp()""",
            (code, job.id, job.library_id, job.user_id, job.token),
        )
