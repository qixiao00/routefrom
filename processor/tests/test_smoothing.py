from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime, timedelta

from routefrom_pipeline.continuity import ContinuityResult
from routefrom_pipeline.model import NormalizedLocationPoint
from routefrom_pipeline.smoothing import SmoothingConfig, smooth_trace


def point(index: int, *, latitude: float, longitude: float) -> NormalizedLocationPoint:
    recorded_at = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(seconds=index * 10)
    return NormalizedLocationPoint(
        source_row_number=index + 2,
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
        source_horizontal_accuracy=15,
        horizontal_accuracy_meters=15,
        source_vertical_accuracy=None,
        vertical_accuracy_meters=None,
        source_speed=None,
        recorded_speed_mps=None,
        network_type=0,
        network_name=None,
        location_type=0,
        normalization_flags=(),
    )


class SmoothingTests(unittest.TestCase):
    def test_rts_smoother_reduces_lateral_noise_and_keeps_anchors(self) -> None:
        baseline_latitude = 31.2
        points = tuple(
            point(
                index,
                latitude=(
                    baseline_latitude
                    if index in (0, 20)
                    else baseline_latitude + (0.00008 if index % 2 else -0.00008)
                ),
                longitude=121.49 + index * 0.0001,
            )
            for index in range(21)
        )
        continuity = ContinuityResult(
            selected_point_indices=tuple(range(21)),
            skipped_point_indices=(),
            edges=(),
            segments=(tuple(range(21)),),
        )

        result = smooth_trace(points, continuity, anchor_indices=(0, 20))

        raw_error = math.sqrt(
            sum((item.wgs_latitude - baseline_latitude) ** 2 for item in points)
            / len(points)
        )
        smooth_error = math.sqrt(
            sum((item.latitude - baseline_latitude) ** 2 for item in result.points)
            / len(result.points)
        )
        self.assertLess(smooth_error, raw_error * 0.5)
        self.assertEqual(
            result.coordinate_by_point[0],
            (points[0].wgs_latitude, points[0].wgs_longitude),
        )
        self.assertEqual(
            result.coordinate_by_point[20],
            (points[20].wgs_latitude, points[20].wgs_longitude),
        )

    def test_segments_are_smoothed_independently_and_skipped_points_stay_absent(self) -> None:
        points = (
            point(0, latitude=31.2, longitude=121.49),
            point(1, latitude=31.2001, longitude=121.4901),
            point(2, latitude=31.2, longitude=121.4902),
            point(3, latitude=40.0, longitude=116.0),
            point(4, latitude=40.0001, longitude=116.0001),
            point(5, latitude=40.0, longitude=116.0002),
            point(6, latitude=0.0, longitude=0.0),
        )
        continuity = ContinuityResult(
            selected_point_indices=(0, 1, 2, 3, 4, 5),
            skipped_point_indices=(6,),
            edges=(),
            segments=((0, 1, 2), (3, 4, 5)),
        )

        result = smooth_trace(points, continuity)

        self.assertEqual(result.segments, continuity.segments)
        self.assertNotIn(6, result.coordinate_by_point)
        self.assertGreater(result.coordinate_by_point[3][0], 39.0)
        self.assertLess(result.coordinate_by_point[2][0], 32.0)

    def test_smoothed_gps_never_leaves_measurement_support(self) -> None:
        points = tuple(
            point(
                index,
                latitude=31.2 + (0.01 if index == 5 else 0),
                longitude=121.49 + index * 0.0001,
            )
            for index in range(11)
        )
        continuity = ContinuityResult(
            selected_point_indices=tuple(range(11)),
            skipped_point_indices=(),
            edges=(),
            segments=(tuple(range(11)),),
        )
        config = SmoothingConfig(displacement_limit_cap_meters=30)

        result = smooth_trace(points, continuity, config=config)

        self.assertTrue(any(item.displacement_was_limited for item in result.points))
        self.assertLessEqual(
            max(item.displacement_from_observation_meters for item in result.points),
            30.01,
        )


if __name__ == "__main__":
    unittest.main()
