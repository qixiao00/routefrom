from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime, timedelta

from routefrom_pipeline import process_trace
from routefrom_pipeline.model import NormalizedLocationPoint
from routefrom_pipeline.modes import ModeMapEvidence, infer_transport_modes
from routefrom_pipeline.modes.inference import _log_probability
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


class ModeInferenceTests(unittest.TestCase):
    def test_probability_floor_prevents_log_underflow(self) -> None:
        self.assertTrue(math.isfinite(_log_probability(0.0)))

    def test_sustained_sensor_supported_cruise_speed_is_air_not_unknown(self) -> None:
        points = [
            point(index * 60, 31.2, 121.49 + index * 0.10, speed=160.0)
            for index in range(12)
        ]

        processed = process_trace(points)

        self.assertTrue(processed.modes.legs)
        self.assertEqual(processed.modes.legs[0].selected_mode_code, "air")

    def test_penalized_change_points_split_walk_from_road_vehicle(self) -> None:
        points: list[NormalizedLocationPoint] = []
        longitude = 121.49
        for index in range(12):
            if index:
                longitude += 0.000126
            points.append(point(index * 10, 31.2, longitude, speed=1.2))
        for index in range(12, 24):
            longitude += 0.00157
            points.append(point(index * 10, 31.2, longitude, speed=15.0))

        processed = process_trace(points)

        self.assertEqual(len(processed.modes.legs), 2)
        self.assertEqual(processed.modes.legs[0].selected_mode_code, "walk")
        self.assertEqual(processed.modes.legs[0].selected_mode_level, "detail")
        self.assertEqual(processed.modes.legs[1].selected_mode_code, "road_vehicle")
        self.assertEqual(processed.modes.legs[1].selected_mode_level, "parent")
        for leg in processed.modes.legs:
            self.assertAlmostEqual(
                sum(score.probability for score in leg.mode_scores),
                1.0,
                places=9,
            )

    def test_bus_requires_map_evidence_for_detail_selection(self) -> None:
        points = [
            point(index * 15, 31.2, 121.49 + index * 0.0012, speed=8.0)
            for index in range(15)
        ]
        processed = process_trace(points)

        self.assertEqual(processed.modes.legs[0].selected_mode_code, "road_vehicle")
        self.assertEqual(processed.modes.legs[0].selected_mode_level, "parent")

        map_evidence = {
            index: ModeMapEvidence(
                road_network_probability=0.95,
                bus_route_probability=0.98,
            )
            for index in range(len(points))
        }
        enhanced = infer_transport_modes(
            points,
            processed.assessments,
            processed.stays,
            processed.trips,
            map_evidence_by_point=map_evidence,
        )

        self.assertEqual(enhanced.legs[0].selected_mode_code, "bus")
        self.assertEqual(enhanced.legs[0].selected_mode_level, "detail")
        self.assertGreater(enhanced.legs[0].confidence, 0.75)
        bus_score = next(
            score for score in enhanced.legs[0].mode_scores if score.mode_code == "bus"
        )
        self.assertIn("bus_route", bus_score.contributions)

    def test_transport_pause_is_downweighted_not_a_new_mode_leg(self) -> None:
        points: list[NormalizedLocationPoint] = []
        longitude = 121.49
        timestamp = 0
        for _ in range(8):
            points.append(point(timestamp, 31.2, longitude, speed=8.0))
            longitude += 0.00084
            timestamp += 10
        hold_longitude = longitude
        for _ in range(6):
            points.append(point(timestamp, 31.2, hold_longitude, speed=0.0))
            timestamp += 10
        for _ in range(8):
            longitude += 0.00084
            points.append(point(timestamp, 31.2, longitude, speed=8.0))
            timestamp += 10

        processed = process_trace(points)

        pauses = [
            event
            for event in processed.stays.events
            if event.event_type == StationaryEventType.TRANSPORT_PAUSE
        ]
        self.assertEqual(len(pauses), 1)
        self.assertEqual(len(processed.modes.legs), 1)
        self.assertEqual(processed.modes.legs[0].selected_mode_code, "road_vehicle")


if __name__ == "__main__":
    unittest.main()
