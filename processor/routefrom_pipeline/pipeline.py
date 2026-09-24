from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from routefrom_pipeline.continuity import (
    ContinuityConfig,
    ContinuityResult,
    GapConfig,
    InferredConnection,
    ObservationGap,
    SamplingIntervalAssessment,
    SamplingModelConfig,
    build_observation_gaps,
    estimate_sampling_intervals,
    select_continuity,
)
from routefrom_pipeline.motion import MotionConfig, MotionResult, infer_motion
from routefrom_pipeline.map_matching import (
    MapMatchConfig,
    MapMatchResult,
    MapMatcher,
    MapSnapshotRef,
    match_trace,
)
from routefrom_pipeline.modes import ModeConfig, ModeResult, infer_transport_modes
from routefrom_pipeline.places import PlaceConfig, PlaceResult, resolve_places
from routefrom_pipeline.quality import (
    AnomalyConfig,
    LocatedObservation,
    PointAssessment,
    PointFeatures,
    assess_points,
    build_point_features,
)
from routefrom_pipeline.smoothing import (
    SmoothingConfig,
    SmoothingResult,
    smooth_trace,
)
from routefrom_pipeline.stays import StayConfig, StayResult, detect_stays
from routefrom_pipeline.trips import TripConfig, TripResult, segment_trips
from routefrom_pipeline.trajectory import (
    TrajectoryConfig,
    TrajectoryRepresentation,
    build_hybrid_trajectory_representation,
    build_trajectory_representation,
)

ALGORITHM_VERSION = "quality-continuity-sampling-survival-motion-stays-trips-places-modes-smoothing-map-matching-trajectory-v7"


@dataclass(frozen=True, slots=True)
class ProcessingConfig:
    interval_window: int = 6
    timezone: str = "Asia/Shanghai"
    anomaly: AnomalyConfig = AnomalyConfig()
    sampling: SamplingModelConfig = SamplingModelConfig()
    continuity: ContinuityConfig = ContinuityConfig()
    gaps: GapConfig = GapConfig()
    motion: MotionConfig = MotionConfig()
    stays: StayConfig = StayConfig()
    trips: TripConfig = TripConfig()
    places: PlaceConfig = PlaceConfig()
    modes: ModeConfig = ModeConfig()
    smoothing: SmoothingConfig = SmoothingConfig()
    map_matching: MapMatchConfig = MapMatchConfig()
    trajectory: TrajectoryConfig = TrajectoryConfig()


@dataclass(frozen=True, slots=True)
class ProcessedTrace:
    algorithm_version: str
    points: tuple[LocatedObservation, ...]
    features: tuple[PointFeatures, ...]
    assessments: tuple[PointAssessment, ...]
    sampling_intervals: tuple[SamplingIntervalAssessment, ...]
    continuity: ContinuityResult
    motion: MotionResult
    observation_gaps: tuple[ObservationGap, ...]
    inferred_connections: tuple[InferredConnection, ...]
    stays: StayResult
    trips: TripResult
    places: PlaceResult
    modes: ModeResult
    smoothing: SmoothingResult
    map_matching: MapMatchResult | None
    trajectory: TrajectoryRepresentation
    smoothed_trajectory: TrajectoryRepresentation
    map_matched_trajectory: TrajectoryRepresentation | None


def process_trace(
    points: Sequence[LocatedObservation],
    *,
    config: ProcessingConfig = ProcessingConfig(),
    map_matcher: MapMatcher | None = None,
    map_snapshot: MapSnapshotRef | None = None,
) -> ProcessedTrace:
    """Run the first deterministic RouteFrom processing stage.

    This stage never mutates source observations. It computes explainable point
    evidence, quality assessments, and a selected continuity graph. Stays, trips,
    and transport modes intentionally consume this result in later stages.
    """

    if (map_matcher is None) != (map_snapshot is None):
        raise ValueError("map_matcher and map_snapshot must be provided together")
    normalized_points = tuple(points)
    for previous, current in zip(normalized_points, normalized_points[1:]):
        if current.recorded_at <= previous.recorded_at:
            raise ValueError("points must be strictly ordered by recorded_at")

    features = tuple(
        build_point_features(
            normalized_points,
            interval_window=config.interval_window,
        )
    )
    assessments = tuple(assess_points(features, config.anomaly))
    sampling_intervals = estimate_sampling_intervals(
        normalized_points,
        features,
        config=config.sampling,
    )
    continuity = select_continuity(
        normalized_points,
        assessments,
        local_intervals_seconds=tuple(
            feature.local_interval_median_seconds for feature in features
        ),
        sampling_intervals=sampling_intervals,
        config=config.continuity,
    )
    motion = infer_motion(
        normalized_points,
        assessments,
        continuity,
        config=config.motion,
    )
    observation_gaps, inferred_connections = build_observation_gaps(
        normalized_points,
        assessments,
        continuity,
        motion,
        config=config.gaps,
    )
    stays = detect_stays(
        normalized_points,
        assessments,
        continuity,
        motion,
        config=config.stays,
    )
    trips = segment_trips(
        normalized_points,
        continuity,
        motion,
        stays,
        observation_gaps,
        inferred_connections,
        config=config.trips,
    )
    places = resolve_places(
        stays,
        eligible_event_indices=trips.confirmed_visit_event_indices,
        timezone=config.timezone,
        config=config.places,
    )
    modes = infer_transport_modes(
        normalized_points,
        assessments,
        stays,
        trips,
        config=config.modes,
    )
    smoothing_anchors = {
        point_index
        for segment in continuity.segments
        if segment
        for point_index in (segment[0], segment[-1])
    }
    for event in stays.events:
        smoothing_anchors.update((event.point_indices[0], event.point_indices[-1]))
    for trip in trips.trips:
        smoothing_anchors.update((trip.point_indices[0], trip.point_indices[-1]))
    for leg in modes.legs:
        smoothing_anchors.update((leg.point_indices[0], leg.point_indices[-1]))
    smoothing = smooth_trace(
        normalized_points,
        continuity,
        anchor_indices=tuple(sorted(smoothing_anchors)),
        config=config.smoothing,
    )
    trajectory = build_trajectory_representation(
        normalized_points,
        continuity,
        stays,
        trips,
        modes,
        config=config.trajectory,
    )
    smoothed_trajectory = build_trajectory_representation(
        normalized_points,
        continuity,
        stays,
        trips,
        modes,
        config=config.trajectory,
        coordinate_by_point=smoothing.coordinate_by_point,
        variant_kind="smoothed_gps",
    )
    map_matching = (
        match_trace(
            normalized_points,
            modes,
            map_matcher,
            map_snapshot,
            config=config.map_matching,
        )
        if map_matcher is not None and map_snapshot is not None
        else None
    )
    smoothing_guard_rate = (
        smoothing.displacement_limited_count / len(smoothing.points)
        if smoothing.points
        else 1.0
    )
    map_base = (
        smoothed_trajectory
        if smoothing.confidence >= 0.65 and smoothing_guard_rate <= 0.10
        else trajectory
    )
    map_matched_trajectory = (
        build_hybrid_trajectory_representation(
            map_base,
            [
                (segment.point_indices, segment.vertices)
                for segment in map_matching.segments
                if segment.accepted
            ],
            config=config.trajectory,
        )
        if map_matching is not None and any(
            segment.accepted for segment in map_matching.segments
        )
        else None
    )
    return ProcessedTrace(
        algorithm_version=ALGORITHM_VERSION,
        points=normalized_points,
        features=features,
        assessments=assessments,
        sampling_intervals=sampling_intervals,
        continuity=continuity,
        motion=motion,
        observation_gaps=observation_gaps,
        inferred_connections=inferred_connections,
        stays=stays,
        trips=trips,
        places=places,
        modes=modes,
        smoothing=smoothing,
        map_matching=map_matching,
        trajectory=trajectory,
        smoothed_trajectory=smoothed_trajectory,
        map_matched_trajectory=map_matched_trajectory,
    )
