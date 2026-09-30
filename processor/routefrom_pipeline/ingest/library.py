"""Deterministic, lossless multi-import observation reconciliation.

Import identity and CSV line numbers are provenance, never observation identity.
Conflicting timestamps are retained and must be resolved before processing.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from routefrom_pipeline.model import NormalizedLocationPoint

IDENTITY_VERSION = "linggan-observation-v1"
_SOURCE_FIELDS = (
    "geo_time_epoch_ms", "day_time_epoch_ms", "source_latitude", "source_longitude",
    "wgs_latitude", "wgs_longitude", "source_altitude", "source_course",
    "source_horizontal_accuracy", "source_vertical_accuracy", "source_speed",
    "network_type", "network_name", "location_type",
)


def observation_fingerprint(point: NormalizedLocationPoint, source_id: str) -> str:
    """Exact source payload identity, scoped to a recording source/device.

    No spatial rounding: nearby points or repeated visits are not duplicates.
    Raw sensor values participate so revised readings remain inspectable.
    """
    if not source_id.strip():
        raise ValueError("source_id is required")
    values = []
    for field in _SOURCE_FIELDS:
        value = getattr(point, field)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"non-finite observation field: {field}")
            # Normalize signed zero without reducing numeric precision.
            value = 0.0 if value == 0 else value
        values.append(value)
    encoded = json.dumps(
        [IDENTITY_VERSION, source_id, values], ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class ObservationOrigin:
    import_id: str
    source_row_number: int


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    import_id: str
    source_id: str
    points: tuple[NormalizedLocationPoint, ...]


@dataclass(frozen=True, slots=True)
class LibraryObservation:
    fingerprint: str
    source_id: str
    point: NormalizedLocationPoint
    origins: tuple[ObservationOrigin, ...]


@dataclass(frozen=True, slots=True)
class TimestampConflict:
    epoch_ms: int
    fingerprints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LibrarySnapshot:
    observations: tuple[LibraryObservation, ...]
    conflicts: tuple[TimestampConflict, ...]
    input_row_count: int
    content_sha256: str

    @property
    def duplicate_row_count(self) -> int:
        return self.input_row_count - len(self.observations)

    def processing_points(
        self, resolutions: Mapping[int, str] | None = None,
    ) -> tuple[NormalizedLocationPoint, ...]:
        """Build globally numbered pipeline input after explicit conflict decisions.

        Resolutions select an existing fingerprint; originals and their provenance
        remain in this snapshot. Never silently omit an ambiguous timestamp or
        choose whichever file happened to be uploaded last.
        """
        decisions = resolutions or {}
        required = {conflict.epoch_ms: conflict for conflict in self.conflicts}
        if set(decisions) != set(required):
            raise ValueError("provide exactly one resolution for every conflicting timestamp")
        for epoch_ms, fingerprint in decisions.items():
            if fingerprint not in required[epoch_ms].fingerprints:
                raise ValueError("resolution must reference an observation at that timestamp")
        selected = (
            observation for observation in self.observations
            if observation.point.geo_time_epoch_ms not in required
            or decisions[observation.point.geo_time_epoch_ms] == observation.fingerprint
        )
        return tuple(
            replace(observation.point, source_row_number=index)
            for index, observation in enumerate(selected, start=2)
        )


def reconcile_imports(batches: Iterable[ObservationBatch]) -> LibrarySnapshot:
    """Rebuild a library snapshot from active imports (also supports withdrawal).

    Stable content hash excludes filenames, upload order and CSV row order.
    Tenant isolation must be enforced by the caller before supplying batches.
    """
    rows: dict[str, tuple[str, NormalizedLocationPoint]] = {}
    origins: dict[str, set[ObservationOrigin]] = defaultdict(set)
    seen_imports: set[str] = set()
    input_count = 0
    for batch in batches:
        if not batch.import_id.strip() or batch.import_id in seen_imports:
            raise ValueError("each batch requires a unique, nonempty import_id")
        if not batch.source_id.strip():
            raise ValueError("source_id is required")
        seen_imports.add(batch.import_id)
        source_rows: set[int] = set()
        for point in batch.points:
            if point.source_row_number < 2 or point.source_row_number in source_rows:
                raise ValueError("CSV source row numbers must be unique and start at 2")
            source_rows.add(point.source_row_number)
            if point.recorded_at != datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                milliseconds=point.geo_time_epoch_ms
            ):
                raise ValueError("recorded_at does not match source timestamp")
            fingerprint = observation_fingerprint(point, batch.source_id)
            rows.setdefault(fingerprint, (batch.source_id, point))
            origins[fingerprint].add(ObservationOrigin(batch.import_id, point.source_row_number))
            input_count += 1
    ordered = sorted(rows, key=lambda key: (rows[key][1].geo_time_epoch_ms, key))
    observations = tuple(
        LibraryObservation(key, rows[key][0], replace(rows[key][1], source_row_number=0),
                           tuple(sorted(origins[key])))
        for key in ordered
    )
    by_timestamp: dict[int, list[str]] = defaultdict(list)
    for observation in observations:
        by_timestamp[observation.point.geo_time_epoch_ms].append(observation.fingerprint)
    conflicts = tuple(
        TimestampConflict(epoch_ms, tuple(keys))
        for epoch_ms, keys in by_timestamp.items() if len(keys) > 1
    )
    digest = hashlib.sha256(
        (IDENTITY_VERSION + "\n" + "\n".join(ordered)).encode("ascii")
    ).hexdigest()
    return LibrarySnapshot(observations, conflicts, input_count, digest)
