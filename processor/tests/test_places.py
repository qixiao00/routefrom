from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from routefrom_pipeline.places import resolution
from routefrom_pipeline.places import PlaceConfig, resolve_places
from routefrom_pipeline.stays import StayResult, StationaryEvent, StationaryEventType


def event(
    day: int,
    *,
    longitude: float,
    confidence: float = 0.9,
    duration_hours: float = 2.0,
) -> StationaryEvent:
    started_at = datetime(2026, 7, 1, 1, tzinfo=UTC) + timedelta(days=day - 1)
    ended_at = started_at + timedelta(hours=duration_hours)
    return StationaryEvent(
        event_type=StationaryEventType.VISIT,
        point_indices=(0, 1, 2),
        observed_started_at=started_at,
        observed_ended_at=ended_at,
        possible_started_at=started_at,
        possible_ended_at=ended_at,
        observed_duration_seconds=duration_hours * 3_600,
        possible_duration_seconds=duration_hours * 3_600,
        centroid_latitude=31.2,
        centroid_longitude=longitude,
        spatial_radius_meters=8,
        adaptive_spatial_scale_meters=20,
        effective_point_count=5,
        confidence=confidence,
        visit_probability=confidence,
        transport_pause_probability=(1 - confidence) / 2,
        uncertain_stop_probability=(1 - confidence) / 2,
        arrival_confidence=0.8,
        departure_confidence=0.8,
        open_start=False,
        open_end=False,
        evidence={},
    )


class PlaceResolutionTests(unittest.TestCase):
    def test_repeated_visits_merge_and_score_above_a_single_visit(self) -> None:
        stays = StayResult(
            events=(
                event(1, longitude=121.50000),
                event(5, longitude=121.50008),
                event(12, longitude=121.49994),
                event(8, longitude=121.51000),
            )
        )

        result = resolve_places(stays, eligible_event_indices=(0, 1, 2, 3))

        self.assertEqual(len(result.places), 2)
        repeated = next(place for place in result.places if place.visit_count == 3)
        single = next(place for place in result.places if place.visit_count == 1)
        self.assertEqual(repeated.distinct_local_date_count, 3)
        self.assertGreater(repeated.frequent_probability, single.frequent_probability)
        self.assertEqual(result.unresolved_event_indices, ())
        self.assertEqual(
            {binding.place_index for binding in result.bindings[:3]},
            {result.places.index(repeated)},
        )

    def test_complete_link_prevents_spatial_chain_merging(self) -> None:
        stays = StayResult(
            events=(
                event(1, longitude=121.50000),
                event(2, longitude=121.50025),
                event(3, longitude=121.50050),
            )
        )
        config = PlaceConfig(merge_probability_min=0.58)

        result = resolve_places(
            stays,
            eligible_event_indices=(0, 1, 2),
            config=config,
        )

        self.assertEqual(len(result.places), 2)
        self.assertEqual(max(place.visit_count for place in result.places), 2)

    def test_nearby_competing_places_leave_binding_unresolved(self) -> None:
        stays = StayResult(
            events=(
                event(1, longitude=121.50000),
                event(2, longitude=121.50038),
            )
        )
        config = PlaceConfig(
            merge_probability_min=0.95,
            auto_bind_margin_min=0.30,
        )

        result = resolve_places(
            stays,
            eligible_event_indices=(0, 1),
            config=config,
        )

        self.assertEqual(len(result.places), 2)
        self.assertEqual(result.unresolved_event_indices, (0, 1))
        self.assertTrue(all(binding.place_index is None for binding in result.bindings))

    def test_spatial_grid_avoids_global_pair_comparisons(self) -> None:
        count = 500
        stays = StayResult(
            events=tuple(
                event(index + 1, longitude=100 + index * 0.02)
                for index in range(count)
            )
        )

        with patch(
            "routefrom_pipeline.places.resolution._event_pair_probability",
            wraps=resolution._event_pair_probability,
        ) as pair_probability:
            result = resolve_places(
                stays,
                eligible_event_indices=tuple(range(count)),
            )

        self.assertEqual(len(result.places), count)
        self.assertLess(pair_probability.call_count, count * 3)


if __name__ == "__main__":
    unittest.main()
