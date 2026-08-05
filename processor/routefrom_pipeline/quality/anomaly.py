from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from routefrom_pipeline.model import QualityStatus

from .features import PointFeatures


@dataclass(frozen=True, slots=True)
class AnomalyConfig:
    base_log_odds: float = -4.25
    ground_speed_soft_limit_mps: float = 70.0
    speed_log_weight: float = 1.25
    triangle_ratio_soft_limit: float = 8.0
    triangle_log_weight: float = 1.8
    isolated_jump_min_meters: float = 5_000.0
    isolated_return_ratio_max: float = 0.05
    isolated_return_weight: float = 5.0
    accuracy_soft_limit_meters: float = 200.0
    accuracy_log_weight: float = 0.25
    recorded_speed_residual_soft_limit_mps: float = 40.0
    recorded_speed_residual_weight: float = 0.8
    valid_probability_max: float = 0.50
    excluded_probability_min: float = 0.98


@dataclass(frozen=True, slots=True)
class PointAssessment:
    point_index: int
    quality_status: QualityStatus
    anomaly_probability: float
    path_weight: float
    stay_weight: float
    mode_weight: float
    exclusion_supported: bool
    reason_codes: tuple[str, ...]
    contributions: dict[str, float]


def _positive_log_ratio(value: float | None, limit: float) -> float:
    if value is None or value <= limit:
        return 0.0
    return math.log1p(value / limit - 1.0)


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1 / (1 + exponential)
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def _bounded_weight(probability: float, floor: float) -> float:
    return max(floor, min(1.0, 1.0 - probability))


def assess_point(
    features: PointFeatures,
    config: AnomalyConfig = AnomalyConfig(),
) -> PointAssessment:
    adjacent_speeds = [
        speed
        for speed in (features.speed_prev_mps, features.speed_next_mps)
        if speed is not None
    ]
    maximum_adjacent_speed = max(adjacent_speeds, default=None)
    contributions: dict[str, float] = {}
    reason_codes: list[str] = []

    speed_contribution = config.speed_log_weight * _positive_log_ratio(
        maximum_adjacent_speed, config.ground_speed_soft_limit_mps
    )
    if speed_contribution:
        contributions["speed"] = speed_contribution
        reason_codes.append("implausible_adjacent_speed")

    triangle_contribution = config.triangle_log_weight * _positive_log_ratio(
        features.triangle_ratio, config.triangle_ratio_soft_limit
    )
    if triangle_contribution:
        contributions["triangle"] = triangle_contribution
        reason_codes.append("triangle_bypass_is_shorter")

    adjacent_distances = [
        distance
        for distance in (features.distance_prev_meters, features.distance_next_meters)
        if distance is not None
    ]
    isolated_return = (
        len(adjacent_distances) == 2
        and min(adjacent_distances) >= config.isolated_jump_min_meters
        and features.return_ratio is not None
        and features.return_ratio <= config.isolated_return_ratio_max
        and features.bypass_speed_mps is not None
        and features.bypass_speed_mps <= config.ground_speed_soft_limit_mps
    )
    if isolated_return:
        contributions["isolated_return"] = config.isolated_return_weight
        reason_codes.append("far_jump_then_return")

    accuracy_contribution = config.accuracy_log_weight * _positive_log_ratio(
        features.horizontal_accuracy_meters, config.accuracy_soft_limit_meters
    )
    if accuracy_contribution:
        contributions["accuracy"] = accuracy_contribution
        reason_codes.append("low_horizontal_accuracy")

    speed_residual_contribution = config.recorded_speed_residual_weight * _positive_log_ratio(
        features.recorded_speed_residual_mps,
        config.recorded_speed_residual_soft_limit_mps,
    )
    if speed_residual_contribution:
        contributions["recorded_speed_residual"] = speed_residual_contribution
        reason_codes.append("recorded_and_calculated_speed_disagree")

    log_odds = config.base_log_odds + sum(contributions.values())
    probability = _sigmoid(log_odds)
    exclusion_supported = isolated_return and triangle_contribution > 0

    if probability < config.valid_probability_max:
        quality_status = QualityStatus.VALID
    elif probability >= config.excluded_probability_min and exclusion_supported:
        quality_status = QualityStatus.EXCLUDED
    else:
        quality_status = QualityStatus.SUSPECT

    # Accuracy may lower confidence, but it cannot independently collapse every task weight.
    path_weight = _bounded_weight(probability, 0.05)
    stay_weight = _bounded_weight(probability + min(0.25, accuracy_contribution * 0.08), 0.03)
    mode_weight = _bounded_weight(probability + min(0.35, accuracy_contribution * 0.05), 0.02)

    return PointAssessment(
        point_index=features.point_index,
        quality_status=quality_status,
        anomaly_probability=probability,
        path_weight=path_weight,
        stay_weight=stay_weight,
        mode_weight=mode_weight,
        exclusion_supported=exclusion_supported,
        reason_codes=tuple(reason_codes),
        contributions=contributions,
    )


def assess_points(
    features: Sequence[PointFeatures],
    config: AnomalyConfig = AnomalyConfig(),
) -> list[PointAssessment]:
    return [assess_point(feature, config) for feature in features]
