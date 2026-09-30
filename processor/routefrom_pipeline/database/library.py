"""Atomic per-user library membership and exact observation provenance writes."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from routefrom_pipeline.ingest.library import (
    IDENTITY_VERSION,
    LibrarySnapshot,
    ObservationBatch,
    observation_fingerprint,
    reconcile_imports,
)
from routefrom_pipeline.ingest.linggan import iter_linggan_csv, profile_linggan_csv
from routefrom_pipeline.model import NormalizedLocationPoint


class LibraryCursor(Protocol):
    def execute(self, query: str, params: Sequence[object] = ()) -> Any: ...
    def fetchone(self) -> Sequence[Any] | None: ...
    def fetchall(self) -> Sequence[Sequence[Any]]: ...


class LibraryConnection(Protocol):
    def cursor(self) -> LibraryCursor: ...
    def transaction(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class LibraryImportResult:
    library_id: UUID
    file_id: UUID
    revision: int
    source_row_count: int
    added_observation_count: int
    duplicate_row_count: int
    reused: bool


def _payload(point: NormalizedLocationPoint) -> dict[str, object]:
    result = dataclasses.asdict(point)
    result["source_row_number"] = 0
    result["recorded_at"] = point.recorded_at.isoformat()
    return result


def _incoming(points: Iterable[NormalizedLocationPoint], source_id: UUID) -> str:
    return json.dumps([
        {
            "fingerprint": observation_fingerprint(point, str(source_id)),
            "row_number": point.source_row_number,
            "epoch_ms": point.geo_time_epoch_ms,
            "recorded_at": point.recorded_at.isoformat(),
            "longitude": point.wgs_longitude,
            "latitude": point.wgs_latitude,
            "payload": _payload(point),
        } for point in points
    ], ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _required_row(cursor: LibraryCursor) -> Sequence[Any]:
    row = cursor.fetchone()
    if row is None:
        raise ValueError("library or import was not found for this user")
    return row


def attach_library_import(
    connection: LibraryConnection,
    *,
    user_id: UUID,
    dataset_id: UUID,
    dataset_import_id: UUID,
    source_path: str | Path,
    source_key: str = "linggan-primary",
    source_name: str = "灵敢足迹",
    batch_size: int = 2000,
) -> LibraryImportResult:
    """Attach a successfully parsed immutable dataset and preserve all its rows.

    All writers serialize on the per-user library row. CSV validation happens
    first; file membership, observations, provenance and revision commit once.
    Re-upload is idempotent. Withdrawn files can be reactivated explicitly here.
    """
    if batch_size < 1 or not 1 <= len(source_key) <= 128 or not source_key.strip():
        raise ValueError("invalid batch_size or source_key")
    profile = profile_linggan_csv(source_path)
    points = tuple(iter_linggan_csv(source_path))
    if not points:
        raise ValueError("source CSV contains no observations")
    with Path(source_path).open("rb") as source_handle:
        verified_hash = hashlib.file_digest(source_handle, "sha256").hexdigest()
    if profile.row_count != len(points) or verified_hash != profile.sha256.lower():
        raise ValueError("source changed during validation")
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(
            """SELECT import.source_sha256, import.row_count, import.source_size_bytes
               FROM app.dataset_imports AS import
               JOIN app.datasets AS dataset ON dataset.id = import.dataset_id
               WHERE import.id = %s AND import.dataset_id = %s
                 AND dataset.user_id = %s AND import.status = 'succeeded'""",
            (dataset_import_id, dataset_id, user_id),
        )
        raw = _required_row(cursor)
        if (str(raw[0]).lower() != profile.sha256.lower()
                or int(raw[1]) != len(points)
                or int(raw[2]) != Path(source_path).stat().st_size):
            raise ValueError("source does not match the immutable successful import")
        cursor.execute(
            """INSERT INTO app.footprint_libraries (user_id) VALUES (%s)
               ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
               RETURNING id, revision""", (user_id,),
        )
        library = _required_row(cursor)
        library_id, revision = UUID(str(library[0])), int(library[1])
        cursor.execute(
            """INSERT INTO app.library_sources (library_id, source_key, name)
               VALUES (%s, %s, %s)
               ON CONFLICT (library_id, source_key) DO UPDATE SET source_key = EXCLUDED.source_key
               RETURNING id""", (library_id, source_key, source_name),
        )
        source_id = UUID(str(_required_row(cursor)[0]))
        cursor.execute(
            "SELECT count(*) FROM app.active_library_observations WHERE library_id = %s",
            (library_id,),
        )
        before = int(_required_row(cursor)[0])
        cursor.execute(
            """SELECT id, source_id, status, dataset_import_id FROM app.library_files
               WHERE library_id = %s AND dataset_id = %s""", (library_id, dataset_id),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if UUID(str(existing[1])) != source_id or UUID(str(existing[3])) != dataset_import_id:
                raise ValueError("file already belongs to a different source or parser import")
            file_id = UUID(str(existing[0]))
            if str(existing[2]) == "active":
                return LibraryImportResult(library_id, file_id, revision, len(points), 0,
                                           len(points), True)
            cursor.execute(
                """UPDATE app.library_files SET status = 'active', withdrawn_at = NULL
                   WHERE id = %s AND library_id = %s""", (file_id, library_id),
            )
        else:
            cursor.execute(
                """INSERT INTO app.library_files
                   (library_id, user_id, source_id, dataset_id, dataset_import_id)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (library_id, user_id, source_id, dataset_id, dataset_import_id),
            )
            file_id = UUID(str(_required_row(cursor)[0]))
        origin_count = 0
        for offset in range(0, len(points), batch_size):
            incoming = _incoming(points[offset:offset + batch_size], source_id)
            cursor.execute(
                """INSERT INTO app.library_observations
                   (library_id, source_id, identity_version, fingerprint,
                    geo_time_epoch_ms, recorded_at, position, payload)
                   SELECT %s, %s, %s, fingerprint, epoch_ms, recorded_at,
                          ST_SetSRID(ST_MakePoint(longitude, latitude), 4326), payload
                   FROM jsonb_to_recordset(%s::jsonb) AS incoming
                     (fingerprint text, epoch_ms bigint, recorded_at timestamptz,
                      longitude double precision, latitude double precision, payload jsonb)
                   ON CONFLICT (library_id, source_id, identity_version, fingerprint) DO NOTHING""",
                (library_id, source_id, IDENTITY_VERSION, incoming),
            )
            cursor.execute(
                """WITH inserted AS (
                   INSERT INTO app.library_observation_origins
                     (library_id, source_id, observation_id, library_file_id,
                      dataset_id, dataset_import_id, source_point_id)
                   SELECT %s, %s, observation.id, %s, %s, %s, point.id
                   FROM jsonb_to_recordset(%s::jsonb) AS incoming
                     (fingerprint text, row_number integer)
                   JOIN app.library_observations AS observation
                     ON observation.library_id = %s AND observation.source_id = %s
                    AND observation.identity_version = %s
                    AND observation.fingerprint = incoming.fingerprint
                   JOIN app.location_points AS point
                     ON point.dataset_id = %s AND point.dataset_import_id = %s
                    AND point.source_row_number = incoming.row_number
                   ON CONFLICT DO NOTHING RETURNING 1)
                   SELECT count(*) FROM inserted""",
                (library_id, source_id, file_id, dataset_id, dataset_import_id,
                 incoming, library_id, source_id, IDENTITY_VERSION, dataset_id, dataset_import_id),
            )
            origin_count += int(_required_row(cursor)[0])
        # Re-activation already has its complete provenance; first attachment
        # must map every CSV row, including within-file duplicates.
        if existing is None and origin_count != len(points):
            raise ValueError("raw import is missing source rows; library attachment rolled back")
        cursor.execute(
            "SELECT count(*) FROM app.active_library_observations WHERE library_id = %s",
            (library_id,),
        )
        added = int(_required_row(cursor)[0]) - before
        cursor.execute(
            """UPDATE app.footprint_libraries SET revision = revision + 1, updated_at = now()
               WHERE id = %s RETURNING revision""", (library_id,),
        )
        revision = int(_required_row(cursor)[0])
        return LibraryImportResult(library_id, file_id, revision, len(points), added,
                                   len(points) - added, False)


def withdraw_library_file(
    connection: LibraryConnection, *, user_id: UUID, library_id: UUID, file_id: UUID,
) -> int:
    """Withdraw membership; shared raw points and provenance are never deleted."""
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(
            """SELECT revision FROM app.footprint_libraries
               WHERE id = %s AND user_id = %s FOR UPDATE""", (library_id, user_id),
        )
        revision = int(_required_row(cursor)[0])
        cursor.execute(
            "SELECT status FROM app.library_files WHERE id = %s AND library_id = %s",
            (file_id, library_id),
        )
        if str(_required_row(cursor)[0]) == "withdrawn":
            return revision
        cursor.execute(
            """UPDATE app.library_files SET status = 'withdrawn', withdrawn_at = now()
               WHERE id = %s AND library_id = %s""", (file_id, library_id),
        )
        cursor.execute(
            """UPDATE app.footprint_libraries SET revision = revision + 1, updated_at = now()
               WHERE id = %s RETURNING revision""", (library_id,),
        )
        return int(_required_row(cursor)[0])


def load_library_snapshot(
    connection: LibraryConnection, *, user_id: UUID, library_id: UUID,
) -> tuple[int, LibrarySnapshot]:
    """Read a consistent active manifest, serialize with membership writers."""
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(
            """SELECT revision FROM app.footprint_libraries
               WHERE id = %s AND user_id = %s FOR SHARE""", (library_id, user_id),
        )
        revision = int(_required_row(cursor)[0])
        cursor.execute(
            """SELECT file.id, file.source_id, point.source_row_number, observation.payload,
                      observation.fingerprint
               FROM app.library_files AS file
               JOIN app.library_observation_origins AS origin ON origin.library_file_id = file.id
               JOIN app.library_observations AS observation ON observation.id = origin.observation_id
               JOIN app.location_points AS point ON point.id = origin.source_point_id
               WHERE file.library_id = %s AND file.status = 'active'
               ORDER BY file.id, point.source_row_number""", (library_id,),
        )
        batches: dict[tuple[str, str], list[NormalizedLocationPoint]] = {}
        for file_id, source_id, row_number, payload, fingerprint in cursor.fetchall():
            values = dict(payload if isinstance(payload, dict) else json.loads(payload))
            values["recorded_at"] = datetime.fromisoformat(values["recorded_at"])
            values["source_row_number"] = int(row_number)
            values["normalization_flags"] = tuple(values["normalization_flags"])
            point = NormalizedLocationPoint(**values)
            if observation_fingerprint(point, str(source_id)) != str(fingerprint):
                raise ValueError("stored observation does not match its fingerprint")
            batches.setdefault((str(file_id), str(source_id)), []).append(point)
        snapshot = reconcile_imports(
            ObservationBatch(file_id, source_id, tuple(points))
            for (file_id, source_id), points in batches.items()
        )
        return revision, snapshot
