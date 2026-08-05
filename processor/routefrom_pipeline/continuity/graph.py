from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

from routefrom_pipeline.quality.anomaly import PointAssessment
from routefrom_pipeline.quality.features import LocatedObservation, haversine_meters

EdgeKind = Literal["adjacent", "bypass", "break"]


@dataclass(frozen=True, slots=True)
class ContinuityConfig:
    max_skipped_points: int = 3
    maximum_continuous_speed_mps: float = 160.0
    speed_transition_softness_mps: float = 25.0
    minimum_gap_seconds: float = 15 * 60
    maximum_gap_seconds: float = 6 * 60 * 60
    expected_interval_multiplier: float = 8.0
    connected_edge_probability_min: float = 0.10
    skip_penalty: float = 0.35
    unsupported_skip_penalty: float = 5.0
    unsupported_anomaly_probability_cap: float = 0.80
    break_penalty: float = -2.75


@dataclass(frozen=True, slots=True)
class ContinuityEdge:
    from_index: int
    to_index: int
    kind: EdgeKind
    elapsed_seconds: float
    displacement_meters: float
    calculated_speed_mps: float | None
    continuity_probability: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContinuityResult:
    selected_point_indices: tuple[int, ...]
    skipped_point_indices: tuple[int, ...]
    edges: tuple[ContinuityEdge, ...]
    segments: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class _State:
    score: float
    previous_last: int
    action: Literal["skip", "start", "connect", "break"]
    edge: ContinuityEdge | None


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1 / (1 + exponential)
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def _safe_log(value: float) -> float:
    return math.log(max(1e-12, min(1.0, value)))


def _expected_gap_threshold_seconds(
    local_interval_seconds: float | None,
    config: ContinuityConfig,
) -> float:
    if local_interval_seconds is None or local_interval_seconds <= 0:
        return config.minimum_gap_seconds
    return min(
        config.maximum_gap_seconds,
        max(config.minimum_gap_seconds, local_interval_seconds * config.expected_interval_multiplier),
    )


def _make_edge(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    from_index: int,
    to_index: int,
    local_interval_seconds: float | None,
    config: ContinuityConfig,
) -> ContinuityEdge:
    start = points[from_index]
    end = points[to_index]
    elapsed_seconds = (end.recorded_at - start.recorded_at).total_seconds()
    displacement_meters = haversine_meters(
        start.wgs_latitude,
        start.wgs_longitude,
        end.wgs_latitude,
        end.wgs_longitude,
    )
    calculated_speed = displacement_meters / elapsed_seconds if elapsed_seconds > 0 else None
    reasons: list[str] = []

    if elapsed_seconds <= 0:
        probability = 1e-12
        reasons.append("non_increasing_time")
    else:
        speed_probability = _sigmoid(
            (config.maximum_continuous_speed_mps - (calculated_speed or 0))
            / config.speed_transition_softness_mps
        )
        gap_threshold = _expected_gap_threshold_seconds(local_interval_seconds, config)
        if elapsed_seconds > gap_threshold:
            gap_probability = max(1e-6, gap_threshold / elapsed_seconds * 0.02)
            reasons.append("adaptive_sampling_gap")
        else:
            gap_probability = 1.0
        probability = speed_probability * gap_probability
        if speed_probability < 0.5:
            reasons.append("network_speed_implausible")

    skipped = range(from_index + 1, to_index)
    if skipped:
        if all(assessments[index].anomaly_probability >= 0.5 for index in skipped):
            reasons.append("bypasses_suspect_points")
        else:
            probability *= 0.05
            reasons.append("bypasses_supported_point")

    return ContinuityEdge(
        from_index=from_index,
        to_index=to_index,
        kind="adjacent" if to_index == from_index + 1 else "bypass",
        elapsed_seconds=elapsed_seconds,
        displacement_meters=displacement_meters,
        calculated_speed_mps=calculated_speed,
        continuity_probability=max(1e-12, min(1.0, probability)),
        reason_codes=tuple(reasons),
    )


def _best_state(current: _State | None, candidate: _State) -> _State:
    if current is None or candidate.score > current.score:
        return candidate
    return current


def select_continuity(
    points: Sequence[LocatedObservation],
    assessments: Sequence[PointAssessment],
    *,
    local_intervals_seconds: Sequence[float | None] | None = None,
    config: ContinuityConfig = ContinuityConfig(),
) -> ContinuityResult:
    if len(points) != len(assessments):
        raise ValueError("points and assessments must have equal length")
    if local_intervals_seconds is not None and len(local_intervals_seconds) != len(points):
        raise ValueError("local_intervals_seconds must match points length")
    if not points:
        return ContinuityResult((), (), (), ())
    if config.max_skipped_points < 0:
        raise ValueError("max_skipped_points cannot be negative")

    # Each layer contains only recent possible last-kept indices. This is a bounded
    # dynamic program over keep, skip, connect and break actions.
    layers: list[dict[int, _State]] = [{-1: _State(0.0, -1, "start", None)}]

    for index, assessment in enumerate(assessments):
        previous_layer = layers[-1]
        current_layer: dict[int, _State] = {}
        local_interval = (
            local_intervals_seconds[index] if local_intervals_seconds is not None else None
        )

        for last_index, state in previous_layer.items():
            if last_index == -1 or index - last_index <= config.max_skipped_points:
                # A high point-anomaly probability is not enough to silently erase a
                # sample. Deletion needs local/topological support (for example, a
                # far jump followed by a short return); otherwise retain the point
                # and let a low-probability edge create an explicit continuity break.
                evidence_penalty = (
                    0.0
                    if assessment.exclusion_supported
                    else config.unsupported_skip_penalty
                )
                skip_score = (
                    state.score
                    + _safe_log(assessment.anomaly_probability)
                    - config.skip_penalty
                    - evidence_penalty
                )
                current_layer[last_index] = _best_state(
                    current_layer.get(last_index),
                    _State(skip_score, last_index, "skip", None),
                )

            structural_anomaly_probability = assessment.anomaly_probability
            if not assessment.exclusion_supported:
                structural_anomaly_probability = min(
                    structural_anomaly_probability,
                    config.unsupported_anomaly_probability_cap,
                )
            keep_probability = 1.0 - structural_anomaly_probability
            if last_index == -1:
                start_candidate = _State(
                    state.score + _safe_log(keep_probability), -1, "start", None
                )
                current_layer[index] = _best_state(
                    current_layer.get(index), start_candidate
                )
                continue

            skipped_count = index - last_index - 1
            if skipped_count <= config.max_skipped_points:
                edge = _make_edge(
                    points,
                    assessments,
                    last_index,
                    index,
                    local_interval,
                    config,
                )
                connected_score = (
                    state.score
                    + _safe_log(keep_probability)
                    + _safe_log(edge.continuity_probability)
                    - skipped_count * config.skip_penalty
                )
                if edge.continuity_probability >= config.connected_edge_probability_min:
                    current_layer[index] = _best_state(
                        current_layer.get(index),
                        _State(connected_score, last_index, "connect", edge),
                    )

            break_edge = ContinuityEdge(
                from_index=last_index,
                to_index=index,
                kind="break",
                elapsed_seconds=(points[index].recorded_at - points[last_index].recorded_at).total_seconds(),
                displacement_meters=haversine_meters(
                    points[last_index].wgs_latitude,
                    points[last_index].wgs_longitude,
                    points[index].wgs_latitude,
                    points[index].wgs_longitude,
                ),
                calculated_speed_mps=None,
                continuity_probability=0.0,
                reason_codes=(
                    "continuity_break",
                    *(
                        ("observation_gap_unknown",)
                        if (
                            points[index].recorded_at - points[last_index].recorded_at
                        ).total_seconds()
                        > _expected_gap_threshold_seconds(local_interval, config)
                        else ("implausible_transition",)
                    ),
                ),
            )
            break_candidate = _State(
                state.score + _safe_log(keep_probability) + config.break_penalty,
                last_index,
                "break",
                break_edge,
            )
            current_layer[index] = _best_state(
                current_layer.get(index), break_candidate
            )

        # Once a previous selected point is farther than the skip budget, retaining
        # that state cannot create another continuous edge.
        current_layer = {
            last: state
            for last, state in current_layer.items()
            if last == -1 or index - last <= config.max_skipped_points
        }
        layers.append(current_layer)

    final_layer = layers[-1]
    final_last = max(final_layer, key=lambda key: final_layer[key].score)
    selected_reversed: list[int] = []
    edge_reversed: list[ContinuityEdge] = []
    last = final_last

    for layer_index in range(len(points), 0, -1):
        state = layers[layer_index][last]
        point_index = layer_index - 1
        if state.action != "skip":
            selected_reversed.append(point_index)
            if state.edge is not None:
                edge_reversed.append(state.edge)
        last = state.previous_last

    selected = tuple(reversed(selected_reversed))
    edges = tuple(reversed(edge_reversed))
    selected_set = set(selected)
    skipped = tuple(index for index in range(len(points)) if index not in selected_set)

    segments: list[list[int]] = []
    if selected:
        segments.append([selected[0]])
        for edge in edges:
            if edge.kind == "break":
                segments.append([edge.to_index])
            else:
                segments[-1].append(edge.to_index)

    return ContinuityResult(
        selected_point_indices=selected,
        skipped_point_indices=skipped,
        edges=edges,
        segments=tuple(tuple(segment) for segment in segments),
    )
