from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from routefrom_pipeline import process_trace
from routefrom_pipeline.database import persist_processed_trace
from routefrom_pipeline.model import NormalizedLocationPoint


def point(index: int, *, longitude: float) -> NormalizedLocationPoint:
    recorded_at = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(seconds=index * 10)
    return NormalizedLocationPoint(
        source_row_number=index + 2,
        geo_time_epoch_ms=int(recorded_at.timestamp() * 1000),
        recorded_at=recorded_at,
        day_time_epoch_ms=int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000),
        source_latitude=31.2,
        source_longitude=longitude,
        wgs_latitude=31.2,
        wgs_longitude=longitude,
        source_altitude=0,
        altitude_meters=0,
        source_course=None,
        course_degrees=None,
        source_horizontal_accuracy=8,
        horizontal_accuracy_meters=8,
        source_vertical_accuracy=None,
        vertical_accuracy_meters=None,
        source_speed=8,
        recorded_speed_mps=8,
        network_type=0,
        network_name=None,
        location_type=0,
        normalization_flags=(),
    )


class FakeCursor:
    def __init__(self, source_rows: list[int]) -> None:
        self.source_rows = source_rows
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.many: list[tuple[str, list[tuple[object, ...]]]] = []

    def execute(self, query: str, params: Any = ()) -> None:
        self.executions.append((query, tuple(params)))

    def executemany(self, query: str, params_seq: Any) -> None:
        self.many.append((query, [tuple(params) for params in params_seq]))

    def fetchall(self) -> list[tuple[int, int]]:
        return [(source_row, 10_000 + source_row) for source_row in self.source_rows]


class FakeConnection:
    def __init__(self, source_rows: list[int]) -> None:
        self.fake_cursor = FakeCursor(source_rows)

    def transaction(self) -> Any:
        return nullcontext()

    def cursor(self) -> FakeCursor:
        return self.fake_cursor


class DatabasePersistenceTests(unittest.TestCase):
    def test_complete_run_is_written_and_activated_in_one_transaction(self) -> None:
        points = [point(index, longitude=121.49 + index * 0.00084) for index in range(18)]
        trace = process_trace(points)
        connection = FakeConnection([item.source_row_number for item in points])

        result = persist_processed_trace(
            connection,
            dataset_id=UUID("ab96de99-f2d3-402b-ad2b-c756e05d4d62"),
            dataset_import_id=UUID("3a2b810e-ce30-408a-a29b-9e6c9bf026e5"),
            input_sha256="a" * 64,
            trace=trace,
            parameters={"profile": "test"},
            code_revision="test-revision",
        )

        self.assertEqual(set(result.stage_run_ids), {
            "quality", "continuity", "motion", "stays", "trips", "modes", "trajectory"
        })
        self.assertEqual(result.row_counts["point_assessments"], len(points))
        self.assertEqual(result.row_counts["trajectory_vertices"], len(points))
        self.assertGreater(result.row_counts["mobility_legs"], 0)
        self.assertGreater(result.row_counts["leg_mode_scores"], 0)
        self.assertTrue(
            any("activate_processing_run" in query for query, _ in connection.fake_cursor.executions)
        )
        for query, batches in connection.fake_cursor.many:
            for params in batches:
                self.assertEqual(query.count("%s"), len(params), query)
        for query, params in connection.fake_cursor.executions:
            self.assertEqual(query.count("%s"), len(params), query)
        final_update_index = next(
            index
            for index, (query, _) in enumerate(connection.fake_cursor.executions)
            if "UPDATE app.processing_runs" in query
        )
        activation_index = next(
            index
            for index, (query, _) in enumerate(connection.fake_cursor.executions)
            if "activate_processing_run" in query
        )
        self.assertLess(final_update_index, activation_index)

    def test_missing_imported_source_row_aborts_before_run_insert(self) -> None:
        points = [point(index, longitude=121.49 + index * 0.0002) for index in range(3)]
        trace = process_trace(points)
        connection = FakeConnection([points[0].source_row_number])

        with self.assertRaisesRegex(ValueError, "missing imported source rows"):
            persist_processed_trace(
                connection,
                dataset_id=UUID("ab96de99-f2d3-402b-ad2b-c756e05d4d62"),
                dataset_import_id=UUID("3a2b810e-ce30-408a-a29b-9e6c9bf026e5"),
                input_sha256="b" * 64,
                trace=trace,
            )

        self.assertFalse(
            any("INSERT INTO app.processing_runs" in query for query, _ in connection.fake_cursor.executions)
        )

    def test_invalid_source_hash_is_rejected_without_database_access(self) -> None:
        trace = process_trace([point(0, longitude=121.49)])
        connection = FakeConnection([2])

        with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
            persist_processed_trace(
                connection,
                dataset_id=UUID("ab96de99-f2d3-402b-ad2b-c756e05d4d62"),
                dataset_import_id=UUID("3a2b810e-ce30-408a-a29b-9e6c9bf026e5"),
                input_sha256="not-a-hash",
                trace=trace,
            )

        self.assertEqual(connection.fake_cursor.executions, [])


if __name__ == "__main__":
    unittest.main()
