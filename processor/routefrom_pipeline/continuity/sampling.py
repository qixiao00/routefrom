from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import NormalDist

from routefrom_pipeline.quality import LocatedObservation, PointFeatures, haversine_meters


class SamplingContext(StrEnum):
    MOVING = "moving"
    STATIONARY = "stationary"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class SamplingModelConfig:
    context_window_edges: int = 48
    minimum_context_samples: int = 8
    moving_speed_mps: float = 1.2
    stationary_speed_mps: float = 0.6
    stationary_accuracy_multiplier: float = 2.5
    stationary_radius_floor_meters: float = 30.0
    prior_median_moving_seconds: float = 15.0
    prior_median_stationary_seconds: float = 600.0
    prior_median_uncertain_seconds: float = 75.0
    prior_log_scale_moving: float = 1.0
    prior_log_scale_stationary: float = 0.8
    prior_log_scale_uncertain: float = 1.4
    minimum_log_scale: float = 0.35
    maximum_log_scale: float = 1.35
    moving_training_max_seconds: float = 10 * 60
    stationary_training_max_seconds: float = 30 * 60
    uncertain_training_max_seconds: float = 15 * 60
    minimum_gap_seconds: float = 15 * 60
    maximum_gap_seconds: float = 6 * 60 * 60
    survival_probability_min: float = 0.01
    displaced_tail_min_meters: float = 100.0
    displaced_tail_accuracy_multiplier: float = 3.0
    maximum_reliable_accuracy_meters: float = 100.0
    short_interval_weak_accuracy_meters: float = 1_000.0
    short_interval_weak_displacement_meters: float = 500.0
    short_interval_accuracy_displacement_fraction: float = 0.10
    unsupported_return_min_leg_meters: float = 500.0
    unsupported_return_ratio_max: float = 0.15
    unsupported_return_speed_mps: float = 80.0
    unsupported_return_bypass_speed_mps: float = 70.0
    unsupported_return_max_seconds: float = 600.0
    sensor_conflict_return_max_seconds: float = 120.0
    sensor_conflict_return_min_leg_meters: float = 300.0
    sensor_conflict_leg_min_speed_mps: float = 15.0
    sensor_conflict_reported_speed_max_mps: float = 3.0
    multi_point_return_max_neighbors: int = 5
    multi_point_return_max_seconds: float = 180.0
    multi_point_return_min_leg_meters: float = 300.0
    multi_point_return_min_leg_speed_mps: float = 4.0
    multi_point_return_anchor_radius_meters: float = 100.0
    speed_support_min_mps: float = 1.2
    speed_support_absolute_tolerance_mps: float = 10.0
    speed_support_relative_tolerance: float = 0.5
    uncorroborated_displacement_min_meters: float = 2_000.0
    corroborating_motion_min_meters: float = 500.0
    corroborating_turn_max_degrees: float = 55.0


@dataclass(frozen=True, slots=True)
class SamplingIntervalAssessment:
    interval_index: int
    context: SamplingContext
    elapsed_seconds: float
    expected_interval_seconds: float
    log_scale: float
    context_sample_count: int
    normal_wait_probability: float
    confidence: float
    is_observation_gap: bool
    reason_codes: tuple[str, ...]


_DEFAULT_SAMPLING_MODEL_CONFIG = SamplingModelConfig()


def _context_for_interval(
    points: Sequence[LocatedObservation],
    features: Sequence[PointFeatures],
    index: int,
    config: SamplingModelConfig,
) -> SamplingContext:
    feature = features[index]
    calculated_speed = feature.speed_next_mps or 0.0
    recorded_speeds = [
        speed
        for speed in (
            points[index].recorded_speed_mps,
            points[index + 1].recorded_speed_mps,
        )
        if speed is not None and speed >= 0
    ]
    effective_speed = max(calculated_speed, *recorded_speeds) if recorded_speeds else calculated_speed
    if effective_speed >= config.moving_speed_mps:
        return SamplingContext.MOVING

    accuracies = [
        accuracy
        for accuracy in (
            points[index].horizontal_accuracy_meters,
            points[index + 1].horizontal_accuracy_meters,
        )
        if accuracy is not None and accuracy > 0
    ]
    support_radius = max(
        config.stationary_radius_floor_meters,
        (max(accuracies) if accuracies else config.stationary_radius_floor_meters)
        * config.stationary_accuracy_multiplier,
    )
    if (
        calculated_speed <= config.stationary_speed_mps
        and (feature.distance_next_meters or 0.0) <= support_radius
    ):
        return SamplingContext.STATIONARY
    return SamplingContext.UNCERTAIN


def _fast_return_without_position_support(
    feature: PointFeatures,
    config: SamplingModelConfig,
) -> bool:
    first_distance = feature.distance_prev_meters
    second_distance = feature.distance_next_meters
    first_duration = feature.dt_prev_seconds
    second_duration = feature.dt_next_seconds
    if (
        first_distance is None
        or second_distance is None
        or first_duration is None
        or second_duration is None
    ):
        return False
    return (
        min(first_distance, second_distance) >= config.unsupported_return_min_leg_meters
        and feature.return_ratio is not None
        and feature.return_ratio <= config.unsupported_return_ratio_max
        and feature.bypass_speed_mps is not None
        and feature.bypass_speed_mps <= config.unsupported_return_bypass_speed_mps
        and max(feature.speed_prev_mps or 0.0, feature.speed_next_mps or 0.0)
        >= config.unsupported_return_speed_mps
        and first_duration > 0
        and second_duration > 0
        and first_duration + second_duration <= config.unsupported_return_max_seconds
    )


def _return_conflicts_with_reported_speeds(
    points: Sequence[LocatedObservation],
    feature: PointFeatures,
    config: SamplingModelConfig,
) -> bool:
    apex_index = feature.point_index
    if apex_index <= 0 or apex_index + 1 >= len(points):
        return False
    first_distance = feature.distance_prev_meters
    second_distance = feature.distance_next_meters
    first_duration = feature.dt_prev_seconds
    second_duration = feature.dt_next_seconds
    if (
        first_distance is None
        or second_distance is None
        or first_duration is None
        or second_duration is None
    ):
        return False
    recorded = [
        point.recorded_speed_mps
        for point in points[apex_index - 1:apex_index + 2]
        if point.recorded_speed_mps is not None
    ]
    return (
        min(first_distance, second_distance) >= config.sensor_conflict_return_min_leg_meters
        and feature.return_ratio is not None
        and feature.return_ratio <= config.unsupported_return_ratio_max
        and first_duration > 0
        and second_duration > 0
        and first_duration + second_duration <= config.sensor_conflict_return_max_seconds
        and min(feature.speed_prev_mps or 0.0, feature.speed_next_mps or 0.0)
        >= config.sensor_conflict_leg_min_speed_mps
        and len(recorded) >= 2
        and max(recorded) <= config.sensor_conflict_reported_speed_max_mps
    )


def _multi_point_return_conflicts_with_reported_speeds(
    points: Sequence[LocatedObservation],
    feature: PointFeatures,
    config: SamplingModelConfig,
) -> bool:
    index = feature.point_index
    if index + 1 >= len(points):
        return False
    distance = feature.distance_next_meters or 0.0
    speed = feature.speed_next_mps or 0.0
    if (distance < config.multi_point_return_min_leg_meters
            or speed < config.multi_point_return_min_leg_speed_mps):
        return False
    start, end = points[index], points[index + 1]
    if any(
        item.recorded_speed_mps is None
        or item.recorded_speed_mps > config.sensor_conflict_reported_speed_max_mps
        for item in (start, end)
    ):
        return False
    radius = min(config.multi_point_return_anchor_radius_meters, distance * 0.25)
    # A nearby fix before or after the disputed edge is independent evidence
    # that the route doubled back. Limit both the point count and wall time so
    # a genuine later return visit cannot invalidate an earlier trip.
    for neighbor in range(
        max(0, index - config.multi_point_return_max_neighbors), index
    ):
        anchor = points[neighbor]
        if (anchor.recorded_speed_mps is not None
                and anchor.recorded_speed_mps <= config.sensor_conflict_reported_speed_max_mps
                and (end.recorded_at - anchor.recorded_at).total_seconds()
                <= config.multi_point_return_max_seconds
                and haversine_meters(
                    anchor.wgs_latitude, anchor.wgs_longitude,
                    end.wgs_latitude, end.wgs_longitude,
                ) <= radius):
            return True
    for neighbor in range(
        index + 2, min(len(points), index + 2 + config.multi_point_return_max_neighbors)
    ):
        anchor = points[neighbor]
        if (anchor.recorded_speed_mps is not None
                and anchor.recorded_speed_mps <= config.sensor_conflict_reported_speed_max_mps
                and (anchor.recorded_at - start.recorded_at).total_seconds()
                <= config.multi_point_return_max_seconds
                and haversine_meters(
                    anchor.wgs_latitude, anchor.wgs_longitude,
                    start.wgs_latitude, start.wgs_longitude,
                ) <= radius):
            return True
    return False


def _prior(context: SamplingContext, config: SamplingModelConfig) -> tuple[float, float]:
    if context == SamplingContext.MOVING:
        return config.prior_median_moving_seconds, config.prior_log_scale_moving
    if context == SamplingContext.STATIONARY:
        return config.prior_median_stationary_seconds, config.prior_log_scale_stationary
    return config.prior_median_uncertain_seconds, config.prior_log_scale_uncertain


def _training_maximum(context: SamplingContext, config: SamplingModelConfig) -> float:
    if context == SamplingContext.MOVING:
        return config.moving_training_max_seconds
    if context == SamplingContext.STATIONARY:
        return config.stationary_training_max_seconds
    return config.uncertain_training_max_seconds


def _quantile(values: Sequence[float], probability: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def _fit_profile(
    samples: Sequence[float],
    context: SamplingContext,
    config: SamplingModelConfig,
) -> tuple[float, float, float]:
    prior_median, prior_scale = _prior(context, config)
    if not samples:
        return prior_median, prior_scale, 0.0

    logged = sorted(math.log(max(1e-3, value)) for value in samples)
    local_median_log = statistics.median(logged)
    local_mad = statistics.median(abs(value - local_median_log) for value in logged)
    upper_scale = max(0.0, (_quantile(logged, 0.90) - local_median_log) / 1.2815515655)
    local_scale = max(config.minimum_log_scale, 1.4826 * local_mad, upper_scale)
    local_scale = min(config.maximum_log_scale, local_scale)

    local_weight = min(1.0, len(samples) / config.minimum_context_samples)
    expected_log = local_weight * local_median_log + (1.0 - local_weight) * math.log(prior_median)
    scale = local_weight * local_scale + (1.0 - local_weight) * prior_scale
    return math.exp(expected_log), scale, local_weight


def estimate_sampling_intervals(
    points: Sequence[LocatedObservation],
    features: Sequence[PointFeatures],
    *,
    config: SamplingModelConfig = _DEFAULT_SAMPLING_MODEL_CONFIG,
) -> tuple[SamplingIntervalAssessment, ...]:
    if len(points) != len(features):
        raise ValueError("points and features must have equal length")
    if len(points) < 2:
        return ()
    if config.context_window_edges < 1:
        raise ValueError("context_window_edges must be positive")
    if config.minimum_context_samples < 1:
        raise ValueError("minimum_context_samples must be positive")
    if not 0 < config.survival_probability_min < 1:
        raise ValueError("survival_probability_min must be between zero and one")
    if min(
        config.moving_training_max_seconds,
        config.stationary_training_max_seconds,
        config.uncertain_training_max_seconds,
    ) <= 0:
        raise ValueError("sampling training limits must be positive")

    elapsed = [
        (points[index + 1].recorded_at - points[index].recorded_at).total_seconds()
        for index in range(len(points) - 1)
    ]
    contexts = [
        _context_for_interval(points, features, index, config)
        for index in range(len(points) - 1)
    ]
    result: list[SamplingIntervalAssessment] = []
    normal = NormalDist()

    for index, (duration, context) in enumerate(zip(elapsed, contexts)):
        window_start = max(0, index - config.context_window_edges)
        window_end = min(len(elapsed), index + config.context_window_edges + 1)
        samples = [
            elapsed[candidate]
            for candidate in range(window_start, window_end)
            if candidate != index
            and contexts[candidate] == context
            and 0 < elapsed[candidate] <= _training_maximum(context, config)
        ]
        expected, log_scale, local_weight = _fit_profile(samples, context, config)
        if duration <= 0:
            survival_probability = 0.0
        else:
            z_score = (math.log(duration) - math.log(expected)) / max(1e-6, log_scale)
            survival_probability = normal.cdf(-z_score)
        hard_maximum = duration >= config.maximum_gap_seconds
        accuracies = [
            accuracy
            for accuracy in (
                points[index].horizontal_accuracy_meters,
                points[index + 1].horizontal_accuracy_meters,
            )
            if accuracy is not None and accuracy > 0
        ]
        displaced = (features[index].distance_next_meters or 0.0) > max(
            config.displaced_tail_min_meters,
            (max(accuracies) if accuracies else 0.0)
            * config.displaced_tail_accuracy_multiplier,
        )
        weak_position_support = (
            duration >= config.minimum_gap_seconds
            and any(
                accuracy > config.maximum_reliable_accuracy_meters
                for accuracy in accuracies
            )
        )
        calculated_speed = features[index].speed_next_mps or 0.0
        reported_speeds = (
            points[index].recorded_speed_mps,
            points[index + 1].recorded_speed_mps,
        )
        speed_supported = (
            calculated_speed >= config.speed_support_min_mps
            and all(
                speed is not None
                and speed >= config.speed_support_min_mps
                and abs(speed - calculated_speed)
                <= max(
                    config.speed_support_absolute_tolerance_mps,
                    calculated_speed * config.speed_support_relative_tolerance,
                )
                for speed in reported_speeds
            )
        )
        previous_motion_support = (
            index > 0
            and (features[index].distance_prev_meters or 0.0)
            >= config.corroborating_motion_min_meters
            and 0 < elapsed[index - 1] <= config.minimum_gap_seconds
            and features[index].turn_degrees is not None
            and features[index].turn_degrees <= config.corroborating_turn_max_degrees
        )
        following_motion_support = (
            index + 1 < len(elapsed)
            and (features[index + 1].distance_next_meters or 0.0)
            >= config.corroborating_motion_min_meters
            and 0 < elapsed[index + 1] <= config.minimum_gap_seconds
            and features[index + 1].turn_degrees is not None
            and features[index + 1].turn_degrees <= config.corroborating_turn_max_degrees
        )
        weak_short_interval_geometry = (
            duration > 0
            and duration < config.minimum_gap_seconds
            and (features[index].distance_next_meters or 0.0)
            >= config.short_interval_weak_displacement_meters
            and max(accuracies, default=0.0)
            >= max(
                config.short_interval_weak_accuracy_meters,
                (features[index].distance_next_meters or 0.0)
                * config.short_interval_accuracy_displacement_fraction,
            )
            and not speed_supported
            and not (previous_motion_support and following_motion_support)
        )
        unsupported_fast_return = (
            not speed_supported
            and (
                _fast_return_without_position_support(features[index], config)
                or _fast_return_without_position_support(features[index + 1], config)
            )
        )
        sensor_conflict_return = (
            _return_conflicts_with_reported_speeds(points, features[index], config)
            or _return_conflicts_with_reported_speeds(points, features[index + 1], config)
        )
        multi_point_sensor_conflict = _multi_point_return_conflicts_with_reported_speeds(
            points, features[index], config
        )
        uncorroborated_displacement = (
            (features[index].distance_next_meters or 0.0)
            >= config.uncorroborated_displacement_min_meters
            and not speed_supported
            and not (previous_motion_support or following_motion_support)
        )
        unsupported_displacement = (
            duration >= config.minimum_gap_seconds
            and displaced
            and not speed_supported
        )
        tail_gap = (
            (duration >= config.minimum_gap_seconds or displaced)
            and survival_probability < config.survival_probability_min
        )
        is_gap = (
            duration <= 0
            or hard_maximum
            or tail_gap
            or unsupported_displacement
            or weak_position_support
            or weak_short_interval_geometry
            or unsupported_fast_return
            or sensor_conflict_return
            or multi_point_sensor_conflict
            or uncorroborated_displacement
        )
        reasons = [f"sampling_context_{context.value}"]
        reasons.append(
            "sampling_profile_local"
            if len(samples) >= config.minimum_context_samples
            else "sampling_profile_prior_backoff"
        )
        if duration <= 0:
            reasons.append("non_increasing_time")
        elif hard_maximum:
            reasons.append("engineering_maximum_gap")
        elif tail_gap:
            reasons.append("wait_survival_tail")
            if duration < config.minimum_gap_seconds:
                reasons.append("displaced_without_sampling_support")
        if unsupported_displacement:
            reasons.append("displaced_without_speed_support")
        if weak_position_support:
            reasons.append("long_wait_with_weak_position_support")
        if weak_short_interval_geometry:
            reasons.append("short_move_with_weak_position_support")
        if unsupported_fast_return:
            reasons.append("fast_return_without_sensor_support")
        if sensor_conflict_return:
            reasons.append("return_conflicts_with_reported_speeds")
        if multi_point_sensor_conflict:
            reasons.append("multi_point_return_conflicts_with_reported_speeds")
        if uncorroborated_displacement:
            reasons.append("uncorroborated_displacement")
        if not is_gap and duration >= config.minimum_gap_seconds:
            reasons.append("long_wait_plausible_for_context")
        elif not is_gap and survival_probability < config.survival_probability_min:
            reasons.append("minimum_gap_guard")

        result.append(
            SamplingIntervalAssessment(
                interval_index=index,
                context=context,
                elapsed_seconds=duration,
                expected_interval_seconds=expected,
                log_scale=log_scale,
                context_sample_count=len(samples),
                normal_wait_probability=max(0.0, min(1.0, survival_probability)),
                confidence=min(1.0, 0.35 + 0.65 * local_weight),
                is_observation_gap=is_gap,
                reason_codes=tuple(reasons),
            )
        )
    return tuple(result)
