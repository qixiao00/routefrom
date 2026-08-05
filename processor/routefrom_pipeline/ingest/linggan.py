from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from routefrom_pipeline.model import CsvProfile, NormalizedLocationPoint

EXPECTED_COLUMNS = (
    "geoTime",
    "latitude",
    "longitude",
    "wgsLatitude",
    "wgsLongitude",
    "altitude",
    "course",
    "horizontalAccuracy",
    "verticalAccuracy",
    "speed",
    "dayTimeMills",
    "networkType",
    "networkName",
    "locationType",
)

SENTINEL_FIELDS = {
    "course": "course_missing_sentinel",
    "horizontalAccuracy": "horizontal_accuracy_missing_sentinel",
    "verticalAccuracy": "vertical_accuracy_missing_sentinel",
    "speed": "speed_missing_sentinel",
}


class LingganCsvError(ValueError):
    """A structural error that prevents a Linggan CSV row from being imported."""


def _required_float(row: dict[str, str], field: str, row_number: int) -> float:
    value = row.get(field, "").strip()
    if not value:
        raise LingganCsvError(f"row {row_number}: {field} is required")
    try:
        return float(value)
    except ValueError as exc:
        raise LingganCsvError(f"row {row_number}: {field} is not numeric") from exc


def _optional_float(row: dict[str, str], field: str, row_number: int) -> float | None:
    value = row.get(field, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise LingganCsvError(f"row {row_number}: {field} is not numeric") from exc


def _optional_int(row: dict[str, str], field: str, row_number: int) -> int | None:
    value = row.get(field, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise LingganCsvError(f"row {row_number}: {field} is not an integer") from exc


def _required_int(row: dict[str, str], field: str, row_number: int) -> int:
    value = _optional_int(row, field, row_number)
    if value is None:
        raise LingganCsvError(f"row {row_number}: {field} is required")
    return value


def _normalize_missing_sentinel(
    value: float | None,
    *,
    field: str,
    flags: list[str],
) -> float | None:
    if value == -1:
        flags.append(SENTINEL_FIELDS[field])
        return None
    return value


def _validate_coordinate(value: float, field: str, row_number: int, limit: float) -> None:
    if not -limit <= value <= limit:
        raise LingganCsvError(f"row {row_number}: {field} is outside [-{limit}, {limit}]")


def _parse_row(row: dict[str, str], row_number: int) -> NormalizedLocationPoint:
    flags: list[str] = []
    geo_time_ms = _required_int(row, "geoTime", row_number)
    day_time_ms = _required_int(row, "dayTimeMills", row_number)

    source_latitude = _required_float(row, "latitude", row_number)
    source_longitude = _required_float(row, "longitude", row_number)
    wgs_latitude = _required_float(row, "wgsLatitude", row_number)
    wgs_longitude = _required_float(row, "wgsLongitude", row_number)
    _validate_coordinate(source_latitude, "latitude", row_number, 90)
    _validate_coordinate(source_longitude, "longitude", row_number, 180)
    _validate_coordinate(wgs_latitude, "wgsLatitude", row_number, 90)
    _validate_coordinate(wgs_longitude, "wgsLongitude", row_number, 180)

    altitude = _optional_float(row, "altitude", row_number)
    course = _optional_float(row, "course", row_number)
    horizontal_accuracy = _optional_float(row, "horizontalAccuracy", row_number)
    vertical_accuracy = _optional_float(row, "verticalAccuracy", row_number)
    speed = _optional_float(row, "speed", row_number)

    normalized_course = _normalize_missing_sentinel(
        course, field="course", flags=flags
    )
    normalized_horizontal_accuracy = _normalize_missing_sentinel(
        horizontal_accuracy, field="horizontalAccuracy", flags=flags
    )
    normalized_vertical_accuracy = _normalize_missing_sentinel(
        vertical_accuracy, field="verticalAccuracy", flags=flags
    )
    normalized_speed = _normalize_missing_sentinel(speed, field="speed", flags=flags)

    if normalized_course is not None and not 0 <= normalized_course <= 360:
        flags.append("course_out_of_range")
        normalized_course = None
    if normalized_horizontal_accuracy is not None and normalized_horizontal_accuracy < 0:
        flags.append("horizontal_accuracy_out_of_range")
        normalized_horizontal_accuracy = None
    if normalized_vertical_accuracy is not None and normalized_vertical_accuracy < 0:
        flags.append("vertical_accuracy_out_of_range")
        normalized_vertical_accuracy = None
    if normalized_speed is not None and normalized_speed < 0:
        flags.append("speed_out_of_range")
        normalized_speed = None

    try:
        recorded_at = datetime.fromtimestamp(geo_time_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise LingganCsvError(f"row {row_number}: geoTime is outside datetime range") from exc

    network_name = row.get("networkName", "").strip() or None

    return NormalizedLocationPoint(
        source_row_number=row_number,
        geo_time_epoch_ms=geo_time_ms,
        recorded_at=recorded_at,
        day_time_epoch_ms=day_time_ms,
        source_latitude=source_latitude,
        source_longitude=source_longitude,
        wgs_latitude=wgs_latitude,
        wgs_longitude=wgs_longitude,
        source_altitude=altitude,
        altitude_meters=altitude,
        source_course=course,
        course_degrees=normalized_course,
        source_horizontal_accuracy=horizontal_accuracy,
        horizontal_accuracy_meters=normalized_horizontal_accuracy,
        source_vertical_accuracy=vertical_accuracy,
        vertical_accuracy_meters=normalized_vertical_accuracy,
        source_speed=speed,
        recorded_speed_mps=normalized_speed,
        network_type=_optional_int(row, "networkType", row_number),
        network_name=network_name,
        location_type=_optional_int(row, "locationType", row_number),
        normalization_flags=tuple(flags),
    )

def iter_linggan_csv(path: str | Path) -> Iterator[NormalizedLocationPoint]:
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise LingganCsvError("CSV has no header")
        if tuple(reader.fieldnames) != EXPECTED_COLUMNS:
            raise LingganCsvError(
                f"unexpected CSV columns: expected {EXPECTED_COLUMNS!r}, got {tuple(reader.fieldnames)!r}"
            )
        for source_row_number, row in enumerate(reader, start=2):
            yield _parse_row(row, source_row_number)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def profile_linggan_csv(path: str | Path) -> CsvProfile:
    csv_path = Path(path)
    row_count = 0
    first_recorded_at: datetime | None = None
    last_recorded_at: datetime | None = None
    previous_recorded_at: datetime | None = None
    strictly_increasing = True
    duplicate_timestamp_count = 0
    sentinel_counts = {field: 0 for field in SENTINEL_FIELDS}

    for point in iter_linggan_csv(csv_path):
        row_count += 1
        if first_recorded_at is None:
            first_recorded_at = point.recorded_at
        if previous_recorded_at is not None:
            if point.recorded_at <= previous_recorded_at:
                strictly_increasing = False
            if point.recorded_at == previous_recorded_at:
                duplicate_timestamp_count += 1
        previous_recorded_at = point.recorded_at
        last_recorded_at = point.recorded_at
        for flag in point.normalization_flags:
            for field, sentinel_flag in SENTINEL_FIELDS.items():
                if flag == sentinel_flag:
                    sentinel_counts[field] += 1

    return CsvProfile(
        sha256=_sha256(csv_path),
        row_count=row_count,
        first_recorded_at=first_recorded_at,
        last_recorded_at=last_recorded_at,
        timestamps_strictly_increasing=strictly_increasing,
        duplicate_timestamp_count=duplicate_timestamp_count,
        sentinel_counts=sentinel_counts,
    )
