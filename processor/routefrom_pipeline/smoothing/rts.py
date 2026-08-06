from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from routefrom_pipeline.continuity import ContinuityResult
from routefrom_pipeline.quality import LocatedObservation, haversine_meters


@dataclass(frozen=True, slots=True)
class SmoothingConfig:
    measurement_accuracy_floor_meters: float = 4.0
    measurement_accuracy_cap_meters: float = 120.0
    missing_accuracy_meters: float = 50.0
    acceleration_noise_mps2: float = 2.5
    initial_velocity_uncertainty_mps: float = 30.0
    anchor_accuracy_meters: float = 0.5
    displacement_limit_floor_meters: float = 15.0
    displacement_accuracy_multiplier: float = 2.5
    displacement_limit_cap_meters: float = 100.0


@dataclass(frozen=True, slots=True)
class SmoothedPoint:
    point_index: int
    latitude: float
    longitude: float
    displacement_from_observation_meters: float
    estimated_uncertainty_meters: float
    is_anchor: bool
    displacement_was_limited: bool


@dataclass(frozen=True, slots=True)
class SmoothingResult:
    points: tuple[SmoothedPoint, ...]
    segments: tuple[tuple[int, ...], ...]
    confidence: float
    coordinate_by_point: dict[int, tuple[float, float]]

    @property
    def displacement_limited_count(self) -> int:
        return sum(point.displacement_was_limited for point in self.points)


def _project(
    latitude: float,
    longitude: float,
    origin_latitude: float,
    origin_longitude: float,
) -> tuple[float, float]:
    radius = 6_371_008.8
    return (
        math.radians(longitude - origin_longitude)
        * radius
        * math.cos(math.radians(origin_latitude)),
        math.radians(latitude - origin_latitude) * radius,
    )


def _unproject(
    x: float,
    y: float,
    origin_latitude: float,
    origin_longitude: float,
) -> tuple[float, float]:
    radius = 6_371_008.8
    latitude = origin_latitude + math.degrees(y / radius)
    longitude = origin_longitude + math.degrees(
        x / (radius * math.cos(math.radians(origin_latitude)))
    )
    return latitude, longitude


Matrix2 = tuple[tuple[float, float], tuple[float, float]]


def _matmul(left: Matrix2, right: Matrix2) -> Matrix2:
    return (
        (
            left[0][0] * right[0][0] + left[0][1] * right[1][0],
            left[0][0] * right[0][1] + left[0][1] * right[1][1],
        ),
        (
            left[1][0] * right[0][0] + left[1][1] * right[1][0],
            left[1][0] * right[0][1] + left[1][1] * right[1][1],
        ),
    )


def _inverse(matrix: Matrix2) -> Matrix2:
    determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    if abs(determinant) < 1e-12:
        determinant = 1e-12
    return (
        (matrix[1][1] / determinant, -matrix[0][1] / determinant),
        (-matrix[1][0] / determinant, matrix[0][0] / determinant),
    )


def _filter_and_smooth_axis(
    measurements: Sequence[float],
    times: Sequence[float],
    variances: Sequence[float],
    config: SmoothingConfig,
) -> tuple[list[float], list[float]]:
    if len(measurements) == 1:
        return [measurements[0]], [variances[0]]
    initial_dt = max(1e-3, times[1] - times[0])
    state = (measurements[0], (measurements[1] - measurements[0]) / initial_dt)
    covariance = (
        (variances[0], 0.0),
        (0.0, config.initial_velocity_uncertainty_mps**2),
    )
    filtered_states = [state]
    filtered_covariances = [covariance]
    predicted_states = [state]
    predicted_covariances = [covariance]
    transitions: list[tuple[tuple[float, float], tuple[float, float]]] = []
    acceleration_variance = config.acceleration_noise_mps2**2

    for index in range(1, len(measurements)):
        dt = max(1e-3, times[index] - times[index - 1])
        transition = ((1.0, dt), (0.0, 1.0))
        transitions.append(transition)
        process_noise = (
            (acceleration_variance * dt**4 / 4, acceleration_variance * dt**3 / 2),
            (acceleration_variance * dt**3 / 2, acceleration_variance * dt**2),
        )
        predicted_state = (state[0] + dt * state[1], state[1])
        left = _matmul(transition, covariance)
        predicted_covariance_base = _matmul(
            left, ((1.0, 0.0), (dt, 1.0))
        )
        predicted_covariance = (
            (
                predicted_covariance_base[0][0] + process_noise[0][0],
                predicted_covariance_base[0][1] + process_noise[0][1],
            ),
            (
                predicted_covariance_base[1][0] + process_noise[1][0],
                predicted_covariance_base[1][1] + process_noise[1][1],
            ),
        )
        innovation_variance = predicted_covariance[0][0] + variances[index]
        gain = (
            predicted_covariance[0][0] / innovation_variance,
            predicted_covariance[1][0] / innovation_variance,
        )
        residual = measurements[index] - predicted_state[0]
        state = (
            predicted_state[0] + gain[0] * residual,
            predicted_state[1] + gain[1] * residual,
        )
        covariance = (
            (
                (1 - gain[0]) * predicted_covariance[0][0],
                (1 - gain[0]) * predicted_covariance[0][1],
            ),
            (
                predicted_covariance[1][0] - gain[1] * predicted_covariance[0][0],
                predicted_covariance[1][1] - gain[1] * predicted_covariance[0][1],
            ),
        )
        predicted_states.append(predicted_state)
        predicted_covariances.append(predicted_covariance)
        filtered_states.append(state)
        filtered_covariances.append(covariance)

    smoothed_states = list(filtered_states)
    smoothed_variances = [max(0.0, covariance[0][0]) for covariance in filtered_covariances]
    for index in range(len(measurements) - 2, -1, -1):
        transition = transitions[index]
        covariance = filtered_covariances[index]
        covariance_transition_transpose = _matmul(
            covariance,
            ((transition[0][0], transition[1][0]), (transition[0][1], transition[1][1])),
        )
        gain = _matmul(
            covariance_transition_transpose,
            _inverse(predicted_covariances[index + 1]),
        )
        difference = (
            smoothed_states[index + 1][0] - predicted_states[index + 1][0],
            smoothed_states[index + 1][1] - predicted_states[index + 1][1],
        )
        smoothed_states[index] = (
            filtered_states[index][0]
            + gain[0][0] * difference[0]
            + gain[0][1] * difference[1],
            filtered_states[index][1]
            + gain[1][0] * difference[0]
            + gain[1][1] * difference[1],
        )
    return [state[0] for state in smoothed_states], smoothed_variances


def smooth_trace(
    points: Sequence[LocatedObservation],
    continuity: ContinuityResult,
    *,
    anchor_indices: Sequence[int] = (),
    config: SmoothingConfig = SmoothingConfig(),
) -> SmoothingResult:
    """Run independent ENU position-velocity Kalman filters and RTS smoothers."""

    anchors = set(anchor_indices)
    smoothed_points: list[SmoothedPoint] = []
    coordinate_by_point: dict[int, tuple[float, float]] = {}
    confidence_terms: list[float] = []
    for segment in continuity.segments:
        if not segment:
            continue
        anchors.update((segment[0], segment[-1]))
        origin_latitude = sum(points[index].wgs_latitude for index in segment) / len(segment)
        origin_longitude = sum(points[index].wgs_longitude for index in segment) / len(segment)
        projected = [
            _project(
                points[index].wgs_latitude,
                points[index].wgs_longitude,
                origin_latitude,
                origin_longitude,
            )
            for index in segment
        ]
        initial_time = points[segment[0]].recorded_at
        times = [
            (points[index].recorded_at - initial_time).total_seconds()
            for index in segment
        ]
        accuracies = [
            min(
                config.measurement_accuracy_cap_meters,
                max(
                    config.measurement_accuracy_floor_meters,
                    points[index].horizontal_accuracy_meters
                    or config.missing_accuracy_meters,
                ),
            )
            for index in segment
        ]
        variances = [
            (config.anchor_accuracy_meters if index in anchors else accuracy) ** 2
            for index, accuracy in zip(segment, accuracies)
        ]
        smoothed_x, variance_x = _filter_and_smooth_axis(
            [coordinate[0] for coordinate in projected], times, variances, config
        )
        smoothed_y, variance_y = _filter_and_smooth_axis(
            [coordinate[1] for coordinate in projected], times, variances, config
        )
        for position, point_index in enumerate(segment):
            displacement_limit = min(
                config.displacement_limit_cap_meters,
                max(
                    config.displacement_limit_floor_meters,
                    accuracies[position] * config.displacement_accuracy_multiplier,
                ),
            )
            projected_displacement = math.hypot(
                smoothed_x[position] - projected[position][0],
                smoothed_y[position] - projected[position][1],
            )
            displacement_was_limited = projected_displacement > displacement_limit * 0.99
            if displacement_was_limited:
                smoothed_x[position] = projected[position][0]
                smoothed_y[position] = projected[position][1]
            latitude, longitude = _unproject(
                smoothed_x[position],
                smoothed_y[position],
                origin_latitude,
                origin_longitude,
            )
            if point_index in anchors:
                latitude = points[point_index].wgs_latitude
                longitude = points[point_index].wgs_longitude
                displacement_was_limited = False
            displacement = haversine_meters(
                points[point_index].wgs_latitude,
                points[point_index].wgs_longitude,
                latitude,
                longitude,
            )
            uncertainty = math.sqrt(
                max(0.0, variance_x[position] + variance_y[position])
            )
            confidence_terms.append(math.exp(-displacement / max(1.0, accuracies[position])))
            coordinate_by_point[point_index] = (latitude, longitude)
            smoothed_points.append(
                SmoothedPoint(
                    point_index,
                    latitude,
                    longitude,
                    displacement,
                    uncertainty,
                    point_index in anchors,
                    displacement_was_limited,
                )
            )
    confidence = sum(confidence_terms) / len(confidence_terms) if confidence_terms else 0.0
    return SmoothingResult(
        tuple(smoothed_points),
        continuity.segments,
        confidence,
        coordinate_by_point,
    )
