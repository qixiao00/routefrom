from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from routefrom_pipeline.continuity import (
    ContinuityConfig,
    ContinuityResult,
    GapConfig,
    InferredConnection,
    ObservationGap,
    build_observation_gaps,
    select_continuity,
)
from routefrom_pipeline.motion import MotionConfig, MotionResult, infer_motion
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
from routefrom_pipeline.stays import StayConfig, StayResult, detect_stays
from routefrom_pipeline.trips import TripConfig, TripResult, segment_trips
from routefrom_pipeline.trajectory import (
    TrajectoryConfig,
    TrajectoryRepresentation,
    build_trajectory_representation,
)

ALGORITHM_VERSION = "quality-continuity-motion-stays-trips-places-modes-trajectory-v2"


@dataclass(frozen=True, slots=True)
class ProcessingConfig:
    interval_window: int = 6
    timezone: str = "Asia/Shanghai"
    anomaly: AnomalyConfig = AnomalyConfig()
    continuity: ContinuityConfig = ContinuityConfig()
    gaps: GapConfig = GapConfig()
    motion: MotionConfig = MotionConfig()
    stays: StayConfig = StayConfig()
    trips: TripConfig = TripConfig()
    places: PlaceConfig = PlaceConfig()
    modes: ModeConfig = ModeConfig()
    trajectory: TrajectoryConfig = TrajectoryConfig()


@dataclass(frozen=True, slots=True)
class ProcessedTrace:
    algorithm_version: str
    points: tuple[LocatedObservation, ...]
    features: tuple[PointFeatures, ...]
    assessments: tuple[PointAssessment, ...]
    continuity: ContinuityResult
    motion: MotionResult
    observation_gaps: tuple[ObservationGap, ...]
    inferred_connections: tuple[InferredConnection, ...]
    stays: StayResult
    trips: TripResult
    places: PlaceResult
    modes: ModeResult
    trajectory: TrajectoryRepresentation


def process_trace(
    points: Sequence[LocatedObservation],
    *,
    config: ProcessingConfig = ProcessingConfig(),
) -> ProcessedTrace:
    """Run the first deterministic RouteFrom processing stage.

    This stage never mutates source observations. It computes explainable point
    evidence, quality assessments, and a selected continuity graph. Stays, trips,
    and transport modes intentionally consume this result in later stages.
    """

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
    continuity = select_continuity(
        normalized_points,
        assessments,
        local_intervals_seconds=tuple(
            feature.local_interval_median_seconds for feature in features
        ),
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
    trajectory = build_trajectory_representation(
        normalized_points,
        continuity,
        stays,
        trips,
        modes,
        config=config.trajectory,
    )
    return ProcessedTrace(
        algorithm_version=ALGORITHM_VERSION,
        points=normalized_points,
        features=features,
        assessments=assessments,
        continuity=continuity,
        motion=motion,
        observation_gaps=observation_gaps,
        inferred_connections=inferred_connections,
        stays=stays,
        trips=trips,
        places=places,
        modes=modes,
        trajectory=trajectory,
    )
