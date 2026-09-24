from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from routefrom_pipeline.continuity import (
    SamplingContext,
    estimate_sampling_intervals,
)
from routefrom_pipeline.model import NormalizedLocationPoint
from routefrom_pipeline.pipeline import process_trace
from routefrom_pipeline.quality import build_point_features


def point(
    seconds: int,
    longitude: float,
    *,
    recorded_speed: float | None,
    accuracy: float = 8.0,
) -> NormalizedLocationPoint:
    recorded_at = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(seconds=seconds)
    return NormalizedLocationPoint(
        source_row_number=seconds + 2,
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


class SamplingSurvivalTests(unittest.TestCase):
    def test_stationary_twenty_minute_wait_can_be_normal_for_local_context(self) -> None:
        times = [0, 600, 1_200, 1_800, 2_400, 3_000, 4_200, 4_800, 5_400, 6_000]
        points = [point(seconds, 121.5, recorded_speed=0.0) for seconds in times]

        assessments = estimate_sampling_intervals(points, build_point_features(points))
        candidate = assessments[5]

        self.assertEqual(candidate.elapsed_seconds, 1_200)
        self.assertEqual(candidate.context, SamplingContext.STATIONARY)
        self.assertFalse(candidate.is_observation_gap)
        self.assertGreater(candidate.normal_wait_probability, 0.01)
        self.assertIn("long_wait_plausible_for_context", candidate.reason_codes)

    def test_same_wait_is_a_gap_while_moving(self) -> None:
        intervals = [15, 15, 15, 15, 15, 1_200, 15, 15, 15, 15]
        times = [0]
        longitudes = [121.5]
        for duration in intervals:
            times.append(times[-1] + duration)
            longitudes.append(longitudes[-1] + (duration * 1.5) / 95_000)
        points = [
            point(seconds, longitude, recorded_speed=1.5)
            for seconds, longitude in zip(times, longitudes)
        ]

        assessments = estimate_sampling_intervals(points, build_point_features(points))
        candidate = assessments[5]

        self.assertEqual(candidate.context, SamplingContext.MOVING)
        self.assertTrue(candidate.is_observation_gap)
        self.assertLess(candidate.normal_wait_probability, 0.01)
        self.assertIn("wait_survival_tail", candidate.reason_codes)

        processed = process_trace(points)
        gap_edges = [
            edge
            for edge in processed.continuity.edges
            if "observation_gap_unknown" in edge.reason_codes
        ]
        self.assertEqual(len(gap_edges), 1)
        self.assertEqual(gap_edges[0].sampling_context, SamplingContext.MOVING)

    def test_six_hour_engineering_maximum_always_breaks_observation(self) -> None:
        times = [0, 600, 1_200, 1_800, 2_400, 24_000, 24_600, 25_200]
        points = [point(seconds, 121.5, recorded_speed=0.0) for seconds in times]

        assessments = estimate_sampling_intervals(points, build_point_features(points))
        candidate = assessments[4]

        self.assertTrue(candidate.is_observation_gap)
        self.assertIn("engineering_maximum_gap", candidate.reason_codes)

    def test_repeated_long_displacements_do_not_train_the_normal_wait_profile(self) -> None:
        times = [0, 15, 30, 3_630, 7_230, 10_830, 10_845, 10_860]
        longitudes = [121.5, 121.5002, 121.5004, 122.1, 122.7, 123.3, 123.3002, 123.3004]
        points = [
            point(seconds, longitude, recorded_speed=1.5)
            for seconds, longitude in zip(times, longitudes)
        ]

        sampling = estimate_sampling_intervals(points, build_point_features(points))

        self.assertLess(sampling[2].expected_interval_seconds, 60)
        self.assertTrue(all(sampling[index].is_observation_gap for index in (2, 3, 4)))
        self.assertIn("wait_survival_tail", sampling[2].reason_codes)

        trace = process_trace(points)
        self.assertTrue(
            all(
                any(edge.kind == "break" and edge.from_index <= index < edge.to_index
                    for edge in trace.continuity.edges)
                for index in (2, 3, 4)
            )
        )

    def test_short_high_speed_sequence_with_recorded_speed_stays_connected(self) -> None:
        points = [
            point(index * 60, 121.5 + index * 0.10, recorded_speed=160.0)
            for index in range(6)
        ]

        sampling = estimate_sampling_intervals(points, build_point_features(points))
        trace = process_trace(points)

        self.assertTrue(all(not interval.is_observation_gap for interval in sampling))
        self.assertEqual(len(trace.continuity.segments), 1)

    def test_long_displacement_without_speed_support_remains_unknown(self) -> None:
        times = [0, 15, 30, 3_930, 3_945, 3_960]
        longitudes = [121.5, 121.5001, 121.5002, 121.51, 121.5101, 121.5102]
        points = [
            point(seconds, longitude, recorded_speed=0.0)
            for seconds, longitude in zip(times, longitudes)
        ]

        sampling = estimate_sampling_intervals(points, build_point_features(points))

        self.assertTrue(sampling[2].is_observation_gap)
        self.assertIn("displaced_without_speed_support", sampling[2].reason_codes)
        trace = process_trace(points)
        self.assertTrue(
            any(
                edge.kind == "break" and edge.from_index == 2 and edge.to_index == 3
                for edge in trace.continuity.edges
            )
        )

    def test_long_wait_with_unreliable_position_cannot_confirm_same_place(self) -> None:
        points = [
            point(0, 121.5, recorded_speed=0.0),
            point(600, 121.5, recorded_speed=0.0),
            point(2_400, 121.505, recorded_speed=0.0, accuracy=900),
            point(3_000, 121.505, recorded_speed=0.0),
        ]

        sampling = estimate_sampling_intervals(points, build_point_features(points))

        self.assertTrue(sampling[1].is_observation_gap)
        self.assertIn("long_wait_with_weak_position_support", sampling[1].reason_codes)
        trace = process_trace(points)
        self.assertTrue(
            any(
                edge.kind == "break" and edge.from_index == 1 and edge.to_index == 2
                for edge in trace.continuity.edges
            )
        )

    def test_isolated_jump_without_sensor_or_neighbor_support_is_unknown(self) -> None:
        points = [
            point(0, 121.5, recorded_speed=0.0),
            point(30, 121.5001, recorded_speed=0.0),
            point(230, 121.67, recorded_speed=None, accuracy=150),
            point(900, 121.5002, recorded_speed=0.0),
            point(930, 121.5003, recorded_speed=0.0),
        ]

        sampling = estimate_sampling_intervals(points, build_point_features(points))

        self.assertTrue(sampling[1].is_observation_gap)
        self.assertIn("uncorroborated_displacement", sampling[1].reason_codes)

    def test_coherent_fast_motion_without_sensor_speed_can_remain_observed(self) -> None:
        points = [
            point(index * 60, 121.5 + index * 0.06, recorded_speed=None, accuracy=500)
            for index in range(10)
        ]

        sampling = estimate_sampling_intervals(points, build_point_features(points))

        self.assertTrue(all(not item.is_observation_gap for item in sampling[1:-1]))


if __name__ == "__main__":
    unittest.main()
