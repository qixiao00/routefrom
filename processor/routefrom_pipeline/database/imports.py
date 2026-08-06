from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from routefrom_pipeline.ingest.linggan import (
    EXPECTED_COLUMNS,
    iter_linggan_csv,
    profile_linggan_csv,
)
from routefrom_pipeline.model import CsvProfile, NormalizedLocationPoint

PARSER_NAME = "linggan_footprint_csv"
PARSER_VERSION = "linggan-csv-v1"
SCHEMA_FINGERPRINT = hashlib.sha256("\x1f".join(EXPECTED_COLUMNS).encode()).hexdigest()


class Cursor(Protocol):
    def execute(self, query: str, params: Sequence[object] = ()) -> Any: ...
    def executemany(self, query: str, params_seq: Iterable[Sequence[object]]) -> Any: ...
    def fetchone(self) -> Sequence[object] | None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def transaction(self) -> Any: ...


class DatasetImportConflictError(RuntimeError):
    """The source already has an import that cannot be safely reused."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    dataset_import_id: UUID
    profile: CsvProfile
    inserted_point_count: int
    reused: bool


def _point_wkt(point: NormalizedLocationPoint) -> str:
    return f"POINT({point.wgs_longitude:.12g} {point.wgs_latitude:.12g})"


def _point_row(
    dataset_id: UUID,
    dataset_import_id: UUID,
    point: NormalizedLocationPoint,
    timezone_name: str,
    timezone: ZoneInfo,
) -> tuple[object, ...]:
    return (
        dataset_id,
        dataset_import_id,
        point.source_row_number,
        point.recorded_at,
        point.recorded_at.astimezone(timezone).date(),
        timezone_name,
        _point_wkt(point),
        point.source_latitude,
        point.source_longitude,
        point.altitude_meters,
        point.recorded_speed_mps,
        point.course_degrees,
        point.horizontal_accuracy_meters,
        point.vertical_accuracy_meters,
        point.network_type,
        point.location_type,
        point.geo_time_epoch_ms,
        point.day_time_epoch_ms,
        point.source_latitude,
        point.source_longitude,
        point.source_altitude,
        point.source_course,
        point.source_horizontal_accuracy,
        point.source_vertical_accuracy,
        point.source_speed,
        point.network_name,
        list(point.normalization_flags),
    )


_INSERT_LOCATION_POINTS = """
INSERT INTO app.location_points (
  dataset_id, dataset_import_id, source_row_number, recorded_at, local_date,
  timezone, position, raw_latitude, raw_longitude, altitude_meters,
  recorded_speed_mps, course_degrees, horizontal_accuracy_meters,
  vertical_accuracy_meters, network_type, location_type, geo_time_epoch_ms,
  day_time_epoch_ms, source_latitude, source_longitude, source_altitude,
  source_course, source_horizontal_accuracy, source_vertical_accuracy,
  source_speed, network_name, normalization_flags
) VALUES (
  %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s,
  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
"""


def _profile_metrics(profile: CsvProfile) -> str:
    return json.dumps(
        {
            "duplicate_timestamp_count": profile.duplicate_timestamp_count,
            "first_recorded_at": (
                profile.first_recorded_at.isoformat() if profile.first_recorded_at else None
            ),
            "last_recorded_at": (
                profile.last_recorded_at.isoformat() if profile.last_recorded_at else None
            ),
            "sentinel_counts": profile.sentinel_counts,
            "timestamps_strictly_increasing": profile.timestamps_strictly_increasing,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def import_linggan_csv(
    connection: Connection,
    *,
    dataset_id: UUID,
    source_path: str | Path,
    source_object_key: str | None = None,
    timezone: str = "Asia/Shanghai",
    batch_size: int = 2_000,
) -> ImportResult:
    """Validate and append one immutable Linggan CSV import atomically.

    The source is profiled before opening a database transaction. Point rows,
    import metadata, and dataset summary fields then commit together. A repeated
    successful import of the same bytes returns the existing import without
    duplicating observations.
    """

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    try:
        timezone_info = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc

    csv_path = Path(source_path)
    profile = profile_linggan_csv(csv_path)
    source_size_bytes = csv_path.stat().st_size
    source_sha256 = profile.sha256.lower()
    dataset_import_id = uuid4()
    inserted_count = 0

    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT source_sha256, source_size_bytes, source_object_key
            FROM app.datasets
            WHERE id = %s
            FOR UPDATE
            """,
            (dataset_id,),
        )
        dataset = cursor.fetchone()
        if dataset is None:
            raise ValueError(f"dataset does not exist: {dataset_id}")
        if str(dataset[0]).lower() != source_sha256:
            raise ValueError("CSV sha256 does not match the registered dataset source")
        if int(dataset[1]) != source_size_bytes:
            raise ValueError("CSV size does not match the registered dataset source")
        registered_object_key = str(dataset[2])

        cursor.execute(
            """
            SELECT id, status, row_count, rejected_row_count
            FROM app.dataset_imports
            WHERE dataset_id = %s AND source_sha256 = %s
            """,
            (dataset_id, source_sha256),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if str(existing[1]) != "succeeded":
                raise DatasetImportConflictError(
                    f"source already has a non-reusable {existing[1]} import"
                )
            if int(existing[2]) != profile.row_count or int(existing[3]) != 0:
                raise DatasetImportConflictError(
                    "existing successful import metadata does not match the source profile"
                )
            return ImportResult(UUID(str(existing[0])), profile, 0, True)

        cursor.execute(
            "SELECT COALESCE(max(import_number), 0) FROM app.dataset_imports WHERE dataset_id = %s",
            (dataset_id,),
        )
        import_number_row = cursor.fetchone()
        import_number = int(import_number_row[0] if import_number_row is not None else 0) + 1
        cursor.execute(
            """
            INSERT INTO app.dataset_imports (
              id, dataset_id, import_number, parser_name, parser_version,
              source_sha256, source_object_key, source_size_bytes,
              schema_fingerprint, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'running')
            """,
            (
                dataset_import_id,
                dataset_id,
                import_number,
                PARSER_NAME,
                PARSER_VERSION,
                source_sha256,
                source_object_key or registered_object_key,
                source_size_bytes,
                SCHEMA_FINGERPRINT,
            ),
        )

        batch: list[tuple[object, ...]] = []
        for point in iter_linggan_csv(csv_path):
            batch.append(_point_row(dataset_id, dataset_import_id, point, timezone, timezone_info))
            if len(batch) >= batch_size:
                cursor.executemany(_INSERT_LOCATION_POINTS, batch)
                inserted_count += len(batch)
                batch.clear()
        if batch:
            cursor.executemany(_INSERT_LOCATION_POINTS, batch)
            inserted_count += len(batch)
        if inserted_count != profile.row_count:
            raise RuntimeError("CSV changed between profiling and database import")

        cursor.execute(
            """
            UPDATE app.dataset_imports
            SET row_count = %s, rejected_row_count = 0, status = 'succeeded',
                metrics = %s::jsonb, completed_at = now()
            WHERE id = %s
            """,
            (inserted_count, _profile_metrics(profile), dataset_import_id),
        )
        cursor.execute(
            """
            UPDATE app.datasets
            SET status = 'processing', point_count = %s, rejected_point_count = 0,
                recorded_from = %s, recorded_to = %s, parser_version = %s,
                failure_message = NULL
            WHERE id = %s
            """,
            (
                inserted_count,
                profile.first_recorded_at,
                profile.last_recorded_at,
                PARSER_VERSION,
                dataset_id,
            ),
        )

    return ImportResult(dataset_import_id, profile, inserted_count, False)
