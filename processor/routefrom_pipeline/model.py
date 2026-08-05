from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class QualityStatus(StrEnum):
    VALID = "valid"
    SUSPECT = "suspect"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class NormalizedLocationPoint:
    source_row_number: int
    geo_time_epoch_ms: int
    recorded_at: datetime
    day_time_epoch_ms: int
    source_latitude: float
    source_longitude: float
    wgs_latitude: float
    wgs_longitude: float
    source_altitude: float | None
    altitude_meters: float | None
    source_course: float | None
    course_degrees: float | None
    source_horizontal_accuracy: float | None
    horizontal_accuracy_meters: float | None
    source_vertical_accuracy: float | None
    vertical_accuracy_meters: float | None
    source_speed: float | None
    recorded_speed_mps: float | None
    network_type: int | None
    network_name: str | None
    location_type: int | None
    normalization_flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CsvProfile:
    sha256: str
    row_count: int
    first_recorded_at: datetime | None
    last_recorded_at: datetime | None
    timestamps_strictly_increasing: bool
    duplicate_timestamp_count: int
    sentinel_counts: dict[str, int]
