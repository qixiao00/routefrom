from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from routefrom_pipeline import process_trace
from routefrom_pipeline.model import NormalizedLocationPoint
from routefrom_pipeline.motion import MotionState
from routefrom_pipeline.stays import StationaryEventType


def point(
    seconds: int,
    latitude: float,
    longitude: float,
    *,
    speed: float | None,
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


class MotionAndStayTests(unittest.TestCase):
    def test_compact_long_stop_between_movement_is_a_visit(self) -> None:
        points = [
            point(index * 30, 31.2, 121.49 + index * 0.0006, speed=2.0)
            for index in range(4)
        ]
        points.extend(
            point(
                seconds,
                31.2 + (index % 2) * 0.00001,
                121.4924 + (index % 3 - 1) * 0.00001,
                speed=0.1,
            )
            for index, seconds in enumerate(range(120, 721, 60))
        )
        points.extend(
            point(720 + index * 30, 31.2, 121.4924 + index * 0.0006, speed=2.0)
            for index in range(1, 5)
        )

        processed = process_trace(points)
        visits = [
            event
            for event in processed.stays.events
            if event.event_type == StationaryEventType.VISIT
        ]

        self.assertEqual(len(visits), 1)
        self.assertGreaterEqual(visits[0].observed_duration_seconds, 8 * 60)
        self.assertGreater(visits[0].visit_probability, 0.7)
        self.assertLess(visits[0].spatial_radius_meters, 10)
        self.assertFalse(visits[0].open_start)
        self.assertFalse(visits[0].open_end)

    def test_short_stop_with_aligned_movement_is_transport_pause(self) -> None:
        points = [
            point(index * 10, 31.2, 121.49 + index * 0.00025, speed=2.3)
            for index in range(4)
        ]
        points.extend(
            point(seconds, 31.2, 121.491, speed=0.0)
            for seconds in range(40, 91, 10)
        )
        points.extend(
            point(seconds, 31.2, 121.491 + index * 0.00025, speed=2.3)
            for index, seconds in enumerate(range(100, 141, 10), 1)
        )

        processed = process_trace(points)

        self.assertEqual(len(processed.stays.events), 1)
        event = processed.stays.events[0]
        self.assertEqual(event.event_type, StationaryEventType.TRANSPORT_PAUSE)
        self.assertGreater(event.transport_pause_probability, 0.5)
        self.assertEqual(
            processed.motion.state_by_point[event.point_indices[0]],
            MotionState.STATIONARY,
        )

    def test_same_place_sampling_gap_is_not_confirmed_stay_time(self) -> None:
        points = [
            point(0, 31.2, 121.5, speed=0.0),
            point(10, 31.20001, 121.50001, speed=0.0),
            point(7_200, 31.20001, 121.50001, speed=0.0),
            point(7_210, 31.2, 121.5, speed=0.0),
        ]

        processed = process_trace(points)

        self.assertEqual(len(processed.continuity.segments), 2)
        self.assertEqual(
            sum(event.observed_duration_seconds for event in processed.stays.events),
            20.0,
        )
        self.assertTrue(
            all(
                event.event_type == StationaryEventType.UNCERTAIN_STOP
                for event in processed.stays.events
            )
        )
        self.assertTrue(all(event.open_start and event.open_end for event in processed.stays.events))


if __name__ == "__main__":
    unittest.main()
