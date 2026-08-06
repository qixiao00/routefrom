from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from routefrom_pipeline.map_matching import (
    MapMatchConfig,
    MapSnapshotRef,
    MatchedObservation,
    ValhallaMapMatcher,
    decode_polyline6,
    match_trace,
)
from routefrom_pipeline.map_matching.matching import MapMatchRequest, MatcherResponse
from routefrom_pipeline.modes import MobilityLeg, ModeResult
from routefrom_pipeline.trajectory import (
    TrajectoryRepresentation,
    TrajectorySegment,
    TrajectoryVertex,
    build_hybrid_trajectory_representation,
)


@dataclass(frozen=True)
class Point:
    recorded_at: datetime
    wgs_latitude: float
    wgs_longitude: float
    horizontal_accuracy_meters: float | None = 8.0
    recorded_speed_mps: float | None = 10.0
    course_degrees: float | None = 90.0


def points(count: int = 8) -> tuple[Point, ...]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Point(base + timedelta(seconds=index * 10), 31.2, 121.5 + index * 0.001)
        for index in range(count)
    )


def modes(count: int = 8, mode: str = "car") -> ModeResult:
    leg = MobilityLeg(
        trip_index=0,
        sequence_number=0,
        track_segment_index=0,
        point_indices=tuple(range(count)),
        observed_started_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_ended_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=70),
        selected_mode_code=mode,
        selected_mode_level="detail",
        confidence=0.9,
        features=None,  # type: ignore[arg-type]
        mode_scores=(),
        evidence={},
    )
    return ModeResult(feature_schema_version="test", legs=(leg,))


class FakeMatcher:
    name = "fake_hmm"
    version = "1"

    def __init__(self, *, residual: float = 2.0, discontinuity: bool = False) -> None:
        self.residual = residual
        self.discontinuity = discontinuity
        self.requests: list[MapMatchRequest] = []

    def match(self, request: MapMatchRequest, config: MapMatchConfig) -> MatcherResponse:
        self.requests.append(request)
        observations = tuple(
            MatchedObservation(
                point_index=point_index,
                latitude=latitude,
                longitude=longitude,
                match_type="matched",
                edge_id=str(position),
                way_id=100 + position,
                distance_from_trace_point_meters=self.residual,
                route_position=float(position),
                begin_route_discontinuity=self.discontinuity and position == 1,
            )
            for position, (point_index, (latitude, longitude)) in enumerate(
                zip(request.point_indices, request.coordinates)
            )
        )
        return MatcherResponse(
            path=request.coordinates,
            observations=observations,
            edge_ids=tuple(str(index) for index in range(len(observations) - 1)),
            way_ids=tuple(100 + index for index in range(len(observations) - 1)),
            provider_metadata={},
        )


class MapMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = MapSnapshotRef(
            id=UUID("00000000-0000-0000-0000-000000000123"),
            provider="osm_valhalla",
            dataset_name="test",
            snapshot_version="2026-01-01",
        )

    def test_requests_are_chunked_with_overlap_inside_one_leg(self) -> None:
        matcher = FakeMatcher()
        result = match_trace(
            points(),
            modes(),
            matcher,
            self.snapshot,
            config=MapMatchConfig(max_points_per_request=4),
        )

        self.assertEqual(
            [request.point_indices for request in matcher.requests],
            [(0, 1, 2, 3), (3, 4, 5, 6), (5, 6, 7)],
        )
        self.assertTrue(result.preferred)
        self.assertEqual(result.moving_coverage, 1.0)

    def test_route_discontinuity_rejects_segment_and_keeps_fallback(self) -> None:
        result = match_trace(points(), modes(), FakeMatcher(discontinuity=True), self.snapshot)

        self.assertFalse(result.preferred)
        self.assertEqual(result.segments[0].status, "rejected")
        self.assertIn("route_discontinuity", result.segments[0].reason_codes)

    def test_unsupported_mode_is_not_forced_onto_roads(self) -> None:
        matcher = FakeMatcher()
        result = match_trace(points(), modes(mode="train"), matcher, self.snapshot)

        self.assertEqual(matcher.requests, [])
        self.assertEqual(result.fallback_reason, "no_supported_mobility_legs")

    def test_hybrid_representation_keeps_continuity_segment_and_route_turn(self) -> None:
        source = points(3)
        base_vertices = tuple(
            TrajectoryVertex(
                sequence_number=index,
                point_index=index,
                recorded_at=point.recorded_at,
                latitude=point.wgs_latitude,
                longitude=point.wgs_longitude,
                importance_meters=None if index in (0, 2) else 0.0,
                is_interpolated=False,
                anchor_reasons=("boundary",) if index in (0, 2) else (),
            )
            for index, point in enumerate(source)
        )
        base = TrajectoryRepresentation(
            variant_kind="smoothed_gps",
            importance_algorithm="test",
            segments=(
                TrajectorySegment(0, base_vertices, source[0].recorded_at, source[-1].recorded_at),
            ),
            chunks=(),
        )
        routed = (
            base_vertices[0],
            TrajectoryVertex(
                None,
                None,
                source[0].recorded_at + timedelta(seconds=5),
                31.2005,
                121.5005,
                0.0,
                True,
                (),
            ),
            base_vertices[1],
            base_vertices[2],
        )

        hybrid = build_hybrid_trajectory_representation(
            base,
            [((0, 1, 2), routed)],
        )

        self.assertEqual(hybrid.variant_kind, "map_matched")
        self.assertEqual(len(hybrid.segments), 1)
        self.assertEqual(len(hybrid.segments[0].vertices), 4)
        self.assertTrue(hybrid.segments[0].vertices[1].is_interpolated)

    def test_hybrid_representation_merges_overlapping_matcher_chunks(self) -> None:
        source = points(4)
        base_vertices = tuple(
            TrajectoryVertex(
                index,
                index,
                item.recorded_at,
                item.wgs_latitude,
                item.wgs_longitude,
                None if index in (0, 3) else 0.0,
                False,
                ("boundary",) if index in (0, 3) else (),
            )
            for index, item in enumerate(source)
        )
        base = TrajectoryRepresentation(
            "smoothed_gps",
            "test",
            (TrajectorySegment(0, base_vertices, source[0].recorded_at, source[-1].recorded_at),),
            (),
        )

        hybrid = build_hybrid_trajectory_representation(
            base,
            [((0, 1, 2), base_vertices[:3]), ((1, 2, 3), base_vertices[1:])],
        )

        self.assertEqual(
            [vertex.point_index for vertex in hybrid.segments[0].vertices],
            [0, 1, 2, 3],
        )

    def test_valhalla_adapter_blocks_remote_private_trace_upload(self) -> None:
        with self.assertRaisesRegex(ValueError, "private trajectory"):
            ValhallaMapMatcher("https://public-routing.example.com")
        ValhallaMapMatcher("http://127.0.0.1:8002")

    def test_polyline6_decoder_uses_six_digit_precision(self) -> None:
        self.assertEqual(decode_polyline6("_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI"), (
            (38.5, -120.2),
            (40.7, -120.95),
            (43.252, -126.453),
        ))


if __name__ == "__main__":
    unittest.main()
