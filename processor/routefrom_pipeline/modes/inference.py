from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, Sequence

from routefrom_pipeline.quality import (
    LocatedObservation,
    PointAssessment,
    angular_difference_degrees,
    bearing_degrees,
    haversine_meters,
)
from routefrom_pipeline.stays import StayResult
from routefrom_pipeline.trips import TripResult


ModeLevel = Literal["root", "parent", "detail"]

_LEAF_MODES = (
    "walk",
    "run",
    "bicycle",
    "e_bike",
    "car",
    "bus",
    "motorcycle",
    "scooter",
    "metro",
    "train",
    "air",
    "boat",
    "unknown",
)

_PARENT_BY_LEAF = {
    "walk": "pedestrian",
    "run": "pedestrian",
    "bicycle": "cycle",
    "e_bike": "cycle",
    "car": "road_vehicle",
    "bus": "road_vehicle",
    "motorcycle": "road_vehicle",
    "scooter": "road_vehicle",
    "metro": "rail",
    "train": "rail",
    "air": "air",
    "boat": "boat",
    "unknown": "unknown",
}


@dataclass(frozen=True, slots=True)
class ModeConfig:
    change_penalty_scale: float = 12.0
    pause_sample_weight: float = 0.08
    accuracy_floor_meters: float = 5.0
    accuracy_cap_meters: float = 100.0
    parent_probability_min: float = 0.38
    detail_conditional_probability_min: float = 0.72
    detail_conditional_margin_min: float = 0.25
    same_leaf_transition_penalty: float = 0.0
    same_parent_transition_penalty: float = 0.45
    different_parent_transition_penalty: float = 1.25
    unknown_transition_penalty: float = 0.35


@dataclass(frozen=True, slots=True)
class ModeMapEvidence:
    pedestrian_network_probability: float = 0.0
    cycle_network_probability: float = 0.0
    road_network_probability: float = 0.0
    bus_route_probability: float = 0.0
    rail_network_probability: float = 0.0
    metro_network_probability: float = 0.0
    water_network_probability: float = 0.0
    airport_probability: float = 0.0


@dataclass(frozen=True, slots=True)
class ModeFeatureVector:
    point_count: int
    edge_count: int
    duration_seconds: float
    distance_meters: float
    speed_p10_mps: float
    speed_p50_mps: float
    speed_p90_mps: float
    speed_p95_mps: float
    acceleration_p90_mps2: float
    stop_fraction: float
    turn_p50_degrees: float
    altitude_change_meters: float | None
    effective_mode_weight: float
    map_evidence: ModeMapEvidence


@dataclass(frozen=True, slots=True)
class ModeScore:
    mode_code: str
    probability: float
    contributions: dict[str, float]


@dataclass(frozen=True, slots=True)
class MobilityLeg:
    trip_index: int
    sequence_number: int
    track_segment_index: int
    point_indices: tuple[int, ...]
    observed_started_at: datetime
    observed_ended_at: datetime
    selected_mode_code: str
    selected_mode_level: ModeLevel
    confidence: float
    features: ModeFeatureVector
    mode_scores: tuple[ModeScore, ...]
    evidence: dict[str, float | int | str]


@dataclass(frozen=True, slots=True)
class ModeResult:
    feature_schema_version: str
    legs: tuple[MobilityLeg, ...]


@dataclass(frozen=True, slots=True)
class _EdgeSample:
    from_index: int
    to_index: int
    duration_seconds: float
    distance_meters: float
    speed_mps: float
    acceleration_mps2: float
    turn_degrees: float
    weight: float


@dataclass(frozen=True, slots=True)
class _LegCandidate:
    trip_index: int
    track_segment_index: int
    point_indices: tuple[int, ...]
    features: ModeFeatureVector
    raw_logits: dict[str, float]
    contributions: dict[str, dict[str, float]]


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1.0 / (1.0 + exponential)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _logsumexp(values: Sequence[float]) -> float:
    maximum = max(values)
    return maximum + math.log(sum(math.exp(value - maximum) for value in values))


def _softmax(logits: Mapping[str, float]) -> dict[str, float]:
    normalizer = _logsumexp(tuple(logits.values()))
    return {mode: math.exp(value - normalizer) for mode, value in logits.items()}


def _log_probability(value: float) -> float:
    return math.log(max(1e-300, value))


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _edge_samples(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    indices: Sequence[int],
    pause_points: set[int],
    config: ModeConfig,
) -> tuple[_EdgeSample, ...]:
    raw: list[tuple[int, int, float, float, float, float]] = []
    bearings: list[float] = []
    for previous, current in zip(indices, indices[1:]):
        duration = (points[current].recorded_at - points[previous].recorded_at).total_seconds()
        if duration <= 0:
            continue
        distance = haversine_meters(
            points[previous].wgs_latitude,
            points[previous].wgs_longitude,
            points[current].wgs_latitude,
            points[current].wgs_longitude,
        )
        speed = distance / duration
        bearing = bearing_degrees(
            points[previous].wgs_latitude,
            points[previous].wgs_longitude,
            points[current].wgs_latitude,
            points[current].wgs_longitude,
        )
        raw.append((previous, current, duration, distance, speed, bearing))
        bearings.append(bearing)
    samples: list[_EdgeSample] = []
    for position, (previous, current, duration, distance, speed, _) in enumerate(raw):
        previous_speed = raw[position - 1][4] if position > 0 else speed
        acceleration = abs(speed - previous_speed) / max(duration, 1.0)
        turn = (
            angular_difference_degrees(bearings[position - 1], bearings[position])
            if position > 0
            else 0.0
        )
        quality_weight = min(
            assessments[previous].mode_weight,
            assessments[current].mode_weight,
        )
        pause_weight = (
            config.pause_sample_weight
            if previous in pause_points or current in pause_points
            else 1.0
        )
        samples.append(
            _EdgeSample(
                from_index=previous,
                to_index=current,
                duration_seconds=duration,
                distance_meters=distance,
                speed_mps=speed,
                acceleration_mps2=acceleration,
                turn_degrees=turn,
                weight=max(0.01, quality_weight * pause_weight),
            )
        )
    return tuple(samples)


def _sample_vector(sample: _EdgeSample) -> tuple[float, float, float]:
    return (
        2.0 * math.log1p(sample.speed_mps),
        math.log1p(sample.acceleration_mps2 * 10.0),
        sample.turn_degrees / 90.0,
    )


def _prefix_statistics(
    samples: Sequence[_EdgeSample],
) -> tuple[list[float], list[list[float]], list[list[float]]]:
    weight_prefix = [0.0]
    sum_prefix = [[0.0] for _ in range(3)]
    square_prefix = [[0.0] for _ in range(3)]
    for sample in samples:
        vector = _sample_vector(sample)
        weight_prefix.append(weight_prefix[-1] + sample.weight)
        for dimension, value in enumerate(vector):
            sum_prefix[dimension].append(
                sum_prefix[dimension][-1] + sample.weight * value
            )
            square_prefix[dimension].append(
                square_prefix[dimension][-1] + sample.weight * value * value
            )
    return weight_prefix, sum_prefix, square_prefix


def _segment_cost(
    start: int,
    end: int,
    weight_prefix: Sequence[float],
    sum_prefix: Sequence[Sequence[float]],
    square_prefix: Sequence[Sequence[float]],
) -> float:
    weight = weight_prefix[end] - weight_prefix[start]
    if weight <= 1e-9:
        return 0.0
    cost = 0.0
    for dimension in range(3):
        total = sum_prefix[dimension][end] - sum_prefix[dimension][start]
        total_square = (
            square_prefix[dimension][end] - square_prefix[dimension][start]
        )
        cost += max(0.0, total_square - total * total / weight)
    return cost


def _pelt_boundaries(
    samples: Sequence[_EdgeSample],
    config: ModeConfig,
) -> tuple[tuple[int, int], ...]:
    count = len(samples)
    if count == 0:
        return ()
    penalty = config.change_penalty_scale * math.log(count + 1.0)
    weight_prefix, sum_prefix, square_prefix = _prefix_statistics(samples)
    scores = [math.inf] * (count + 1)
    previous = [0] * (count + 1)
    scores[0] = -penalty
    candidates = [0]
    for end in range(1, count + 1):
        candidate_scores = [
            (
                scores[start]
                + _segment_cost(
                    start,
                    end,
                    weight_prefix,
                    sum_prefix,
                    square_prefix,
                )
                + penalty,
                start,
            )
            for start in candidates
        ]
        scores[end], previous[end] = min(candidate_scores)
        candidates = [
            start
            for start in candidates
            if scores[start]
            + _segment_cost(
                start,
                end,
                weight_prefix,
                sum_prefix,
                square_prefix,
            )
            <= scores[end] + penalty
        ]
        candidates.append(end)
    ranges_reversed: list[tuple[int, int]] = []
    end = count
    while end > 0:
        start = previous[end]
        ranges_reversed.append((start, end))
        end = start
    return tuple(reversed(ranges_reversed))


def _aggregate_map_evidence(
    indices: Sequence[int],
    map_evidence_by_point: Mapping[int, ModeMapEvidence],
) -> ModeMapEvidence:
    evidence = [map_evidence_by_point[index] for index in indices if index in map_evidence_by_point]
    if not evidence:
        return ModeMapEvidence()
    fields = ModeMapEvidence.__dataclass_fields__
    return ModeMapEvidence(
        **{
            field: sum(getattr(item, field) for item in evidence) / len(evidence)
            for field in fields
        }
    )


def _feature_vector(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    samples: Sequence[_EdgeSample],
    indices: Sequence[int],
    map_evidence_by_point: Mapping[int, ModeMapEvidence],
) -> ModeFeatureVector:
    speeds = [sample.speed_mps for sample in samples]
    accelerations = [sample.acceleration_mps2 for sample in samples]
    turns = [sample.turn_degrees for sample in samples]
    altitudes = [
        points[index].altitude_meters
        for index in indices
        if points[index].altitude_meters is not None
    ]
    return ModeFeatureVector(
        point_count=len(indices),
        edge_count=len(samples),
        duration_seconds=sum(sample.duration_seconds for sample in samples),
        distance_meters=sum(sample.distance_meters for sample in samples),
        speed_p10_mps=_quantile(speeds, 0.10),
        speed_p50_mps=_quantile(speeds, 0.50),
        speed_p90_mps=_quantile(speeds, 0.90),
        speed_p95_mps=_quantile(speeds, 0.95),
        acceleration_p90_mps2=_quantile(accelerations, 0.90),
        stop_fraction=(
            sum(sample.duration_seconds for sample in samples if sample.speed_mps < 0.5)
            / max(1.0, sum(sample.duration_seconds for sample in samples))
        ),
        turn_p50_degrees=_quantile(turns, 0.50),
        altitude_change_meters=(
            max(altitudes) - min(altitudes) if len(altitudes) >= 2 else None
        ),
        effective_mode_weight=(
            sum(assessments[index].mode_weight for index in indices) / len(indices)
        ),
        map_evidence=_aggregate_map_evidence(indices, map_evidence_by_point),
    )


def _bell(value: float, center: float, spread: float) -> float:
    return -0.5 * ((value - center) / spread) ** 2


def _air_cruise_log_likelihood(speed_mps: float) -> float:
    if speed_mps < 75.0:
        return _bell(speed_mps, 75.0, 20.0)
    if speed_mps > 280.0:
        return _bell(speed_mps, 280.0, 40.0)
    return 0.0


def _mode_logits(
    features: ModeFeatureVector,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    speed = features.speed_p50_mps
    high_speed = features.speed_p90_mps
    map_evidence = features.map_evidence
    contributions: dict[str, dict[str, float]] = {mode: {} for mode in _LEAF_MODES}

    def add(mode: str, feature: str, value: float) -> None:
        contributions[mode][feature] = value

    priors = {
        "walk": -0.8,
        "run": -1.8,
        "bicycle": -1.1,
        "e_bike": -1.8,
        "car": -1.0,
        "bus": -1.6,
        "motorcycle": -1.8,
        "scooter": -1.9,
        "metro": -2.8,
        "train": -3.0,
        "air": -3.5,
        "boat": -3.5,
        "unknown": -2.8,
    }
    speed_models = {
        "walk": (1.25, 0.85),
        "run": (3.0, 1.15),
        "bicycle": (4.5, 2.4),
        "e_bike": (6.5, 3.0),
        "car": (12.0, 8.5),
        "bus": (9.0, 6.5),
        "motorcycle": (13.0, 9.0),
        "scooter": (7.0, 4.0),
        "metro": (15.0, 8.0),
        "train": (25.0, 14.0),
        "boat": (8.0, 5.0),
    }
    for mode, (center, spread) in speed_models.items():
        add(mode, "speed_p50", _bell(speed, center, spread))
    add("air", "speed_p50", _air_cruise_log_likelihood(speed))

    child_counts: dict[str, int] = {}
    for mode in _LEAF_MODES:
        parent = _PARENT_BY_LEAF[mode]
        child_counts[parent] = child_counts.get(parent, 0) + 1
    for mode in _LEAF_MODES:
        add(
            mode,
            "hierarchy_size_normalization",
            -math.log(child_counts[_PARENT_BY_LEAF[mode]]),
        )

    add("walk", "pedestrian_network", 2.2 * map_evidence.pedestrian_network_probability)
    add("run", "pedestrian_network", 1.7 * map_evidence.pedestrian_network_probability)
    add("bicycle", "cycle_network", 2.0 * map_evidence.cycle_network_probability)
    add("e_bike", "cycle_network", 1.6 * map_evidence.cycle_network_probability)
    for mode in ("car", "bus", "motorcycle", "scooter"):
        add(mode, "road_network", 1.7 * map_evidence.road_network_probability)
    add("bus", "bus_route", 3.0 * map_evidence.bus_route_probability)
    add("metro", "rail_network", 2.5 * map_evidence.rail_network_probability)
    add("metro", "metro_network", 2.5 * map_evidence.metro_network_probability)
    add("train", "rail_network", 3.2 * map_evidence.rail_network_probability)
    add("boat", "water_network", 4.0 * map_evidence.water_network_probability)
    add("air", "airport", 3.0 * map_evidence.airport_probability)
    add("air", "high_speed", 2.5 * _sigmoid((high_speed - 55.0) / 12.0))
    add("run", "high_speed", 0.8 * _sigmoid((high_speed - 2.2) / 0.5))
    add("bus", "stop_fraction", 0.9 * min(1.0, features.stop_fraction * 4.0))
    add("unknown", "low_quality", 2.2 * (1.0 - features.effective_mode_weight))
    add("unknown", "weak_observation", 0.8 if features.edge_count < 3 else 0.0)

    logits = {
        mode: priors[mode] + sum(contributions[mode].values()) for mode in _LEAF_MODES
    }
    return logits, contributions


def _transition_penalty(previous: str, current: str, config: ModeConfig) -> float:
    if previous == current:
        return config.same_leaf_transition_penalty
    if "unknown" in (previous, current):
        return config.unknown_transition_penalty
    if _PARENT_BY_LEAF[previous] == _PARENT_BY_LEAF[current]:
        return config.same_parent_transition_penalty
    return config.different_parent_transition_penalty


def _smoothed_probabilities(
    candidates: Sequence[_LegCandidate],
    config: ModeConfig,
) -> list[dict[str, float]]:
    if not candidates:
        return []
    emissions = [_softmax(candidate.raw_logits) for candidate in candidates]
    forward: list[dict[str, float]] = []
    forward.append({mode: _log_probability(emissions[0][mode]) for mode in _LEAF_MODES})
    for position in range(1, len(candidates)):
        current: dict[str, float] = {}
        for mode in _LEAF_MODES:
            current[mode] = _log_probability(emissions[position][mode]) + _logsumexp(
                tuple(
                    forward[position - 1][previous]
                    - _transition_penalty(previous, mode, config)
                    for previous in _LEAF_MODES
                )
            )
        normalizer = _logsumexp(tuple(current.values()))
        forward.append({mode: value - normalizer for mode, value in current.items()})
    backward: list[dict[str, float]] = [
        {mode: 0.0 for mode in _LEAF_MODES} for _ in candidates
    ]
    for position in range(len(candidates) - 2, -1, -1):
        current = {}
        for mode in _LEAF_MODES:
            current[mode] = _logsumexp(
                tuple(
                    -_transition_penalty(mode, following, config)
                    + _log_probability(emissions[position + 1][following])
                    + backward[position + 1][following]
                    for following in _LEAF_MODES
                )
            )
        normalizer = _logsumexp(tuple(current.values()))
        backward[position] = {mode: value - normalizer for mode, value in current.items()}
    result: list[dict[str, float]] = []
    for position in range(len(candidates)):
        logits = {
            mode: forward[position][mode] + backward[position][mode]
            for mode in _LEAF_MODES
        }
        result.append(_softmax(logits))
    return result


def _select_hierarchical_mode(
    probabilities: Mapping[str, float],
    config: ModeConfig,
) -> tuple[str, ModeLevel, float]:
    parent_probabilities: dict[str, float] = {}
    for mode, probability in probabilities.items():
        parent = _PARENT_BY_LEAF[mode]
        parent_probabilities[parent] = parent_probabilities.get(parent, 0.0) + probability
    parent = max(parent_probabilities, key=parent_probabilities.__getitem__)
    parent_probability = parent_probabilities[parent]
    if parent == "unknown" or parent_probability < config.parent_probability_min:
        return "unknown", "root", parent_probabilities.get("unknown", parent_probability)
    children = [mode for mode in _LEAF_MODES if _PARENT_BY_LEAF[mode] == parent]
    if len(children) == 1:
        return children[0], "detail", parent_probability
    ranked = sorted(
        ((probabilities[child] / parent_probability, child) for child in children),
        reverse=True,
    )
    best_probability, best_child = ranked[0]
    second_probability = ranked[1][0]
    if (
        best_probability >= config.detail_conditional_probability_min
        and best_probability - second_probability >= config.detail_conditional_margin_min
    ):
        return best_child, "detail", probabilities[best_child]
    return parent, "parent", parent_probability


def infer_transport_modes(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    stays: StayResult,
    trips: TripResult,
    *,
    map_evidence_by_point: Mapping[int, ModeMapEvidence] | None = None,
    config: ModeConfig = ModeConfig(),
) -> ModeResult:
    if len(points) != len(assessments):
        raise ValueError("points and assessments must have equal length")
    map_evidence = map_evidence_by_point or {}
    pause_points = {
        point_index
        for event in stays.events
        if event.event_type.value == "transport_pause"
        for point_index in event.point_indices
    }
    all_legs: list[MobilityLeg] = []
    for trip_index, trip in enumerate(trips.trips):
        for track_segment_index, track_segment in enumerate(trip.track_segments):
            samples = _edge_samples(
                points,
                assessments,
                track_segment.point_indices,
                pause_points,
                config,
            )
            boundaries = _pelt_boundaries(samples, config)
            candidates: list[_LegCandidate] = []
            for start, end in boundaries:
                segment_samples = samples[start:end]
                indices = tuple(
                    [segment_samples[0].from_index]
                    + [sample.to_index for sample in segment_samples]
                )
                features = _feature_vector(
                    points,
                    assessments,
                    segment_samples,
                    indices,
                    map_evidence,
                )
                logits, contributions = _mode_logits(features)
                candidates.append(
                    _LegCandidate(
                        trip_index=trip_index,
                        track_segment_index=track_segment_index,
                        point_indices=indices,
                        features=features,
                        raw_logits=logits,
                        contributions=contributions,
                    )
                )
            smoothed = _smoothed_probabilities(candidates, config)
            for candidate, probabilities in zip(candidates, smoothed):
                selected_mode, selected_level, confidence = _select_hierarchical_mode(
                    probabilities, config
                )
                scores = tuple(
                    ModeScore(
                        mode_code=mode,
                        probability=probabilities[mode],
                        contributions=candidate.contributions[mode],
                    )
                    for mode in _LEAF_MODES
                )
                all_legs.append(
                    MobilityLeg(
                        trip_index=trip_index,
                        sequence_number=len(all_legs),
                        track_segment_index=track_segment_index,
                        point_indices=candidate.point_indices,
                        observed_started_at=points[candidate.point_indices[0]].recorded_at,
                        observed_ended_at=points[candidate.point_indices[-1]].recorded_at,
                        selected_mode_code=selected_mode,
                        selected_mode_level=selected_level,
                        confidence=confidence,
                        features=candidate.features,
                        mode_scores=scores,
                        evidence={
                            "model": "interpretable_hierarchical_likelihood_v1",
                            "segmentation": "penalized_pelt_v1",
                            "map_evidence_available": int(bool(map_evidence)),
                        },
                    )
                )
    return ModeResult(
        feature_schema_version="mode-features-v1",
        legs=tuple(all_legs),
    )
