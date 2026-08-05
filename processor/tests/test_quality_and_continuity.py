from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from routefrom_pipeline.continuity import select_continuity
from routefrom_pipeline.model import NormalizedLocationPoint, QualityStatus
from routefrom_pipeline.pipeline import ALGORITHM_VERSION, process_trace
from routefrom_pipeline.quality import assess_points, build_point_features


def point(
    seconds: int,
    latitude: float,
    longitude: float,
    *,
    accuracy: float = 8.0,
    recorded_speed: float | None = 1.0,
) -> NormalizedLocationPoint:
    recorded_at = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(seconds=seconds)
    return NormalizedLocationPoint(
        source_row_number=seconds + 2,
        geo_time_epoch_ms=int(recorded_at.timestamp() * 1000),
        recorded_at=recorded_at,
        day_time_epoch_ms=int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000),
        source_latitude=latitude,
        source_longitude=longitude,
        wgs_latitude=latitude,
        wgs_longitude=longitude,
        source_altitude=0,
        altitude_meters=0,
        source_course=None,
        course_degrees=None,
        source_horizontal_accuracy=accuracy,
        horizontal_accuracy_meters=accuracy,
        source_vertical_accuracy=None,
        vertical_accuracy_meters=None,
        source_speed=recorded_speed,
        recorded_speed_mps=recorded_speed,
        network_type=0,
        network_name=None,
        location_type=0,
        normalization_flags=(),
    )


class QualityAndContinuityTests(unittest.TestCase):
    def test_isolated_long_distance_return_is_excluded_and_bypassed(self) -> None:
        points = [
            point(0, 28.1900, 112.9600),
            point(10, 24.5384, 118.1241),
            point(20, 28.1901, 112.9601),
        ]

        features = build_point_features(points)
        assessments = assess_points(features)
        result = select_continuity(
            points,
            assessments,
            local_intervals_seconds=[10, 10, 10],
        )

        self.assertEqual(assessments[1].quality_status, QualityStatus.EXCLUDED)
        self.assertGreaterEqual(assessments[1].anomaly_probability, 0.98)
        self.assertIn("far_jump_then_return", assessments[1].reason_codes)
        self.assertEqual(result.selected_point_indices, (0, 2))
        self.assertEqual(result.skipped_point_indices, (1,))
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.edges[0].kind, "bypass")

    def test_accuracy_alone_cannot_exclude_a_smooth_point(self) -> None:
        points = [
            point(0, 31.2000, 121.5000),
            point(20, 31.2001, 121.5001, accuracy=10_000),
            point(40, 31.2002, 121.5002),
        ]

        assessment = assess_points(build_point_features(points))[1]

        self.assertNotEqual(assessment.quality_status, QualityStatus.EXCLUDED)
        self.assertFalse(assessment.exclusion_supported)
        self.assertIn("low_horizontal_accuracy", assessment.reason_codes)

    def test_unreliable_jump_without_return_creates_a_break(self) -> None:
        points = [
            point(0, 31.2000, 121.5000),
            point(10, 31.2001, 121.5001),
            point(20, 24.5384, 118.1241),
            point(30, 24.5385, 118.1242),
        ]
        features = build_point_features(points)
        assessments = assess_points(features)
        # The boundary points can be suspect, but there is no short bypass proving
        # either location is an isolated teleport.
        result = select_continuity(
            points,
            assessments,
            local_intervals_seconds=[10, 10, 10, 10],
        )

        self.assertTrue(any(edge.kind == "break" for edge in result.edges))
        self.assertGreaterEqual(len(result.segments), 2)

    def test_outlier_probability_is_not_a_single_speed_threshold(self) -> None:
        points = [
            point(0, 31.2000, 121.5000, recorded_speed=80),
            point(10, 31.2070, 121.5000, recorded_speed=80),
            point(20, 31.2140, 121.5000, recorded_speed=80),
        ]

        middle = assess_points(build_point_features(points))[1]

        self.assertNotEqual(middle.quality_status, QualityStatus.EXCLUDED)
        self.assertNotIn("far_jump_then_return", middle.reason_codes)

    def test_long_sampling_gap_is_unknown_and_breaks_continuity(self) -> None:
        points = [
            point(0, 31.2000, 121.5000),
            point(10, 31.2001, 121.5001),
            point(7_200, 31.2002, 121.5002),
            point(7_210, 31.2003, 121.5003),
        ]

        processed = process_trace(points)
        gap_edges = [
            edge
            for edge in processed.continuity.edges
            if "observation_gap_unknown" in edge.reason_codes
        ]

        self.assertEqual(processed.algorithm_version, ALGORITHM_VERSION)
        self.assertEqual(len(gap_edges), 1)
        self.assertEqual(len(processed.continuity.segments), 2)

    def test_pipeline_rejects_non_increasing_time(self) -> None:
        repeated = point(0, 31.2000, 121.5000)

        with self.assertRaisesRegex(ValueError, "strictly ordered"):
            process_trace([repeated, repeated])


if __name__ == "__main__":
    unittest.main()
