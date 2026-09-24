from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime, timedelta

from routefrom_pipeline import process_trace
from routefrom_pipeline.model import NormalizedLocationPoint
from routefrom_pipeline.trajectory import (
    TimeRange,
    TrajectoryConfig,
    build_trajectory_representation,
    normalize_time_ranges,
    query_trajectory,
)
from routefrom_pipeline.trajectory.representation import (
    _assign_interval_importance,
    _distance_to_segment,
)


def point(
    base: datetime,
    seconds: int,
    latitude: float,
    longitude: float,
    *,
    speed: float | None = 2.0,
) -> NormalizedLocationPoint:
    recorded_at = base + timedelta(seconds=seconds)
    return NormalizedLocationPoint(
        source_row_number=seconds + 2,
        geo_time_epoch_ms=int(recorded_at.timestamp() * 1000),
        recorded_at=recorded_at,
        day_time_epoch_ms=int(base.timestamp() * 1000),
        source_latitude=latitude,
        source_longitude=longitude,
        wgs_latitude=latitude,
        wgs_longitude=longitude,
        source_altitude=0,
        altitude_meters=0,
        source_course=None,
        course_degrees=None,
        source_horizontal_accuracy=8,
        horizontal_accuracy_meters=8,
        source_vertical_accuracy=None,
        vertical_accuracy_meters=None,
        source_speed=speed,
        recorded_speed_mps=speed,
        network_type=0,
        network_name=None,
        location_type=0,
        normalization_flags=(),
    )


class TrajectoryTests(unittest.TestCase):
    def test_gradual_curve_never_collapses_into_a_long_chord(self) -> None:
        coordinates = [
            (index * 50.0, 1000.0 * math.sin(math.pi * index / 200))
            for index in range(201)
        ]
        importance: list[float | None] = [0.0] * len(coordinates)
        importance[0] = importance[-1] = None
        _assign_interval_importance(coordinates, 0, len(coordinates) - 1, importance)

        selected = [
            index
            for index, score in enumerate(importance)
            if score is None or score >= 50.0
        ]
        self.assertGreater(len(selected), 2)
        self.assertLess(len(selected), len(coordinates))
        for left, right in zip(selected, selected[1:]):
            self.assertLessEqual(
                max(
                    _distance_to_segment(
                        coordinates[left], coordinates[index], coordinates[right]
                    )
                    for index in range(left, right + 1)
                ),
                50.0 + 1e-7,
            )

    def test_nested_kinks_respect_every_display_tolerance(self) -> None:
        coordinates = [
            (
                index * 10.0,
                100.0 * math.sin(index / 10) + 30.0 * math.sin(index / 2),
            )
            for index in range(101)
        ]
        importance: list[float | None] = [0.0] * len(coordinates)
        importance[0] = importance[-1] = None
        _assign_interval_importance(coordinates, 0, len(coordinates) - 1, importance)

        for tolerance in (5.0, 10.0, 20.0, 50.0):
            selected = [
                index
                for index, score in enumerate(importance)
                if score is None or score >= tolerance
            ]
            for left, right in zip(selected, selected[1:]):
                self.assertLessEqual(
                    max(
                        _distance_to_segment(
                            coordinates[left], coordinates[index], coordinates[right]
                        )
                        for index in range(left, right + 1)
                    ),
                    tolerance + 1e-7,
                )

    def test_time_ranges_are_sorted_and_unionized_without_calendar_granularity(self) -> None:
        base = datetime(2026, 7, 1, tzinfo=UTC)
        ranges = [
            TimeRange(base + timedelta(seconds=30), base + timedelta(seconds=50)),
            TimeRange(base, base + timedelta(seconds=20)),
            TimeRange(base + timedelta(seconds=15), base + timedelta(seconds=30)),
            TimeRange(base + timedelta(seconds=80), base + timedelta(seconds=90)),
        ]

        normalized = normalize_time_ranges(ranges)

        self.assertEqual(
            normalized,
            (
                TimeRange(base, base + timedelta(seconds=50)),
                TimeRange(base + timedelta(seconds=80), base + timedelta(seconds=90)),
            ),
        )

    def test_discontinuous_ranges_are_clipped_and_never_connected(self) -> None:
        base = datetime(2026, 7, 1, tzinfo=UTC)
        points = [
            point(base, index * 10, 31.2, 121.49 + index * 0.0002)
            for index in range(11)
        ]
        processed = process_trace(points)
        ranges = [
            TimeRange(base + timedelta(seconds=15), base + timedelta(seconds=35)),
            TimeRange(base + timedelta(seconds=65), base + timedelta(seconds=85)),
        ]

        paths = query_trajectory(processed.trajectory, ranges)

        self.assertEqual(len(paths), 2)
        self.assertEqual([path.selection_range_index for path in paths], [0, 1])
        self.assertEqual(paths[0].vertices[0].recorded_at, ranges[0].start)
        self.assertEqual(paths[0].vertices[-1].recorded_at, ranges[0].end)
        self.assertEqual(paths[1].vertices[0].recorded_at, ranges[1].start)
        self.assertEqual(paths[1].vertices[-1].recorded_at, ranges[1].end)
        self.assertTrue(paths[0].vertices[0].is_interpolated)
        self.assertTrue(paths[1].vertices[-1].is_interpolated)

    def test_nested_importance_keeps_high_precision_as_a_superset(self) -> None:
        base = datetime(2026, 7, 1, tzinfo=UTC)
        points = [
            point(
                base,
                index * 10,
                31.2 + math.sin(index / 3) * 0.00012,
                121.49 + index * 0.00018,
            )
            for index in range(60)
        ]
        processed = process_trace(points)
        selected_range = TimeRange(base, base + timedelta(seconds=591))

        detailed = query_trajectory(
            processed.trajectory,
            [selected_range],
            pixel_tolerance_meters=0.0,
        )[0]
        medium = query_trajectory(
            processed.trajectory,
            [selected_range],
            pixel_tolerance_meters=2.0,
        )[0]
        coarse = query_trajectory(
            processed.trajectory,
            [selected_range],
            pixel_tolerance_meters=10.0,
        )[0]

        detailed_points = {vertex.point_index for vertex in detailed.vertices}
        medium_points = {vertex.point_index for vertex in medium.vertices}
        coarse_points = {vertex.point_index for vertex in coarse.vertices}
        self.assertTrue(coarse_points <= medium_points <= detailed_points)
        anchors = {
            vertex.point_index
            for vertex in processed.trajectory.segments[0].vertices
            if vertex.is_semantic_anchor
        }
        self.assertTrue(anchors <= coarse_points)

    def test_vertex_budget_is_exact_unless_mandatory_anchors_exceed_it(self) -> None:
        base = datetime(2026, 7, 1, tzinfo=UTC)
        points = [
            point(
                base,
                index * 10,
                31.2 + math.sin(index / 4) * 0.00008,
                121.49 + index * 0.00015,
            )
            for index in range(50)
        ]
        processed = process_trace(points)
        selected_range = TimeRange(base, base + timedelta(seconds=491))

        paths = query_trajectory(
            processed.trajectory,
            [selected_range],
            vertex_budget=8,
        )

        mandatory_count = sum(
            1
            for vertex in processed.trajectory.segments[0].vertices
            if vertex.is_semantic_anchor
        )
        self.assertLessEqual(
            sum(len(path.vertices) for path in paths),
            max(8, mandatory_count),
        )
        self.assertTrue(
            all(math.isfinite(path.applied_importance_threshold_meters) for path in paths)
        )

    def test_chunks_use_size_and_semantics_not_midnight(self) -> None:
        base = datetime(2026, 7, 1, 23, 58, tzinfo=UTC)
        points = [
            point(base, index * 60, 31.2, 121.49 + index * 0.0005)
            for index in range(8)
        ]
        processed = process_trace(points)

        self.assertEqual(len(processed.trajectory.chunks), 1)
        chunk = processed.trajectory.chunks[0]
        self.assertNotEqual(chunk.started_at.date(), chunk.ended_at.date())

        rechunked = build_trajectory_representation(
            points,
            processed.continuity,
            processed.stays,
            processed.trips,
            processed.modes,
            config=TrajectoryConfig(
                max_chunk_vertices=4,
                split_chunks_at_semantic_anchors=False,
            ),
        )
        self.assertGreater(len(rechunked.chunks), 1)
        self.assertTrue(all(len(item.vertices) <= 4 for item in rechunked.chunks))
        for previous, current in zip(rechunked.chunks, rechunked.chunks[1:]):
            self.assertEqual(
                previous.last_sequence_number,
                current.first_sequence_number,
            )
            self.assertTrue(current.has_leading_overlap)

    def test_observation_gap_always_produces_separate_solid_paths(self) -> None:
        base = datetime(2026, 7, 1, tzinfo=UTC)
        points = [
            point(base, 0, 31.2, 121.5, speed=0.0),
            point(base, 10, 31.20001, 121.50001, speed=0.0),
            point(base, 7_200, 31.201, 121.501, speed=0.0),
            point(base, 7_210, 31.20101, 121.50101, speed=0.0),
        ]
        processed = process_trace(points)
        selected_range = TimeRange(base, base + timedelta(seconds=7_211))

        paths = query_trajectory(processed.trajectory, [selected_range])

        self.assertEqual(len(processed.observation_gaps), 1)
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0].segment_index, paths[1].segment_index)
        self.assertLess(paths[0].vertices[-1].recorded_at, paths[1].vertices[0].recorded_at)

    def test_time_ranges_require_aware_non_empty_bounds(self) -> None:
        naive = datetime(2026, 7, 1)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            TimeRange(naive, naive + timedelta(seconds=1))
        aware = naive.replace(tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            TimeRange(aware, aware)


if __name__ == "__main__":
    unittest.main()
