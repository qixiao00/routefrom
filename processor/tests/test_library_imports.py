from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime

from routefrom_pipeline.ingest.library import (
    ObservationBatch,
    observation_fingerprint,
    reconcile_imports,
)
from routefrom_pipeline.ingest.linggan import _parse_row


def point(epoch_ms: int, row: int = 2, longitude: str = "118.7"):
    return _parse_row({
        "geoTime": str(epoch_ms), "dayTimeMills": "1682006400000",
        "latitude": "30.9", "longitude": longitude,
        "wgsLatitude": "30.9", "wgsLongitude": longitude,
        "horizontalAccuracy": "5", "speed": "1",
    }, row)


class LibraryImportTests(unittest.TestCase):
    def test_overlap_is_deduplicated_with_all_origins_and_order_independent(self):
        a = ObservationBatch("a", "phone", (point(1000), point(2000, 3)))
        b = ObservationBatch("b", "phone", (point(3000), point(2000, 3)))
        result = reconcile_imports([a, b])
        reverse = reconcile_imports([b, a])
        self.assertEqual(result, reverse)
        self.assertEqual(result.duplicate_row_count, 1)
        self.assertEqual(len(result.observations[1].origins), 2)
        self.assertEqual([p.geo_time_epoch_ms for p in result.processing_points()],
                         [1000, 2000, 3000])
        self.assertEqual([p.source_row_number for p in result.processing_points()], [2, 3, 4])

    def test_repeated_visits_and_nearby_coordinates_are_not_removed(self):
        result = reconcile_imports([ObservationBatch("a", "phone", (
            point(1000), point(2000, 3), point(2000, 4, "118.700000001"),
        ))])
        self.assertEqual(len(result.observations), 3)
        self.assertEqual(len(result.conflicts), 1)
        with self.assertRaisesRegex(ValueError, "resolution"):
            result.processing_points()

    def test_conflicting_sensor_reading_is_preserved_and_requires_resolution(self):
        original = point(1000)
        corrected = replace(original, source_horizontal_accuracy=2, horizontal_accuracy_meters=2)
        result = reconcile_imports([
            ObservationBatch("a", "phone", (original,)),
            ObservationBatch("b", "phone", (corrected,)),
        ])
        key = observation_fingerprint(corrected, "phone")
        self.assertEqual(len(result.processing_points({1000: key})), 1)
        self.assertEqual(len(result.observations), 2)
        with self.assertRaises(ValueError):
            result.processing_points({1000: "not-an-observation"})

    def test_withdrawing_an_import_preserves_shared_observations(self):
        a = ObservationBatch("a", "phone", (point(1000),))
        b = ObservationBatch("b", "phone", (point(1000), point(2000, 3)))
        merged = reconcile_imports([a, b])
        remaining = reconcile_imports([b])
        self.assertEqual(merged.content_sha256, remaining.content_sha256)
        self.assertEqual(len(remaining.observations), 2)
        self.assertEqual(len(remaining.observations[0].origins), 1)

    def test_different_devices_remain_separate(self):
        result = reconcile_imports([
            ObservationBatch("a", "phone", (point(1000),)),
            ObservationBatch("b", "watch", (point(1000),)),
        ])
        self.assertEqual(result.duplicate_row_count, 0)
        self.assertEqual(len(result.conflicts), 1)

    def test_reordered_csv_has_same_content_identity(self):
        first = reconcile_imports([ObservationBatch("a", "phone", (point(1000), point(2000, 3)))])
        second = reconcile_imports([ObservationBatch("b", "phone", (point(2000), point(1000, 3)))])
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(first.processing_points(), second.processing_points())

    def test_invalid_provenance_and_nonfinite_values_fail(self):
        with self.assertRaises(ValueError):
            reconcile_imports([ObservationBatch("a", "phone", (point(1000), point(2000)))])
        with self.assertRaises(ValueError):
            observation_fingerprint(replace(point(1000), source_speed=float("nan")), "phone")
        with self.assertRaises(ValueError):
            reconcile_imports([ObservationBatch("a", "phone", (
                replace(point(1000), recorded_at=datetime.now(UTC)),
            ))])


if __name__ == "__main__":
    unittest.main()
