"""Verified immutable pipeline input links, committed before snapshot publication."""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from routefrom_pipeline.ingest.library import IDENTITY_VERSION, LibraryObservation

if TYPE_CHECKING:
    import psycopg


def persist_library_lineage(connection: psycopg.Connection, *, library_id, user_id,
                            processing_run_id, dataset_id, dataset_import_id,
                            observations: Sequence[LibraryObservation]) -> None:
    if not observations:
        raise ValueError("nonempty processing input required")
    with connection.transaction():
        imported = connection.execute(
            "SELECT parser_version FROM app.dataset_imports WHERE id = %s AND dataset_id = %s",
            (dataset_import_id, dataset_id),
        ).fetchone()
        if not imported or imported[0] not in ("linggan-csv-v1", "linggan-csv-v2"):
            raise ValueError("unsupported generated CSV parser for exact lineage")
        # v1 wrote WKT with 12 significant digits. Do not mutate immutable old
        # rows; verify against that recorded parser transform. Observation IDs
        # still come from full-precision fingerprints, never spatial rounding.
        legacy = imported[0] == "linggan-csv-v1"
        connection.execute(
            """INSERT INTO app.library_processing_inputs
               (processing_run_id, dataset_import_id, dataset_id, library_id, user_id, point_count)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (processing_run_id, dataset_import_id, dataset_id, library_id, user_id, len(observations)),
        )
        count = 0
        for offset in range(0, len(observations), 2000):
            batch = json.dumps([
                {"row_number": offset + index + 2, "source_id": item.source_id,
                 "fingerprint": item.fingerprint,
                 "longitude": float(format(item.point.wgs_longitude, ".12g")) if legacy else item.point.wgs_longitude,
                 "latitude": float(format(item.point.wgs_latitude, ".12g")) if legacy else item.point.wgs_latitude}
                for index, item in enumerate(observations[offset:offset + 2000])
            ], separators=(",", ":"))
            cursor = connection.execute(
                """INSERT INTO app.library_run_observations
                   (processing_run_id, dataset_import_id, dataset_id, library_id,
                    source_id, observation_id, point_id)
                   SELECT %s, %s, %s, %s, observation.source_id, observation.id, point.id
                   FROM jsonb_to_recordset(%s::jsonb)
                     AS incoming(row_number integer, source_id uuid, fingerprint text, longitude double precision, latitude double precision)
                   JOIN app.library_observations AS observation ON observation.library_id = %s
                     AND observation.source_id = incoming.source_id
                     AND observation.identity_version = %s AND observation.fingerprint = incoming.fingerprint
                   JOIN app.location_points AS point ON point.dataset_import_id = %s
                     AND point.dataset_id = %s AND point.source_row_number = incoming.row_number
                   WHERE observation.geo_time_epoch_ms = point.geo_time_epoch_ms
                     AND observation.payload->'day_time_epoch_ms' = to_jsonb(point.day_time_epoch_ms)
                     AND (observation.payload->>'source_latitude')::double precision = point.source_latitude
                     AND (observation.payload->>'source_longitude')::double precision = point.source_longitude
                     AND incoming.latitude = ST_Y(point.position)
                     AND incoming.longitude = ST_X(point.position)
                     AND (observation.payload->>'source_altitude')::double precision IS NOT DISTINCT FROM point.source_altitude
                     AND (observation.payload->>'source_course')::double precision IS NOT DISTINCT FROM point.source_course
                     AND (observation.payload->>'source_horizontal_accuracy')::double precision IS NOT DISTINCT FROM point.source_horizontal_accuracy
                     AND (observation.payload->>'source_vertical_accuracy')::double precision IS NOT DISTINCT FROM point.source_vertical_accuracy
                     AND (observation.payload->>'source_speed')::double precision IS NOT DISTINCT FROM point.source_speed
                     AND observation.payload->'network_type' = COALESCE(to_jsonb(point.network_type), 'null'::jsonb)
                     AND observation.payload->'network_name' = COALESCE(to_jsonb(point.network_name), 'null'::jsonb)
                     AND observation.payload->'location_type' = COALESCE(to_jsonb(point.location_type), 'null'::jsonb)""",
                (processing_run_id, dataset_import_id, dataset_id, library_id, batch,
                 library_id, IDENTITY_VERSION, dataset_import_id, dataset_id),
            )
            count += cursor.rowcount
        if count != len(observations):
            raise ValueError("processing input lineage is incomplete or generated CSV differs from source")
        row_count = connection.execute(
            "SELECT count(*) FROM app.location_points WHERE dataset_import_id = %s AND dataset_id = %s",
            (dataset_import_id, dataset_id),
        ).fetchone()[0]
        if row_count != count:
            raise ValueError("generated CSV row count does not match processing input lineage")
