from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Sequence

from routefrom_pipeline.continuity.graph import ContinuityResult
from routefrom_pipeline.quality import LocatedObservation, PointAssessment, haversine_meters


class MotionState(StrEnum):
    STATIONARY = "stationary"
    MOVING = "moving"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class MotionConfig:
    context_radius_points: int = 4
    context_max_seconds: float = 5 * 60
    accuracy_floor_meters: float = 5.0
    accuracy_cap_meters: float = 100.0
    stationary_speed_center_mps: float = 1.4
    stationary_speed_softness_mps: float = 0.65
    moving_speed_center_mps: float = 1.2
    moving_speed_softness_mps: float = 0.85
    stationary_radius_floor_meters: float = 20.0
    stationary_accuracy_multiplier: float = 2.2
    stationary_radius_cap_meters: float = 120.0
    direct_transition_penalty: float = 2.1
    uncertain_transition_penalty: float = 0.75
    short_stationary_prior_seconds: float = 75.0
    short_stationary_exit_penalty: float = 2.0
    short_moving_prior_seconds: float = 20.0
    short_moving_exit_penalty: float = 0.8


@dataclass(frozen=True, slots=True)
class PointMotionEvidence:
    point_index: int
    calculated_speed_mps: float | None
    local_radius_meters: float | None
    adaptive_stationary_radius_meters: float
    stationary_probability: float
    moving_probability: float
    uncertain_probability: float
    reason_codes: tuple[str, ...]

    def probability_for(self, state: MotionState) -> float:
        if state == MotionState.STATIONARY:
            return self.stationary_probability
        if state == MotionState.MOVING:
            return self.moving_probability
        return self.uncertain_probability


@dataclass(frozen=True, slots=True)
class MotionEpisode:
    state: MotionState
    point_indices: tuple[int, ...]
    observed_started_at: datetime
    observed_ended_at: datetime
    observed_duration_seconds: float
    confidence: float
    start_boundary_confidence: float
    end_boundary_confidence: float


@dataclass(frozen=True, slots=True)
class MotionResult:
    evidence: tuple[PointMotionEvidence, ...]
    state_by_point: tuple[MotionState | None, ...]
    episodes: tuple[MotionEpisode, ...]


_STATES = (MotionState.STATIONARY, MotionState.MOVING, MotionState.UNCERTAIN)


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1.0 / (1.0 + exponential)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _safe_log(value: float) -> float:
    return math.log(max(1e-9, min(1.0, value)))


def _weighted_centroid(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    indices: Sequence[int],
    config: MotionConfig,
) -> tuple[float, float]:
    weighted_latitude = 0.0
    weighted_longitude = 0.0
    total_weight = 0.0
    for index in indices:
        accuracy = points[index].horizontal_accuracy_meters
        bounded_accuracy = min(
            config.accuracy_cap_meters,
            max(config.accuracy_floor_meters, accuracy or config.accuracy_cap_meters),
        )
        weight = assessments[index].stay_weight / (bounded_accuracy * bounded_accuracy)
        weighted_latitude += points[index].wgs_latitude * weight
        weighted_longitude += points[index].wgs_longitude * weight
        total_weight += weight
    if total_weight <= 0:
        point = points[indices[len(indices) // 2]]
        return point.wgs_latitude, point.wgs_longitude
    return weighted_latitude / total_weight, weighted_longitude / total_weight


def _context_indices(
    points: Sequence[LocatedObservation],
    segment: Sequence[int],
    position: int,
    config: MotionConfig,
) -> tuple[int, ...]:
    center_index = segment[position]
    center_time = points[center_index].recorded_at
    start = max(0, position - config.context_radius_points)
    end = min(len(segment), position + config.context_radius_points + 1)
    return tuple(
        index
        for index in segment[start:end]
        if abs((points[index].recorded_at - center_time).total_seconds())
        <= config.context_max_seconds
    )


def _adjacent_speeds(
    points: Sequence[LocatedObservation],
    segment: Sequence[int],
    position: int,
) -> tuple[float, ...]:
    point_index = segment[position]
    speeds: list[float] = []
    for neighbor_position in (position - 1, position + 1):
        if not 0 <= neighbor_position < len(segment):
            continue
        neighbor_index = segment[neighbor_position]
        elapsed = abs(
            (points[neighbor_index].recorded_at - points[point_index].recorded_at).total_seconds()
        )
        if elapsed <= 0:
            continue
        speeds.append(
            haversine_meters(
                points[point_index].wgs_latitude,
                points[point_index].wgs_longitude,
                points[neighbor_index].wgs_latitude,
                points[neighbor_index].wgs_longitude,
            )
            / elapsed
        )
    return tuple(speeds)


def _build_evidence_for_segment(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    segment: Sequence[int],
    config: MotionConfig,
) -> dict[int, PointMotionEvidence]:
    result: dict[int, PointMotionEvidence] = {}
    for position, point_index in enumerate(segment):
        point = points[point_index]
        context = _context_indices(points, segment, position, config)
        centroid_latitude, centroid_longitude = _weighted_centroid(
            points, assessments, context, config
        )
        distances = [
            haversine_meters(
                centroid_latitude,
                centroid_longitude,
                points[index].wgs_latitude,
                points[index].wgs_longitude,
            )
            for index in context
        ]
        local_radius = statistics.median(distances) if distances else None
        context_accuracies = [
            points[index].horizontal_accuracy_meters
            for index in context
            if points[index].horizontal_accuracy_meters is not None
        ]
        median_accuracy = (
            statistics.median(context_accuracies)
            if context_accuracies
            else config.accuracy_cap_meters
        )
        adaptive_radius = min(
            config.stationary_radius_cap_meters,
            max(
                config.stationary_radius_floor_meters,
                median_accuracy * config.stationary_accuracy_multiplier,
            ),
        )

        speeds = _adjacent_speeds(points, segment, position)
        calculated_speed = statistics.median(speeds) if speeds else None
        recorded_speed = point.recorded_speed_mps
        speed_for_emission = calculated_speed if calculated_speed is not None else recorded_speed
        if speed_for_emission is None:
            stationary_speed_probability = 0.5
            moving_speed_probability = 0.35
        else:
            stationary_speed_probability = _sigmoid(
                (config.stationary_speed_center_mps - speed_for_emission)
                / config.stationary_speed_softness_mps
            )
            moving_speed_probability = _sigmoid(
                (speed_for_emission - config.moving_speed_center_mps)
                / config.moving_speed_softness_mps
            )
        radius_probability = _sigmoid(
            (adaptive_radius - (local_radius or 0.0)) / max(5.0, adaptive_radius * 0.35)
        )
        recorded_stationary_probability = (
            _sigmoid(
                (config.stationary_speed_center_mps - recorded_speed)
                / config.stationary_speed_softness_mps
            )
            if recorded_speed is not None
            else 0.5
        )
        quality = assessments[point_index].stay_weight
        stationary_score = quality * (
            0.55 * stationary_speed_probability
            + 0.30 * radius_probability
            + 0.15 * recorded_stationary_probability
        )
        moving_score = quality * (
            0.72 * moving_speed_probability
            + 0.18 * (1.0 - radius_probability)
            + 0.10 * (1.0 - recorded_stationary_probability)
        )
        disagreement = abs(stationary_speed_probability - radius_probability)
        uncertain_score = 0.18 + 0.55 * (1.0 - quality) + 0.35 * disagreement
        score_total = max(1e-9, stationary_score + moving_score + uncertain_score)
        stationary_probability = stationary_score / score_total
        moving_probability = moving_score / score_total
        uncertain_probability = uncertain_score / score_total

        reasons: list[str] = []
        if stationary_speed_probability >= 0.7:
            reasons.append("low_observed_speed")
        if radius_probability >= 0.7:
            reasons.append("spatially_compact_context")
        if moving_speed_probability >= 0.7:
            reasons.append("sustained_displacement")
        if quality < 0.5:
            reasons.append("low_stay_evidence_weight")
        if disagreement >= 0.5:
            reasons.append("speed_dispersion_disagree")

        result[point_index] = PointMotionEvidence(
            point_index=point_index,
            calculated_speed_mps=calculated_speed,
            local_radius_meters=local_radius,
            adaptive_stationary_radius_meters=adaptive_radius,
            stationary_probability=stationary_probability,
            moving_probability=moving_probability,
            uncertain_probability=uncertain_probability,
            reason_codes=tuple(reasons),
        )
    return result


def _transition_penalty(
    previous: MotionState,
    current: MotionState,
    run_duration_seconds: float,
    config: MotionConfig,
) -> float:
    if previous == current:
        return 0.0
    penalty = (
        config.uncertain_transition_penalty
        if MotionState.UNCERTAIN in (previous, current)
        else config.direct_transition_penalty
    )
    if previous == MotionState.STATIONARY:
        shortfall = max(
            0.0,
            1.0 - run_duration_seconds / config.short_stationary_prior_seconds,
        )
        penalty += config.short_stationary_exit_penalty * shortfall
    elif previous == MotionState.MOVING:
        shortfall = max(0.0, 1.0 - run_duration_seconds / config.short_moving_prior_seconds)
        penalty += config.short_moving_exit_penalty * shortfall
    return penalty


def _decode_segment(
    points: Sequence[LocatedObservation],
    segment: Sequence[int],
    evidence_by_index: dict[int, PointMotionEvidence],
    config: MotionConfig,
) -> tuple[MotionState, ...]:
    if not segment:
        return ()
    scores: list[dict[MotionState, float]] = []
    previous_states: list[dict[MotionState, MotionState | None]] = []
    run_starts: list[dict[MotionState, int]] = []

    first_evidence = evidence_by_index[segment[0]]
    scores.append(
        {
            state: _safe_log(first_evidence.probability_for(state))
            for state in _STATES
        }
    )
    previous_states.append({state: None for state in _STATES})
    run_starts.append({state: 0 for state in _STATES})

    for position in range(1, len(segment)):
        point_evidence = evidence_by_index[segment[position]]
        current_scores: dict[MotionState, float] = {}
        current_previous: dict[MotionState, MotionState | None] = {}
        current_run_starts: dict[MotionState, int] = {}
        for current_state in _STATES:
            best_score = -math.inf
            best_previous: MotionState | None = None
            best_run_start = position
            for previous_state in _STATES:
                run_start = run_starts[position - 1][previous_state]
                run_duration = (
                    points[segment[position - 1]].recorded_at
                    - points[segment[run_start]].recorded_at
                ).total_seconds()
                candidate_score = (
                    scores[position - 1][previous_state]
                    + _safe_log(point_evidence.probability_for(current_state))
                    - _transition_penalty(
                        previous_state,
                        current_state,
                        run_duration,
                        config,
                    )
                )
                if candidate_score > best_score:
                    best_score = candidate_score
                    best_previous = previous_state
                    best_run_start = run_start if previous_state == current_state else position
            current_scores[current_state] = best_score
            current_previous[current_state] = best_previous
            current_run_starts[current_state] = best_run_start
        scores.append(current_scores)
        previous_states.append(current_previous)
        run_starts.append(current_run_starts)

    state = max(_STATES, key=lambda candidate: scores[-1][candidate])
    decoded_reversed: list[MotionState] = []
    for position in range(len(segment) - 1, -1, -1):
        decoded_reversed.append(state)
        previous_state = previous_states[position][state]
        if previous_state is not None:
            state = previous_state
    return tuple(reversed(decoded_reversed))


def _boundary_confidence(evidence: PointMotionEvidence, state: MotionState) -> float:
    chosen = evidence.probability_for(state)
    alternatives = [
        evidence.probability_for(candidate) for candidate in _STATES if candidate != state
    ]
    return max(0.0, min(1.0, 0.5 + (chosen - max(alternatives, default=0.0)) / 2.0))


def _episodes_from_states(
    points: Sequence[LocatedObservation],
    segment: Sequence[int],
    states: Sequence[MotionState],
    evidence_by_index: dict[int, PointMotionEvidence],
) -> list[MotionEpisode]:
    episodes: list[MotionEpisode] = []
    start = 0
    while start < len(segment):
        end = start + 1
        while end < len(segment) and states[end] == states[start]:
            end += 1
        indices = tuple(segment[start:end])
        state = states[start]
        probabilities = [
            evidence_by_index[index].probability_for(state) for index in indices
        ]
        episodes.append(
            MotionEpisode(
                state=state,
                point_indices=indices,
                observed_started_at=points[indices[0]].recorded_at,
                observed_ended_at=points[indices[-1]].recorded_at,
                observed_duration_seconds=(
                    points[indices[-1]].recorded_at - points[indices[0]].recorded_at
                ).total_seconds(),
                confidence=sum(probabilities) / len(probabilities),
                start_boundary_confidence=_boundary_confidence(
                    evidence_by_index[indices[0]], state
                ),
                end_boundary_confidence=_boundary_confidence(
                    evidence_by_index[indices[-1]], state
                ),
            )
        )
        start = end
    return episodes


def infer_motion(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    continuity: ContinuityResult,
    *,
    config: MotionConfig = MotionConfig(),
) -> MotionResult:
    if len(points) != len(assessments):
        raise ValueError("points and assessments must have equal length")
    evidence_by_index: dict[int, PointMotionEvidence] = {}
    decoded_by_index: dict[int, MotionState] = {}
    episodes: list[MotionEpisode] = []

    for segment in continuity.segments:
        segment_evidence = _build_evidence_for_segment(
            points, assessments, segment, config
        )
        evidence_by_index.update(segment_evidence)
        states = _decode_segment(points, segment, segment_evidence, config)
        decoded_by_index.update(zip(segment, states))
        episodes.extend(
            _episodes_from_states(points, segment, states, segment_evidence)
        )

    evidence = tuple(evidence_by_index[index] for index in sorted(evidence_by_index))
    state_by_point = tuple(decoded_by_index.get(index) for index in range(len(points)))
    return MotionResult(
        evidence=evidence,
        state_by_point=state_by_point,
        episodes=tuple(episodes),
    )
