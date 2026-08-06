from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from routefrom_pipeline.quality import haversine_meters
from routefrom_pipeline.stays import StayResult, StationaryEvent


@dataclass(frozen=True, slots=True)
class PlaceConfig:
    spatial_scale_floor_meters: float = 20.0
    spatial_scale_cap_meters: float = 220.0
    event_radius_weight: float = 0.5
    merge_probability_min: float = 0.58
    auto_bind_probability_min: float = 0.55
    auto_bind_margin_min: float = 0.12
    unknown_place_prior: float = 0.22
    maximum_candidates: int = 3
    frequent_visit_center: float = 3.0
    frequent_distinct_day_center: float = 2.0
    frequent_dwell_hours_center: float = 4.0
    frequent_span_days_center: float = 7.0


@dataclass(frozen=True, slots=True)
class PlaceCandidate:
    place_index: int
    probability: float
    distance_meters: float


@dataclass(frozen=True, slots=True)
class PlaceBinding:
    stationary_event_index: int
    place_index: int | None
    probability: float
    probability_margin: float
    candidates: tuple[PlaceCandidate, ...]
    evidence: dict[str, float | bool | str]


@dataclass(frozen=True, slots=True)
class PlaceCluster:
    visit_event_indices: tuple[int, ...]
    anchor_event_index: int
    centroid_latitude: float
    centroid_longitude: float
    spatial_radius_meters: float
    adaptive_spatial_scale_meters: float
    timezone: str
    visit_count: int
    distinct_local_date_count: int
    observed_duration_seconds: float
    first_visited_at: datetime
    last_visited_at: datetime
    confidence: float
    frequent_probability: float
    evidence: dict[str, float | bool | str]


@dataclass(frozen=True, slots=True)
class PlaceResult:
    places: tuple[PlaceCluster, ...]
    bindings: tuple[PlaceBinding, ...]
    unresolved_event_indices: tuple[int, ...]


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1.0 / (1.0 + exponential)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _event_weight(event: StationaryEvent) -> float:
    semantic_confidence = math.sqrt(max(0.0, event.visit_probability * event.confidence))
    return max(0.05, semantic_confidence) * math.sqrt(max(1.0, event.effective_point_count))


def _event_scale(event: StationaryEvent, config: PlaceConfig) -> float:
    return min(
        config.spatial_scale_cap_meters,
        max(
            config.spatial_scale_floor_meters,
            event.adaptive_spatial_scale_meters
            + config.event_radius_weight * event.spatial_radius_meters,
        ),
    )


def _event_pair_probability(
    left: StationaryEvent,
    right: StationaryEvent,
    config: PlaceConfig,
) -> float:
    distance = haversine_meters(
        left.centroid_latitude,
        left.centroid_longitude,
        right.centroid_latitude,
        right.centroid_longitude,
    )
    combined_scale = math.hypot(_event_scale(left, config), _event_scale(right, config))
    spatial_likelihood = math.exp(-0.5 * (distance / combined_scale) ** 2)
    evidence_quality = math.sqrt(
        max(0.0, left.visit_probability * left.confidence)
        * max(0.0, right.visit_probability * right.confidence)
    )
    return max(0.0, min(1.0, spatial_likelihood * (0.65 + 0.35 * evidence_quality)))


def _project_meters(latitude: float, longitude: float) -> tuple[float, float]:
    earth_radius = 6_371_008.8
    bounded_latitude = max(-85.0, min(85.0, latitude))
    return (
        earth_radius * math.radians(longitude),
        earth_radius
        * math.log(math.tan(math.pi / 4 + math.radians(bounded_latitude) / 2)),
    )


def _candidate_pair_scores(
    event_indices: Sequence[int],
    events: Sequence[StationaryEvent],
    config: PlaceConfig,
) -> dict[tuple[int, int], float]:
    maximum_combined_scale = math.hypot(
        config.spatial_scale_cap_meters,
        config.spatial_scale_cap_meters,
    )
    maximum_candidate_distance = maximum_combined_scale * math.sqrt(
        -2 * math.log(config.merge_probability_min)
    )
    cell_size = max(1.0, maximum_candidate_distance)
    buckets: dict[tuple[int, int], list[int]] = {}
    coordinates: dict[int, tuple[float, float]] = {}
    for event_index in event_indices:
        event = events[event_index]
        x, y = _project_meters(event.centroid_latitude, event.centroid_longitude)
        coordinates[event_index] = (x, y)
        bucket = (math.floor(x / cell_size), math.floor(y / cell_size))
        buckets.setdefault(bucket, []).append(event_index)

    scores: dict[tuple[int, int], float] = {}
    for left_index in event_indices:
        x, y = coordinates[left_index]
        bucket_x = math.floor(x / cell_size)
        bucket_y = math.floor(y / cell_size)
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for right_index in buckets.get(
                    (bucket_x + x_offset, bucket_y + y_offset), ()
                ):
                    if right_index <= left_index:
                        continue
                    if math.hypot(
                        coordinates[right_index][0] - x,
                        coordinates[right_index][1] - y,
                    ) > maximum_candidate_distance:
                        continue
                    probability = _event_pair_probability(
                        events[left_index], events[right_index], config
                    )
                    if probability >= config.merge_probability_min:
                        scores[(left_index, right_index)] = probability
    return scores


def _complete_link_clusters(
    event_indices: Sequence[int],
    events: Sequence[StationaryEvent],
    config: PlaceConfig,
) -> list[tuple[int, ...]]:
    pair_scores = _candidate_pair_scores(event_indices, events, config)
    members = {event_index: {event_index} for event_index in event_indices}
    cluster_by_event = {event_index: event_index for event_index in event_indices}
    ranked_edges = sorted(
        pair_scores.items(),
        key=lambda item: (-item[1], item[0]),
    )
    for (left_event, right_event), _ in ranked_edges:
        left_cluster = cluster_by_event[left_event]
        right_cluster = cluster_by_event[right_event]
        if left_cluster == right_cluster:
            continue
        if any(
            pair_scores.get(tuple(sorted((left, right))), 0.0)
            < config.merge_probability_min
            for left in members[left_cluster]
            for right in members[right_cluster]
        ):
            continue
        retained_cluster = min(left_cluster, right_cluster)
        removed_cluster = max(left_cluster, right_cluster)
        members[retained_cluster].update(members.pop(removed_cluster))
        for event_index in members[retained_cluster]:
            cluster_by_event[event_index] = retained_cluster
    return [
        tuple(sorted(cluster_members))
        for _, cluster_members in sorted(members.items())
    ]


def _weighted_centroid(
    event_indices: Sequence[int],
    events: Sequence[StationaryEvent],
) -> tuple[float, float]:
    weights = [_event_weight(events[index]) for index in event_indices]
    total = sum(weights)
    return (
        sum(
            events[index].centroid_latitude * weight
            for index, weight in zip(event_indices, weights)
        )
        / total,
        sum(
            events[index].centroid_longitude * weight
            for index, weight in zip(event_indices, weights)
        )
        / total,
    )


def _frequent_probability(
    visit_count: int,
    distinct_date_count: int,
    duration_seconds: float,
    span_days: float,
    config: PlaceConfig,
) -> tuple[float, dict[str, float]]:
    visit_evidence = _sigmoid(visit_count - config.frequent_visit_center)
    day_evidence = _sigmoid(distinct_date_count - config.frequent_distinct_day_center)
    dwell_hours = duration_seconds / 3_600
    dwell_evidence = _sigmoid(
        math.log1p(dwell_hours) - math.log1p(config.frequent_dwell_hours_center)
    )
    span_evidence = _sigmoid(
        (span_days - config.frequent_span_days_center)
        / max(1.0, config.frequent_span_days_center / 2)
    )
    probability = (
        0.35 * visit_evidence
        + 0.30 * day_evidence
        + 0.20 * dwell_evidence
        + 0.15 * span_evidence
    )
    return probability, {
        "visit_evidence": visit_evidence,
        "distinct_day_evidence": day_evidence,
        "dwell_evidence": dwell_evidence,
        "span_evidence": span_evidence,
    }


def _build_place(
    event_indices: Sequence[int],
    events: Sequence[StationaryEvent],
    timezone: ZoneInfo,
    config: PlaceConfig,
) -> PlaceCluster:
    latitude, longitude = _weighted_centroid(event_indices, events)
    distances = sorted(
        haversine_meters(
            latitude,
            longitude,
            events[index].centroid_latitude,
            events[index].centroid_longitude,
        )
        + events[index].spatial_radius_meters
        for index in event_indices
    )
    radius_index = min(len(distances) - 1, math.floor(0.9 * len(distances)))
    spatial_radius = distances[radius_index]
    scales = [_event_scale(events[index], config) for index in event_indices]
    started_at = min(events[index].observed_started_at for index in event_indices)
    ended_at = max(events[index].observed_ended_at for index in event_indices)
    duration = sum(events[index].observed_duration_seconds for index in event_indices)
    local_dates = {
        events[index].observed_started_at.astimezone(timezone).date()
        for index in event_indices
    }
    confidence = sum(
        _event_weight(events[index])
        * math.sqrt(max(0.0, events[index].visit_probability * events[index].confidence))
        for index in event_indices
    ) / sum(_event_weight(events[index]) for index in event_indices)
    span_days = max(0.0, (ended_at - started_at).total_seconds() / 86_400)
    frequent_probability, frequent_evidence = _frequent_probability(
        len(event_indices), len(local_dates), duration, span_days, config
    )
    anchor = min(event_indices, key=lambda index: events[index].observed_started_at)
    return PlaceCluster(
        visit_event_indices=tuple(event_indices),
        anchor_event_index=anchor,
        centroid_latitude=latitude,
        centroid_longitude=longitude,
        spatial_radius_meters=spatial_radius,
        adaptive_spatial_scale_meters=statistics.median(scales),
        timezone=timezone.key,
        visit_count=len(event_indices),
        distinct_local_date_count=len(local_dates),
        observed_duration_seconds=duration,
        first_visited_at=started_at,
        last_visited_at=ended_at,
        confidence=confidence,
        frequent_probability=frequent_probability,
        evidence={
            **frequent_evidence,
            "place_stage": "trajectory_cluster_without_map_context",
            "complete_link_merge_probability_min": config.merge_probability_min,
        },
    )


def _event_place_score(
    event: StationaryEvent,
    place: PlaceCluster,
    events: Sequence[StationaryEvent],
    config: PlaceConfig,
) -> tuple[float, float]:
    distance = haversine_meters(
        event.centroid_latitude,
        event.centroid_longitude,
        place.centroid_latitude,
        place.centroid_longitude,
    )
    compatibility = min(
        _event_pair_probability(event, events[index], config)
        for index in place.visit_event_indices
    )
    return compatibility, distance


def resolve_places(
    stays: StayResult,
    *,
    eligible_event_indices: Sequence[int],
    timezone: str = "Asia/Shanghai",
    config: PlaceConfig = PlaceConfig(),
) -> PlaceResult:
    """Cluster confirmed visits and bind them only when posterior separation is clear."""

    if not 0 < config.merge_probability_min < 1:
        raise ValueError("merge_probability_min must be between zero and one")
    if not 0 < config.auto_bind_probability_min < 1:
        raise ValueError("auto_bind_probability_min must be between zero and one")
    if not 0 <= config.auto_bind_margin_min < 1:
        raise ValueError("auto_bind_margin_min must be in [0, 1)")
    if config.unknown_place_prior <= 0:
        raise ValueError("unknown_place_prior must be positive")
    if config.maximum_candidates < 1:
        raise ValueError("maximum_candidates must be positive")
    try:
        timezone_info = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc
    unique_indices = tuple(sorted(set(eligible_event_indices)))
    if any(index < 0 or index >= len(stays.events) for index in unique_indices):
        raise ValueError("eligible_event_indices contains an invalid event index")
    if not unique_indices:
        return PlaceResult((), (), ())

    clustered_indices = _complete_link_clusters(
        unique_indices, stays.events, config
    )
    places = tuple(
        _build_place(indices, stays.events, timezone_info, config)
        for indices in clustered_indices
    )
    maximum_binding_distance = math.hypot(
        config.spatial_scale_cap_meters,
        config.spatial_scale_cap_meters,
    ) * math.sqrt(-2 * math.log(0.01))
    binding_cell_size = max(1.0, maximum_binding_distance)
    place_coordinates: dict[int, tuple[float, float]] = {}
    place_buckets: dict[tuple[int, int], list[int]] = {}
    owning_place_by_event: dict[int, int] = {}
    for place_index, place in enumerate(places):
        coordinates = _project_meters(
            place.centroid_latitude, place.centroid_longitude
        )
        place_coordinates[place_index] = coordinates
        bucket = (
            math.floor(coordinates[0] / binding_cell_size),
            math.floor(coordinates[1] / binding_cell_size),
        )
        place_buckets.setdefault(bucket, []).append(place_index)
        for event_index in place.visit_event_indices:
            owning_place_by_event[event_index] = place_index
    bindings: list[PlaceBinding] = []
    unresolved: list[int] = []
    for event_index in unique_indices:
        event = stays.events[event_index]
        event_coordinates = _project_meters(
            event.centroid_latitude, event.centroid_longitude
        )
        event_bucket = (
            math.floor(event_coordinates[0] / binding_cell_size),
            math.floor(event_coordinates[1] / binding_cell_size),
        )
        candidate_place_indices = {owning_place_by_event[event_index]}
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for place_index in place_buckets.get(
                    (event_bucket[0] + x_offset, event_bucket[1] + y_offset), ()
                ):
                    place_coordinates_for_index = place_coordinates[place_index]
                    if math.hypot(
                        place_coordinates_for_index[0] - event_coordinates[0],
                        place_coordinates_for_index[1] - event_coordinates[1],
                    ) <= maximum_binding_distance:
                        candidate_place_indices.add(place_index)
        raw_candidates = [
            (
                *_event_place_score(
                    event, places[place_index], stays.events, config
                ),
                place_index,
            )
            for place_index in sorted(candidate_place_indices)
        ]
        denominator = config.unknown_place_prior + sum(score for score, _, _ in raw_candidates)
        ranked = sorted(
            (
                PlaceCandidate(place_index, score / denominator, distance)
                for score, distance, place_index in raw_candidates
            ),
            key=lambda candidate: (-candidate.probability, candidate.distance_meters),
        )
        best = ranked[0]
        second_probability = ranked[1].probability if len(ranked) > 1 else 0.0
        margin = best.probability - second_probability
        assigned = (
            best.probability >= config.auto_bind_probability_min
            and margin >= config.auto_bind_margin_min
        )
        if not assigned:
            unresolved.append(event_index)
        bindings.append(
            PlaceBinding(
                stationary_event_index=event_index,
                place_index=best.place_index if assigned else None,
                probability=best.probability,
                probability_margin=margin,
                candidates=tuple(ranked[: config.maximum_candidates]),
                evidence={
                    "auto_bound": assigned,
                    "unknown_place_prior": config.unknown_place_prior,
                    "map_context_available": False,
                },
            )
        )
    return PlaceResult(places, tuple(bindings), tuple(unresolved))
