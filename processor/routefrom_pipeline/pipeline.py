from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from routefrom_pipeline.continuity import (
    ContinuityConfig,
    ContinuityResult,
    select_continuity,
)
from routefrom_pipeline.motion import MotionConfig, MotionResult, infer_motion
from routefrom_pipeline.quality import (
    AnomalyConfig,
    LocatedObservation,
    PointAssessment,
    PointFeatures,
    assess_points,
    build_point_features,
)
from routefrom_pipeline.stays import StayConfig, StayResult, detect_stays

ALGORITHM_VERSION = "quality-continuity-motion-stays-v1"


@dataclass(frozen=True, slots=True)
class ProcessingConfig:
    interval_window: int = 6
    anomaly: AnomalyConfig = AnomalyConfig()
    continuity: ContinuityConfig = ContinuityConfig()
    motion: MotionConfig = MotionConfig()
    stays: StayConfig = StayConfig()


@dataclass(frozen=True, slots=True)
class ProcessedTrace:
    algorithm_version: str
    points: tuple[LocatedObservation, ...]
    features: tuple[PointFeatures, ...]
    assessments: tuple[PointAssessment, ...]
    continuity: ContinuityResult
    motion: MotionResult
    stays: StayResult


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
    stays = detect_stays(
        normalized_points,
        assessments,
        continuity,
        motion,
        config=config.stays,
    )
    return ProcessedTrace(
        algorithm_version=ALGORITHM_VERSION,
        points=normalized_points,
        features=features,
        assessments=assessments,
        continuity=continuity,
        motion=motion,
        stays=stays,
    )
