from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID

from routefrom_pipeline.modes import ModeResult
from routefrom_pipeline.quality import LocatedObservation, haversine_meters
from routefrom_pipeline.trajectory import TrajectoryVertex


@dataclass(frozen=True, slots=True)
class MapSnapshotRef:
    id: UUID
    provider: str
    dataset_name: str
    snapshot_version: str
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class MapMatchConfig:
    minimum_points: int = 3
    max_points_per_request: int = 450
    search_radius_meters: float = 60.0
    default_gps_accuracy_meters: float = 20.0
    minimum_accepted_coverage: float = 1.0
    maximum_median_residual_meters: float = 25.0
    maximum_p95_residual_meters: float = 60.0
    maximum_path_ratio: float = 3.0
    segment_confidence_min: float = 0.65
    preferred_moving_coverage_min: float = 0.70
    preferred_confidence_min: float = 0.70


@dataclass(frozen=True, slots=True)
class MapMatchRequest:
    request_index: int
    point_indices: tuple[int, ...]
    costing: str
    coordinates: tuple[tuple[float, float], ...]
    recorded_at: tuple[datetime, ...]
    accuracy_meters: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class MatchedObservation:
    point_index: int
    latitude: float | None
    longitude: float | None
    match_type: str
    edge_id: str | None
    way_id: int | None
    distance_from_trace_point_meters: float | None
    route_position: float | None
    begin_route_discontinuity: bool = False
    end_route_discontinuity: bool = False


@dataclass(frozen=True, slots=True)
class MatcherResponse:
    path: tuple[tuple[float, float], ...]
    observations: tuple[MatchedObservation, ...]
    edge_ids: tuple[str, ...]
    way_ids: tuple[int, ...]
    provider_metadata: dict[str, object]


class MapMatcher(Protocol):
    name: str
    version: str

    def match(self, request: MapMatchRequest, config: MapMatchConfig) -> MatcherResponse: ...


@dataclass(frozen=True, slots=True)
class MapMatchSegment:
    request_index: int
    point_indices: tuple[int, ...]
    costing: str
    status: str
    confidence: float
    matched_fraction: float
    median_residual_meters: float | None
    p95_residual_meters: float | None
    path_ratio: float | None
    has_route_discontinuity: bool
    reason_codes: tuple[str, ...]
    observations: tuple[MatchedObservation, ...]
    vertices: tuple[TrajectoryVertex, ...]
    edge_ids: tuple[str, ...]
    way_ids: tuple[int, ...]
    provider_metadata: dict[str, object]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True, slots=True)
class MapMatchResult:
    matcher_name: str
    matcher_version: str
    snapshot: MapSnapshotRef
    segments: tuple[MapMatchSegment, ...]
    eligible_point_count: int
    accepted_point_count: int
    moving_coverage: float
    confidence: float
    preferred: bool
    fallback_reason: str | None


_COSTING_BY_MODE = {
    "walk": "pedestrian",
    "run": "pedestrian",
    "bicycle": "bicycle",
    "e_bike": "bicycle",
    "car": "auto",
    "bus": "auto",
    "motorcycle": "auto",
    "scooter": "auto",
}


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _requests(
    points: Sequence[LocatedObservation],
    modes: ModeResult,
    config: MapMatchConfig,
) -> tuple[MapMatchRequest, ...]:
    requests: list[MapMatchRequest] = []
    for leg in modes.legs:
        costing = _COSTING_BY_MODE.get(leg.selected_mode_code)
        if costing is None or len(leg.point_indices) < config.minimum_points:
            continue
        start = 0
        indices = leg.point_indices
        while start < len(indices) - 1:
            end = min(len(indices), start + config.max_points_per_request)
            chunk = tuple(indices[start:end])
            if len(chunk) < config.minimum_points:
                break
            requests.append(
                MapMatchRequest(
                    request_index=len(requests),
                    point_indices=chunk,
                    costing=costing,
                    coordinates=tuple(
                        (points[index].wgs_latitude, points[index].wgs_longitude)
                        for index in chunk
                    ),
                    recorded_at=tuple(points[index].recorded_at for index in chunk),
                    accuracy_meters=tuple(
                        max(
                            1.0,
                            points[index].horizontal_accuracy_meters
                            or config.default_gps_accuracy_meters,
                        )
                        for index in chunk
                    ),
                )
            )
            if end == len(indices):
                break
            next_start = end - 1
            if len(indices) - next_start < config.minimum_points:
                next_start = max(start + 1, len(indices) - config.minimum_points)
            start = next_start
    return tuple(requests)


def _path_distance(path: Sequence[tuple[float, float]]) -> float:
    return sum(
        haversine_meters(*left, *right) for left, right in zip(path, path[1:])
    )


def _observed_distance(request: MapMatchRequest) -> float:
    return _path_distance(request.coordinates)


def _route_vertices(
    request: MapMatchRequest,
    response: MatcherResponse,
) -> tuple[TrajectoryVertex, ...]:
    positioned = [item for item in response.observations if item.route_position is not None]
    if len(positioned) != len(request.point_indices) or not response.path:
        return ()
    measures = [float(item.route_position) for item in positioned]
    if any(right + 1e-8 < left for left, right in zip(measures, measures[1:])):
        return ()
    first_measure, last_measure = measures[0], measures[-1]
    items: list[tuple[float, int | None, float, float]] = []
    for shape_index, (latitude, longitude) in enumerate(response.path):
        if first_measure < shape_index < last_measure:
            items.append((float(shape_index), None, latitude, longitude))
    for observation in positioned:
        if observation.latitude is None or observation.longitude is None:
            return ()
        items.append(
            (
                float(observation.route_position),
                observation.point_index,
                observation.latitude,
                observation.longitude,
            )
        )
    items.sort(key=lambda item: (item[0], item[1] is None))
    observation_by_index = {
        point_index: position for position, point_index, _, _ in items if point_index is not None
    }
    vertices: list[TrajectoryVertex] = []
    for measure, point_index, latitude, longitude in items:
        if point_index is not None:
            source_position = request.point_indices.index(point_index)
            recorded_at = request.recorded_at[source_position]
        else:
            right_index = next(
                index for index, value in enumerate(measures) if value >= measure
            )
            left_index = max(0, right_index - 1)
            left_measure = measures[left_index]
            right_measure = measures[right_index]
            span = right_measure - left_measure
            fraction = 0.0 if span <= 1e-9 else (measure - left_measure) / span
            elapsed = request.recorded_at[right_index] - request.recorded_at[left_index]
            recorded_at = request.recorded_at[left_index] + elapsed * fraction
        vertex = TrajectoryVertex(
            sequence_number=None,
            point_index=point_index,
            recorded_at=recorded_at,
            latitude=latitude,
            longitude=longitude,
            importance_meters=0.0,
            is_interpolated=point_index is None,
            anchor_reasons=(),
        )
        if vertices and vertex.recorded_at == vertices[-1].recorded_at:
            if vertices[-1].point_index is None or point_index is not None:
                vertices[-1] = vertex
        else:
            vertices.append(vertex)
    if set(observation_by_index) != set(request.point_indices):
        return ()
    return tuple(vertices)


def _evaluate(
    request: MapMatchRequest,
    response: MatcherResponse,
    config: MapMatchConfig,
) -> MapMatchSegment:
    reasons: list[str] = []
    matched = [
        item
        for item in response.observations
        if item.match_type in ("matched", "interpolated")
        and item.distance_from_trace_point_meters is not None
    ]
    matched_fraction = len(matched) / len(request.point_indices)
    residuals = [float(item.distance_from_trace_point_meters) for item in matched]
    median_residual = statistics.median(residuals) if residuals else None
    p95_residual = _quantile(residuals, 0.95) if residuals else None
    has_discontinuity = any(
        item.begin_route_discontinuity or item.end_route_discontinuity
        for item in response.observations
    )
    observed_distance = _observed_distance(request)
    path_distance = _path_distance(response.path)
    path_ratio = path_distance / max(observed_distance, 1.0)
    adaptive_median_limit = min(
        config.maximum_median_residual_meters,
        max(10.0, statistics.median(request.accuracy_meters) * 1.5),
    )
    adaptive_p95_limit = min(
        config.maximum_p95_residual_meters,
        max(25.0, _quantile(request.accuracy_meters, 0.95) * 2.0),
    )
    if matched_fraction < config.minimum_accepted_coverage:
        reasons.append("incomplete_observation_coverage")
    if median_residual is None or median_residual > adaptive_median_limit:
        reasons.append("median_residual_too_large")
    if p95_residual is None or p95_residual > adaptive_p95_limit:
        reasons.append("p95_residual_too_large")
    if path_ratio > config.maximum_path_ratio:
        reasons.append("implausible_network_detour")
    if has_discontinuity:
        reasons.append("route_discontinuity")
    vertices = _route_vertices(request, response)
    if not vertices:
        reasons.append("route_geometry_unusable")
    residual_score = (
        0.0
        if median_residual is None
        else max(0.0, 1.0 - median_residual / max(adaptive_median_limit, 1.0))
    )
    p95_score = (
        0.0
        if p95_residual is None
        else max(0.0, 1.0 - p95_residual / max(adaptive_p95_limit, 1.0))
    )
    detour_score = max(0.0, 1.0 - max(0.0, path_ratio - 1.0) / 2.0)
    confidence = max(
        0.0,
        min(
            1.0,
            0.45 * matched_fraction
            + 0.25 * residual_score
            + 0.15 * p95_score
            + 0.15 * detour_score
            - (0.35 if has_discontinuity else 0.0),
        ),
    )
    if confidence < config.segment_confidence_min:
        reasons.append("segment_confidence_below_threshold")
    return MapMatchSegment(
        request_index=request.request_index,
        point_indices=request.point_indices,
        costing=request.costing,
        status="accepted" if not reasons else "rejected",
        confidence=confidence,
        matched_fraction=matched_fraction,
        median_residual_meters=median_residual,
        p95_residual_meters=p95_residual,
        path_ratio=path_ratio,
        has_route_discontinuity=has_discontinuity,
        reason_codes=tuple(reasons),
        observations=response.observations,
        vertices=vertices,
        edge_ids=response.edge_ids,
        way_ids=response.way_ids,
        provider_metadata=response.provider_metadata,
    )


def match_trace(
    points: Sequence[LocatedObservation],
    modes: ModeResult,
    matcher: MapMatcher,
    snapshot: MapSnapshotRef,
    *,
    config: MapMatchConfig = MapMatchConfig(),
) -> MapMatchResult:
    requests = _requests(points, modes, config)
    results: list[MapMatchSegment] = []
    for request in requests:
        try:
            results.append(_evaluate(request, matcher.match(request, config), config))
        except Exception as error:
            results.append(
                MapMatchSegment(
                    request_index=request.request_index,
                    point_indices=request.point_indices,
                    costing=request.costing,
                    status="error",
                    confidence=0.0,
                    matched_fraction=0.0,
                    median_residual_meters=None,
                    p95_residual_meters=None,
                    path_ratio=None,
                    has_route_discontinuity=False,
                    reason_codes=("matcher_error",),
                    observations=(),
                    vertices=(),
                    edge_ids=(),
                    way_ids=(),
                    provider_metadata={"error_type": type(error).__name__, "error": str(error)[:500]},
                )
            )
    eligible_indices = {
        point_index for request in requests for point_index in request.point_indices
    }
    accepted_indices = {
        point_index
        for segment in results
        if segment.accepted
        for point_index in segment.point_indices
    }
    eligible = len(eligible_indices)
    accepted = len(accepted_indices)
    coverage = accepted / eligible if eligible else 0.0
    confidence = (
        sum(segment.confidence * len(segment.point_indices) for segment in results)
        / sum(len(segment.point_indices) for segment in results)
        if eligible
        else 0.0
    )
    preferred = (
        bool(results)
        and coverage >= config.preferred_moving_coverage_min
        and confidence >= config.preferred_confidence_min
    )
    fallback_reason = None
    if not requests:
        fallback_reason = "no_supported_mobility_legs"
    elif not preferred:
        fallback_reason = "map_match_coverage_or_confidence"
    return MapMatchResult(
        matcher_name=matcher.name,
        matcher_version=matcher.version,
        snapshot=snapshot,
        segments=tuple(results),
        eligible_point_count=eligible,
        accepted_point_count=accepted,
        moving_coverage=coverage,
        confidence=confidence,
        preferred=preferred,
        fallback_reason=fallback_reason,
    )
