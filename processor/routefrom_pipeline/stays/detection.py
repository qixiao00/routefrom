from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Sequence

from routefrom_pipeline.continuity import ContinuityResult
from routefrom_pipeline.motion import MotionResult, MotionState
from routefrom_pipeline.quality import (
    LocatedObservation,
    PointAssessment,
    bearing_degrees,
    haversine_meters,
)


class StationaryEventType(StrEnum):
    VISIT = "visit"
    TRANSPORT_PAUSE = "transport_pause"
    UNCERTAIN_STOP = "uncertain_stop"


@dataclass(frozen=True, slots=True)
class StayConfig:
    accuracy_floor_meters: float = 5.0
    accuracy_cap_meters: float = 100.0
    spatial_scale_floor_meters: float = 20.0
    spatial_accuracy_multiplier: float = 2.5
    spatial_scale_cap_meters: float = 150.0
    duration_evidence_center_seconds: float = 4 * 60
    duration_evidence_softness_seconds: float = 2 * 60
    effective_points_evidence_scale: float = 2.5
    transport_pause_duration_center_seconds: float = 2 * 60
    transport_pause_duration_softness_seconds: float = 60.0
    direction_alignment_softness_degrees: float = 35.0
    semantic_context_points: int = 3


@dataclass(frozen=True, slots=True)
class StationaryEvent:
    event_type: StationaryEventType
    point_indices: tuple[int, ...]
    observed_started_at: datetime
    observed_ended_at: datetime
    possible_started_at: datetime
    possible_ended_at: datetime
    observed_duration_seconds: float
    possible_duration_seconds: float
    centroid_latitude: float
    centroid_longitude: float
    spatial_radius_meters: float
    adaptive_spatial_scale_meters: float
    effective_point_count: float
    confidence: float
    visit_probability: float
    transport_pause_probability: float
    uncertain_stop_probability: float
    arrival_confidence: float
    departure_confidence: float
    open_start: bool
    open_end: bool
    evidence: dict[str, float | bool | str]


@dataclass(frozen=True, slots=True)
class StayResult:
    events: tuple[StationaryEvent, ...]


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1.0 / (1.0 + exponential)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _weighted_centroid_and_radius(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    indices: Sequence[int],
    config: StayConfig,
) -> tuple[float, float, float, float, float]:
    weights: list[float] = []
    accuracies: list[float] = []
    for index in indices:
        accuracy = points[index].horizontal_accuracy_meters
        bounded_accuracy = min(
            config.accuracy_cap_meters,
            max(config.accuracy_floor_meters, accuracy or config.accuracy_cap_meters),
        )
        accuracies.append(bounded_accuracy)
        weights.append(assessments[index].stay_weight / (bounded_accuracy * bounded_accuracy))
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [1.0] * len(indices)
        total_weight = float(len(indices))
    latitude = sum(
        points[index].wgs_latitude * weight for index, weight in zip(indices, weights)
    ) / total_weight
    longitude = sum(
        points[index].wgs_longitude * weight for index, weight in zip(indices, weights)
    ) / total_weight
    distances = sorted(
        haversine_meters(
            latitude,
            longitude,
            points[index].wgs_latitude,
            points[index].wgs_longitude,
        )
        for index in indices
    )
    percentile_index = min(len(distances) - 1, math.floor(0.9 * len(distances)))
    radius = distances[percentile_index]
    median_accuracy = statistics.median(accuracies)
    adaptive_scale = min(
        config.spatial_scale_cap_meters,
        max(
            config.spatial_scale_floor_meters,
            median_accuracy * config.spatial_accuracy_multiplier,
        ),
    )
    effective_point_count = sum(assessments[index].stay_weight for index in indices)
    return latitude, longitude, radius, adaptive_scale, effective_point_count


def _midpoint_time(left: datetime, right: datetime) -> datetime:
    return left + (right - left) / 2


def _segment_context(
    continuity: ContinuityResult,
    event_indices: Sequence[int],
    context_points: int,
) -> tuple[
    int | None,
    int | None,
    tuple[int, ...],
    tuple[int, ...],
    bool,
    bool,
]:
    event_set = set(event_indices)
    for segment in continuity.segments:
        positions = [position for position, index in enumerate(segment) if index in event_set]
        if not positions:
            continue
        first_position = min(positions)
        last_position = max(positions)
        previous_index = segment[first_position - 1] if first_position > 0 else None
        next_index = segment[last_position + 1] if last_position + 1 < len(segment) else None
        return (
            previous_index,
            next_index,
            tuple(segment[max(0, first_position - context_points) : first_position]),
            tuple(segment[last_position + 1 : last_position + 1 + context_points]),
            first_position == 0,
            last_position == len(segment) - 1,
        )
    return None, None, (), (), True, True


def _approach_departure_alignment(
    points: Sequence[LocatedObservation],
    event_indices: Sequence[int],
    previous_index: int | None,
    next_index: int | None,
    config: StayConfig,
) -> float:
    if previous_index is None or next_index is None:
        return 0.5
    arrival_bearing = bearing_degrees(
        points[previous_index].wgs_latitude,
        points[previous_index].wgs_longitude,
        points[event_indices[0]].wgs_latitude,
        points[event_indices[0]].wgs_longitude,
    )
    departure_bearing = bearing_degrees(
        points[event_indices[-1]].wgs_latitude,
        points[event_indices[-1]].wgs_longitude,
        points[next_index].wgs_latitude,
        points[next_index].wgs_longitude,
    )
    difference = abs((arrival_bearing - departure_bearing + 180.0) % 360.0 - 180.0)
    return _sigmoid(
        (90.0 - difference) / config.direction_alignment_softness_degrees
    )


def _semantic_probabilities(
    duration_seconds: float,
    spatial_consistency: float,
    moving_before: bool,
    moving_after: bool,
    direction_alignment: float,
    boundary_completeness: float,
    config: StayConfig,
) -> tuple[float, float, float, dict[str, float | bool | str]]:
    duration_evidence = _sigmoid(
        (duration_seconds - config.duration_evidence_center_seconds)
        / config.duration_evidence_softness_seconds
    )
    short_pause_evidence = _sigmoid(
        (config.transport_pause_duration_center_seconds - duration_seconds)
        / config.transport_pause_duration_softness_seconds
    )
    moving_context = float(moving_before and moving_after)
    pause_score = (
        0.05
        + 0.75 * moving_context * short_pause_evidence * direction_alignment
        + 0.10 * (1.0 - spatial_consistency)
    )
    visit_score = (
        0.05
        + 0.72 * duration_evidence * spatial_consistency
        + 0.12
        * spatial_consistency
        * (1.0 - moving_context)
        * boundary_completeness
    )
    uncertain_score = (
        0.12
        + 0.40 * (1.0 - spatial_consistency)
        + 0.25 * (1.0 - abs(duration_evidence - 0.5) * 2.0)
        + 0.35 * (1.0 - boundary_completeness)
    )
    total = visit_score + pause_score + uncertain_score
    evidence: dict[str, float | bool | str] = {
        "duration_evidence": duration_evidence,
        "short_pause_evidence": short_pause_evidence,
        "spatial_consistency": spatial_consistency,
        "moving_before": moving_before,
        "moving_after": moving_after,
        "direction_alignment": direction_alignment,
        "boundary_completeness": boundary_completeness,
        "semantic_stage": "physical_context_only",
    }
    return visit_score / total, pause_score / total, uncertain_score / total, evidence


def detect_stays(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    continuity: ContinuityResult,
    motion: MotionResult,
    *,
    config: StayConfig = StayConfig(),
) -> StayResult:
    if len(points) != len(assessments):
        raise ValueError("points and assessments must have equal length")
    events: list[StationaryEvent] = []
    for episode in motion.episodes:
        if episode.state != MotionState.STATIONARY:
            continue
        indices = episode.point_indices
        (
            centroid_latitude,
            centroid_longitude,
            radius,
            adaptive_scale,
            effective_point_count,
        ) = _weighted_centroid_and_radius(points, assessments, indices, config)
        (
            previous_index,
            next_index,
            previous_context,
            next_context,
            open_start,
            open_end,
        ) = _segment_context(continuity, indices, config.semantic_context_points)
        possible_started_at = (
            _midpoint_time(points[previous_index].recorded_at, points[indices[0]].recorded_at)
            if previous_index is not None
            else points[indices[0]].recorded_at
        )
        possible_ended_at = (
            _midpoint_time(points[indices[-1]].recorded_at, points[next_index].recorded_at)
            if next_index is not None
            else points[indices[-1]].recorded_at
        )
        observed_duration = (
            points[indices[-1]].recorded_at - points[indices[0]].recorded_at
        ).total_seconds()
        possible_duration = (possible_ended_at - possible_started_at).total_seconds()
        spatial_consistency = 1.0 / (1.0 + radius / max(1.0, adaptive_scale))
        effective_point_evidence = 1.0 - math.exp(
            -effective_point_count / config.effective_points_evidence_scale
        )
        direction_alignment = _approach_departure_alignment(
            points, indices, previous_index, next_index, config
        )
        moving_before = any(
            motion.state_by_point[index] == MotionState.MOVING
            for index in previous_context
        )
        moving_after = any(
            motion.state_by_point[index] == MotionState.MOVING for index in next_context
        )
        boundary_completeness = max(
            0.0,
            1.0 - 0.25 * float(open_start) - 0.25 * float(open_end),
        )
        (
            visit_probability,
            transport_pause_probability,
            uncertain_stop_probability,
            semantic_evidence,
        ) = _semantic_probabilities(
            observed_duration,
            spatial_consistency,
            moving_before,
            moving_after,
            direction_alignment,
            boundary_completeness,
            config,
        )
        probabilities = {
            StationaryEventType.VISIT: visit_probability,
            StationaryEventType.TRANSPORT_PAUSE: transport_pause_probability,
            StationaryEventType.UNCERTAIN_STOP: uncertain_stop_probability,
        }
        event_type = max(probabilities, key=probabilities.__getitem__)
        confidence = max(
            0.0,
            min(
                1.0,
                episode.confidence
                * (0.55 + 0.25 * spatial_consistency + 0.20 * effective_point_evidence),
            ),
        )
        events.append(
            StationaryEvent(
                event_type=event_type,
                point_indices=indices,
                observed_started_at=points[indices[0]].recorded_at,
                observed_ended_at=points[indices[-1]].recorded_at,
                possible_started_at=possible_started_at,
                possible_ended_at=possible_ended_at,
                observed_duration_seconds=observed_duration,
                possible_duration_seconds=possible_duration,
                centroid_latitude=centroid_latitude,
                centroid_longitude=centroid_longitude,
                spatial_radius_meters=radius,
                adaptive_spatial_scale_meters=adaptive_scale,
                effective_point_count=effective_point_count,
                confidence=confidence,
                visit_probability=visit_probability,
                transport_pause_probability=transport_pause_probability,
                uncertain_stop_probability=uncertain_stop_probability,
                arrival_confidence=episode.start_boundary_confidence,
                departure_confidence=episode.end_boundary_confidence,
                open_start=open_start,
                open_end=open_end,
                evidence={
                    **semantic_evidence,
                    "motion_confidence": episode.confidence,
                    "effective_point_evidence": effective_point_evidence,
                },
            )
        )
    return StayResult(events=tuple(events))
