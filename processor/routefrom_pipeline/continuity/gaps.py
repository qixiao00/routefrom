from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Sequence

from routefrom_pipeline.motion import MotionResult, MotionState
from routefrom_pipeline.model import QualityStatus
from routefrom_pipeline.quality import LocatedObservation, PointAssessment, haversine_meters

from .graph import ContinuityResult
from .sampling import SamplingContext


class ObservationGapCause(StrEnum):
    SOURCE_SAMPLING_GAP = "source_sampling_gap"
    EXCLUDED_BLOCK = "excluded_block"
    CONTINUITY_FAILURE = "continuity_failure"
    CLOCK_DISCONTINUITY = "clock_discontinuity"


class InferredConnectionKind(StrEnum):
    SAME_PLACE = "same_place"
    STRAIGHT_LINE_CONTEXT = "straight_line_context"


@dataclass(frozen=True, slots=True)
class ObservationGap:
    before_point_index: int
    after_point_index: int
    started_at: datetime
    ended_at: datetime
    elapsed_seconds: float
    displacement_meters: float
    cause: ObservationGapCause
    context_before: MotionState | None
    context_after: MotionState | None
    same_place_probability: float
    confidence: float
    reason_codes: tuple[str, ...]
    sampling_context: SamplingContext | None
    normal_wait_probability: float | None
    expected_interval_seconds: float | None


@dataclass(frozen=True, slots=True)
class InferredConnection:
    before_point_index: int
    after_point_index: int
    kind: InferredConnectionKind
    started_at: datetime
    ended_at: datetime
    displacement_meters: float
    estimated_path_distance_meters: float
    confidence: float
    displayable: bool
    counts_toward_confirmed_distance: bool
    counts_toward_confirmed_duration: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GapConfig:
    same_place_accuracy_multiplier: float = 2.5
    same_place_radius_floor_meters: float = 30.0
    same_place_radius_cap_meters: float = 250.0
    same_place_softness_ratio: float = 0.35
    route_speed_center_mps: float = 55.0
    route_speed_softness_mps: float = 18.0
    route_temporal_decay_seconds: float = 6 * 60 * 60
    inferred_route_distance_multiplier: float = 1.25
    display_confidence_min: float = 0.65


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1.0 / (1.0 + exponential)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _same_place_probability(
    points: Sequence[LocatedObservation],
    before_index: int,
    after_index: int,
    displacement_meters: float,
    motion: MotionResult,
    config: GapConfig,
) -> float:
    accuracies = [
        accuracy
        for accuracy in (
            points[before_index].horizontal_accuracy_meters,
            points[after_index].horizontal_accuracy_meters,
        )
        if accuracy is not None
    ]
    accuracy = sum(accuracies) / len(accuracies) if accuracies else 100.0
    radius = min(
        config.same_place_radius_cap_meters,
        max(
            config.same_place_radius_floor_meters,
            accuracy * config.same_place_accuracy_multiplier,
        ),
    )
    spatial_probability = _sigmoid(
        (radius - displacement_meters)
        / max(5.0, radius * config.same_place_softness_ratio)
    )
    before_state = motion.state_by_point[before_index]
    after_state = motion.state_by_point[after_index]
    if before_state == MotionState.STATIONARY and after_state == MotionState.STATIONARY:
        spatial_probability = min(1.0, spatial_probability + 0.08)
    return spatial_probability


def _gap_cause(
    reason_codes: Sequence[str],
    skipped_assessments: Sequence[PointAssessment],
) -> ObservationGapCause:
    if "non_increasing_time" in reason_codes:
        return ObservationGapCause.CLOCK_DISCONTINUITY
    if skipped_assessments and all(
        assessment.quality_status == QualityStatus.EXCLUDED
        for assessment in skipped_assessments
    ):
        return ObservationGapCause.EXCLUDED_BLOCK
    if "observation_gap_unknown" in reason_codes:
        return ObservationGapCause.SOURCE_SAMPLING_GAP
    return ObservationGapCause.CONTINUITY_FAILURE


def build_observation_gaps(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    continuity: ContinuityResult,
    motion: MotionResult,
    *,
    config: GapConfig = GapConfig(),
) -> tuple[tuple[ObservationGap, ...], tuple[InferredConnection, ...]]:
    if len(points) != len(assessments):
        raise ValueError("points and assessments must have equal length")
    gaps: list[ObservationGap] = []
    connections: list[InferredConnection] = []
    for edge in continuity.edges:
        if edge.kind != "break":
            continue
        before_index = edge.from_index
        after_index = edge.to_index
        elapsed_seconds = edge.elapsed_seconds
        displacement = edge.displacement_meters
        skipped_assessments = assessments[before_index + 1 : after_index]
        same_place_probability = _same_place_probability(
            points,
            before_index,
            after_index,
            displacement,
            motion,
            config,
        )
        cause = _gap_cause(edge.reason_codes, skipped_assessments)
        if elapsed_seconds <= 0:
            confidence = 0.5
        elif edge.normal_wait_probability is not None:
            model_confidence = edge.sampling_model_confidence or 0.5
            confidence = max(
                0.5,
                min(1.0, (1.0 - edge.normal_wait_probability) * model_confidence),
            )
        else:
            confidence = 0.75
        gap = ObservationGap(
            before_point_index=before_index,
            after_point_index=after_index,
            started_at=points[before_index].recorded_at,
            ended_at=points[after_index].recorded_at,
            elapsed_seconds=max(0.0, elapsed_seconds),
            displacement_meters=displacement,
            cause=cause,
            context_before=motion.state_by_point[before_index],
            context_after=motion.state_by_point[after_index],
            same_place_probability=same_place_probability,
            confidence=confidence,
            reason_codes=edge.reason_codes,
            sampling_context=edge.sampling_context,
            normal_wait_probability=edge.normal_wait_probability,
            expected_interval_seconds=edge.expected_interval_seconds,
        )
        gaps.append(gap)

        calculated_speed = (
            displacement / elapsed_seconds if elapsed_seconds > 0 else math.inf
        )
        speed_probability = _sigmoid(
            (config.route_speed_center_mps - calculated_speed)
            / config.route_speed_softness_mps
        )
        temporal_probability = math.exp(
            -max(0.0, elapsed_seconds) / config.route_temporal_decay_seconds
        )
        route_probability = speed_probability * temporal_probability
        if same_place_probability >= route_probability:
            kind = InferredConnectionKind.SAME_PLACE
            connection_confidence = same_place_probability
            estimated_distance = displacement
            reasons = ("same_place_on_both_sides", "gap_remains_unobserved")
        else:
            kind = InferredConnectionKind.STRAIGHT_LINE_CONTEXT
            connection_confidence = route_probability
            estimated_distance = displacement * config.inferred_route_distance_multiplier
            reasons = (
                "plausible_elapsed_speed",
                "route_geometry_not_map_matched",
                "gap_remains_unobserved",
            )
        connections.append(
            InferredConnection(
                before_point_index=before_index,
                after_point_index=after_index,
                kind=kind,
                started_at=points[before_index].recorded_at,
                ended_at=points[after_index].recorded_at,
                displacement_meters=displacement,
                estimated_path_distance_meters=estimated_distance,
                confidence=connection_confidence,
                displayable=connection_confidence >= config.display_confidence_min,
                counts_toward_confirmed_distance=False,
                counts_toward_confirmed_duration=False,
                reason_codes=reasons,
            )
        )
    return tuple(gaps), tuple(connections)
