from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from routefrom_pipeline import process_trace
from routefrom_pipeline.continuity import (
    InferredConnectionKind,
    ObservationGapCause,
)
from routefrom_pipeline.model import NormalizedLocationPoint
from routefrom_pipeline.stays import StationaryEventType
from routefrom_pipeline.trips import TripBoundaryState


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


def stationary_block(
    start_seconds: int,
    longitude: float,
) -> list[NormalizedLocationPoint]:
    return [
        point(
            seconds,
            31.2 + (index % 2) * 0.00001,
            longitude + (index % 3 - 1) * 0.00001,
            speed=0.1,
        )
        for index, seconds in enumerate(range(start_seconds, start_seconds + 601, 60))
    ]


class TripAndGapTests(unittest.TestCase):
    def test_transport_pause_does_not_split_a_closed_trip(self) -> None:
        points = stationary_block(0, 121.49)
        points.extend(
            point(660 + (index - 1) * 30, 31.2, 121.49 + index * 0.0006, speed=2.2)
            for index in range(1, 7)
        )
        points.extend(
            point(seconds, 31.2, 121.4937, speed=0.0)
            for seconds in range(840, 891, 10)
        )
        points.extend(
            point(
                900 + (index - 1) * 30,
                31.2,
                121.4937 + index * 0.0006,
                speed=2.2,
            )
            for index in range(1, 7)
        )
        points.extend(stationary_block(1_080, 121.4973))

        processed = process_trace(points)

        self.assertEqual(len(processed.trips.trips), 1)
        trip = processed.trips.trips[0]
        self.assertEqual(trip.boundary_state, TripBoundaryState.CLOSED)
        self.assertEqual(len(processed.trips.confirmed_visit_event_indices), 2)
        self.assertEqual(len(trip.transport_pause_event_indices), 1)
        pause = processed.stays.events[trip.transport_pause_event_indices[0]]
        self.assertEqual(pause.event_type, StationaryEventType.TRANSPORT_PAUSE)
        self.assertEqual(len(trip.track_segments), 1)
        self.assertEqual(trip.gap_indices, ())

    def test_sampling_gap_stays_inside_one_trip_but_breaks_the_solid_track(self) -> None:
        points = stationary_block(0, 121.49)
        points.extend(
            point(600 + index * 30, 31.2, 121.49 + index * 0.0006, speed=2.2)
            for index in range(1, 7)
        )
        points.extend(
            point(7_950 + index * 30, 31.2, 121.4936 + index * 0.0006, speed=2.2)
            for index in range(1, 7)
        )
        points.extend(stationary_block(8_190, 121.4972))

        processed = process_trace(points)

        self.assertEqual(len(processed.observation_gaps), 1)
        self.assertEqual(
            processed.observation_gaps[0].cause,
            ObservationGapCause.SOURCE_SAMPLING_GAP,
        )
        self.assertEqual(len(processed.trips.trips), 1)
        trip = processed.trips.trips[0]
        self.assertEqual(trip.boundary_state, TripBoundaryState.CLOSED)
        self.assertEqual(len(trip.track_segments), 2)
        self.assertEqual(trip.gap_indices, (0,))
        self.assertEqual(trip.unknown_duration_seconds, 7_200)
        self.assertGreater(trip.confirmed_distance_meters, 0)
        self.assertGreater(trip.inferred_distance_meters, 0)
        connection = processed.inferred_connections[0]
        self.assertEqual(connection.kind, InferredConnectionKind.STRAIGHT_LINE_CONTEXT)
        self.assertTrue(connection.displayable)
        self.assertFalse(connection.counts_toward_confirmed_distance)
        self.assertFalse(connection.counts_toward_confirmed_duration)

    def test_dataset_without_visits_produces_an_open_both_trip(self) -> None:
        points = [
            point(index * 20, 31.2, 121.49 + index * 0.0005, speed=2.0)
            for index in range(12)
        ]

        processed = process_trace(points)

        self.assertEqual(processed.trips.confirmed_visit_event_indices, ())
        self.assertEqual(len(processed.trips.trips), 1)
        self.assertEqual(
            processed.trips.trips[0].boundary_state,
            TripBoundaryState.OPEN_BOTH,
        )

    def test_same_place_gap_is_only_a_possible_hold(self) -> None:
        points = [
            point(0, 31.2, 121.5, speed=0.0),
            point(10, 31.20001, 121.50001, speed=0.0),
            point(7_200, 31.20001, 121.50001, speed=0.0),
            point(7_210, 31.2, 121.5, speed=0.0),
        ]

        processed = process_trace(points)

        self.assertEqual(len(processed.observation_gaps), 1)
        self.assertGreater(processed.observation_gaps[0].same_place_probability, 0.8)
        connection = processed.inferred_connections[0]
        self.assertEqual(connection.kind, InferredConnectionKind.SAME_PLACE)
        self.assertFalse(connection.counts_toward_confirmed_distance)
        self.assertEqual(
            sum(event.observed_duration_seconds for event in processed.stays.events),
            20.0,
        )


if __name__ == "__main__":
    unittest.main()
