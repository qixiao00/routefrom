from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from routefrom_pipeline.continuity import ContinuityResult
from routefrom_pipeline.modes import ModeResult
from routefrom_pipeline.quality import LocatedObservation
from routefrom_pipeline.stays import StayResult
from routefrom_pipeline.trips import TripResult


@dataclass(frozen=True, slots=True, order=True)
class TimeRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("time range start must be timezone-aware")
        if self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("time range end must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("time ranges must be non-empty and use [start, end)")


@dataclass(frozen=True, slots=True)
class TrajectoryConfig:
    max_chunk_vertices: int = 2_048
    max_chunk_estimated_bytes: int = 128 * 1_024
    estimated_bytes_per_vertex: int = 48
    split_chunks_at_semantic_anchors: bool = True


@dataclass(frozen=True, slots=True)
class TrajectoryVertex:
    sequence_number: int | None
    point_index: int | None
    recorded_at: datetime
    latitude: float
    longitude: float
    importance_meters: float | None
    is_interpolated: bool
    anchor_reasons: tuple[str, ...]

    @property
    def is_semantic_anchor(self) -> bool:
        return bool(self.anchor_reasons)


@dataclass(frozen=True, slots=True)
class TrajectorySegment:
    segment_index: int
    vertices: tuple[TrajectoryVertex, ...]
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class TrajectoryChunk:
    chunk_number: int
    segment_index: int
    vertices: tuple[TrajectoryVertex, ...]
    started_at: datetime
    ended_at: datetime
    first_sequence_number: int
    last_sequence_number: int
    estimated_bytes: int
    has_leading_overlap: bool


@dataclass(frozen=True, slots=True)
class TrajectoryRepresentation:
    variant_kind: str
    importance_algorithm: str
    segments: tuple[TrajectorySegment, ...]
    chunks: tuple[TrajectoryChunk, ...]


@dataclass(frozen=True, slots=True)
class VisiblePath:
    selection_range_index: int
    segment_index: int
    selected_range: TimeRange
    vertices: tuple[TrajectoryVertex, ...]
    source_chunk_numbers: tuple[int, ...]
    applied_importance_threshold_meters: float


def normalize_time_ranges(ranges: Sequence[TimeRange]) -> tuple[TimeRange, ...]:
    if not ranges:
        return ()
    ordered = sorted(ranges)
    normalized: list[TimeRange] = [ordered[0]]
    for current in ordered[1:]:
        previous = normalized[-1]
        if current.start <= previous.end:
            normalized[-1] = TimeRange(previous.start, max(previous.end, current.end))
        else:
            normalized.append(current)
    return tuple(normalized)


def _semantic_anchor_reasons(
    continuity: ContinuityResult,
    stays: StayResult,
    trips: TripResult,
    modes: ModeResult,
) -> dict[int, set[str]]:
    reasons: dict[int, set[str]] = {}

    def mark(point_index: int, reason: str) -> None:
        reasons.setdefault(point_index, set()).add(reason)

    for segment in continuity.segments:
        if segment:
            mark(segment[0], "continuity_segment_start")
            mark(segment[-1], "continuity_segment_end")
    for event in stays.events:
        mark(event.point_indices[0], "stationary_event_start")
        mark(event.point_indices[-1], "stationary_event_end")
    for trip in trips.trips:
        mark(trip.point_indices[0], "trip_start")
        mark(trip.point_indices[-1], "trip_end")
    for leg in modes.legs:
        mark(leg.point_indices[0], "mobility_leg_start")
        mark(leg.point_indices[-1], "mobility_leg_end")
    return reasons


def _project_meters(
    latitude: float,
    longitude: float,
    origin_latitude: float,
    origin_longitude: float,
) -> tuple[float, float]:
    radius = 6_371_008.8
    x = (
        math.radians(longitude - origin_longitude)
        * radius
        * math.cos(math.radians(origin_latitude))
    )
    y = math.radians(latitude - origin_latitude) * radius
    return x, y


def _distance_to_segment(
    start: tuple[float, float],
    point: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared),
    )
    return math.hypot(
        point[0] - (start[0] + fraction * dx),
        point[1] - (start[1] + fraction * dy),
    )


def _assign_interval_importance(
    coordinates: Sequence[tuple[float, float]],
    start: int,
    end: int,
    importance: list[float | None],
) -> None:
    if end - start <= 1:
        return
    # A local triangle-height score can discard every point on a gradual bend,
    # replacing the whole curve with one long invented chord. The RDP split
    # score bounds the deviation from each retained chord instead. A child
    # split can deviate more than its parent after the parent chord changes;
    # promote that score through its ancestors so a child is never required
    # while a parent is omitted at the same tolerance.
    pending = [(start, end, False)]
    splits: dict[tuple[int, int], tuple[int, float]] = {}
    effective_scores: dict[tuple[int, int], float] = {}
    while pending:
        left, right, visited = pending.pop()
        if right - left <= 1:
            continue
        if visited:
            split, deviation = splits[(left, right)]
            effective = max(
                deviation,
                effective_scores.get((left, split), 0.0),
                effective_scores.get((split, right), 0.0),
            )
            importance[split] = effective
            effective_scores[(left, right)] = effective
            continue
        split = left + 1
        deviation = -1.0
        for index in range(left + 1, right):
            current = _distance_to_segment(
                coordinates[left], coordinates[index], coordinates[right]
            )
            if current > deviation:
                deviation = current
                split = index
        if deviation <= 1e-9:
            continue
        splits[(left, right)] = (split, deviation)
        pending.append((left, right, True))
        pending.append((left, split, False))
        pending.append((split, right, False))


def _vertex_importance(
    points: Sequence[LocatedObservation],
    indices: Sequence[int],
    anchor_positions: set[int],
    coordinate_by_point: Mapping[int, tuple[float, float]],
) -> list[float | None]:
    origin_latitude = sum(
        coordinate_by_point.get(
            index, (points[index].wgs_latitude, points[index].wgs_longitude)
        )[0]
        for index in indices
    ) / len(indices)
    origin_longitude = sum(
        coordinate_by_point.get(
            index, (points[index].wgs_latitude, points[index].wgs_longitude)
        )[1]
        for index in indices
    ) / len(indices)
    coordinates = [
        _project_meters(
            *coordinate_by_point.get(
                index, (points[index].wgs_latitude, points[index].wgs_longitude)
            ),
            origin_latitude,
            origin_longitude,
        )
        for index in indices
    ]
    importance: list[float | None] = [0.0] * len(indices)
    anchors = sorted({0, len(indices) - 1, *anchor_positions})
    for anchor in anchors:
        importance[anchor] = None
    for start, end in zip(anchors, anchors[1:]):
        _assign_interval_importance(coordinates, start, end, importance)
    return importance


def _chunks_from_segments(
    segments: Sequence[TrajectorySegment],
    config: TrajectoryConfig,
) -> tuple[TrajectoryChunk, ...]:
    if config.max_chunk_vertices < 2:
        raise ValueError("max_chunk_vertices must be at least 2")
    if config.estimated_bytes_per_vertex <= 0:
        raise ValueError("estimated_bytes_per_vertex must be positive")
    byte_limited_vertices = max(
        2,
        config.max_chunk_estimated_bytes // config.estimated_bytes_per_vertex,
    )
    vertex_limit = min(config.max_chunk_vertices, byte_limited_vertices)
    chunks: list[TrajectoryChunk] = []
    for segment in segments:
        current: list[TrajectoryVertex] = []
        leading_overlap = False
        for position, vertex in enumerate(segment.vertices):
            current.append(vertex)
            is_internal_anchor = (
                config.split_chunks_at_semantic_anchors
                and vertex.is_semantic_anchor
                and position not in (0, len(segment.vertices) - 1)
            )
            reached_limit = len(current) >= vertex_limit
            if (is_internal_anchor or reached_limit) and position < len(segment.vertices) - 1:
                chunks.append(
                    _make_chunk(
                        len(chunks),
                        segment.segment_index,
                        current,
                        leading_overlap,
                        config,
                    )
                )
                current = [vertex]
                leading_overlap = True
        if current:
            chunks.append(
                _make_chunk(
                    len(chunks),
                    segment.segment_index,
                    current,
                    leading_overlap,
                    config,
                )
            )
    return tuple(chunks)


def _make_chunk(
    chunk_number: int,
    segment_index: int,
    vertices: Sequence[TrajectoryVertex],
    has_leading_overlap: bool,
    config: TrajectoryConfig,
) -> TrajectoryChunk:
    first_sequence = vertices[0].sequence_number
    last_sequence = vertices[-1].sequence_number
    if first_sequence is None or last_sequence is None:
        raise ValueError("stored trajectory chunks cannot contain query interpolation vertices")
    return TrajectoryChunk(
        chunk_number=chunk_number,
        segment_index=segment_index,
        vertices=tuple(vertices),
        started_at=vertices[0].recorded_at,
        ended_at=vertices[-1].recorded_at,
        first_sequence_number=first_sequence,
        last_sequence_number=last_sequence,
        estimated_bytes=len(vertices) * config.estimated_bytes_per_vertex,
        has_leading_overlap=has_leading_overlap,
    )


def build_trajectory_representation(
    points: Sequence[LocatedObservation],
    continuity: ContinuityResult,
    stays: StayResult,
    trips: TripResult,
    modes: ModeResult,
    *,
    config: TrajectoryConfig = TrajectoryConfig(),
    coordinate_by_point: Mapping[int, tuple[float, float]] | None = None,
    variant_kind: str = "cleaned_gps",
) -> TrajectoryRepresentation:
    if variant_kind not in ("cleaned_gps", "smoothed_gps", "map_matched"):
        raise ValueError("unsupported trajectory variant_kind")
    coordinates = coordinate_by_point or {}
    anchor_reasons = _semantic_anchor_reasons(continuity, stays, trips, modes)
    segments: list[TrajectorySegment] = []
    sequence_number = 0
    for segment_index, point_indices in enumerate(continuity.segments):
        if not point_indices:
            continue
        anchor_positions = {
            position
            for position, point_index in enumerate(point_indices)
            if point_index in anchor_reasons
        }
        importance = _vertex_importance(
            points, point_indices, anchor_positions, coordinates
        )
        vertices: list[TrajectoryVertex] = []
        for position, point_index in enumerate(point_indices):
            latitude, longitude = coordinates.get(
                point_index,
                (points[point_index].wgs_latitude, points[point_index].wgs_longitude),
            )
            vertices.append(
                TrajectoryVertex(
                    sequence_number=sequence_number,
                    point_index=point_index,
                    recorded_at=points[point_index].recorded_at,
                    latitude=latitude,
                    longitude=longitude,
                    importance_meters=importance[position],
                    is_interpolated=False,
                    anchor_reasons=tuple(sorted(anchor_reasons.get(point_index, ()))),
                )
            )
            sequence_number += 1
        segments.append(
            TrajectorySegment(
                segment_index=segment_index,
                vertices=tuple(vertices),
                started_at=vertices[0].recorded_at,
                ended_at=vertices[-1].recorded_at,
            )
        )
    chunks = _chunks_from_segments(segments, config)
    return TrajectoryRepresentation(
        variant_kind=variant_kind,
        importance_algorithm="constrained_rdp_deviation_v1",
        segments=tuple(segments),
        chunks=chunks,
    )


def _explicit_importance(vertices: Sequence[TrajectoryVertex]) -> list[float | None]:
    if not vertices:
        return []
    origin_latitude = sum(vertex.latitude for vertex in vertices) / len(vertices)
    origin_longitude = sum(vertex.longitude for vertex in vertices) / len(vertices)
    coordinates = [
        _project_meters(
            vertex.latitude,
            vertex.longitude,
            origin_latitude,
            origin_longitude,
        )
        for vertex in vertices
    ]
    importance: list[float | None] = [0.0] * len(vertices)
    anchors = sorted(
        {
            0,
            len(vertices) - 1,
            *(index for index, vertex in enumerate(vertices) if vertex.is_semantic_anchor),
        }
    )
    for anchor in anchors:
        importance[anchor] = None
    for start, end in zip(anchors, anchors[1:]):
        _assign_interval_importance(coordinates, start, end, importance)
    return importance


def build_hybrid_trajectory_representation(
    base: TrajectoryRepresentation,
    replacements: Sequence[
        tuple[tuple[int, ...], tuple[TrajectoryVertex, ...]]
    ],
    *,
    config: TrajectoryConfig = TrajectoryConfig(),
) -> TrajectoryRepresentation:
    """Replace accepted source-point intervals with routed geometry.

    The result keeps the original continuity segments. Unmatched intervals stay
    on the base (normally smoothed GPS) representation, so a road matcher can
    never invent a connection across an observation gap.
    """

    if base.variant_kind not in ("cleaned_gps", "smoothed_gps"):
        raise ValueError("hybrid map matching requires a GPS base representation")
    replacement_items = [item for item in replacements if item[0] and item[1]]
    segments: list[TrajectorySegment] = []
    sequence_number = 0
    for base_segment in base.segments:
        base_vertices = list(base_segment.vertices)
        position_by_point = {
            vertex.point_index: position
            for position, vertex in enumerate(base_vertices)
            if vertex.point_index is not None
        }
        applicable = []
        for point_indices, vertices in replacement_items:
            if point_indices[0] in position_by_point and point_indices[-1] in position_by_point:
                applicable.append(
                    (
                        position_by_point[point_indices[0]],
                        position_by_point[point_indices[-1]],
                        vertices,
                    )
                )
        applicable.sort(key=lambda item: (item[0], item[1]))
        assembled: list[TrajectoryVertex] = []

        def append(vertex: TrajectoryVertex) -> None:
            if assembled and vertex.recorded_at < assembled[-1].recorded_at:
                raise ValueError("map-matched vertices must preserve observation order")
            if assembled and vertex.recorded_at == assembled[-1].recorded_at:
                if assembled[-1].point_index is None or vertex.point_index is not None:
                    assembled[-1] = vertex
                return
            assembled.append(vertex)

        cursor = 0
        for start, end, routed_vertices in applicable:
            if end < cursor:
                continue
            for vertex in base_vertices[cursor:max(cursor, start)]:
                append(vertex)
            anchor_by_point = {
                vertex.point_index: vertex.anchor_reasons
                for vertex in base_vertices[start : end + 1]
                if vertex.point_index is not None
            }
            for vertex in routed_vertices:
                if assembled and vertex.recorded_at < assembled[-1].recorded_at:
                    # Consecutive matcher chunks deliberately overlap. Geometry
                    # already emitted by the previous accepted chunk wins.
                    continue
                append(
                    TrajectoryVertex(
                        sequence_number=None,
                        point_index=vertex.point_index,
                        recorded_at=vertex.recorded_at,
                        latitude=vertex.latitude,
                        longitude=vertex.longitude,
                        importance_meters=0.0,
                        is_interpolated=vertex.is_interpolated,
                        anchor_reasons=anchor_by_point.get(vertex.point_index, ()),
                    )
                )
            cursor = max(cursor, end + 1)
        for vertex in base_vertices[cursor:]:
            append(vertex)
        importance = _explicit_importance(assembled)
        stored_vertices = tuple(
            TrajectoryVertex(
                sequence_number=sequence_number + position,
                point_index=vertex.point_index,
                recorded_at=vertex.recorded_at,
                latitude=vertex.latitude,
                longitude=vertex.longitude,
                importance_meters=importance[position],
                is_interpolated=vertex.is_interpolated,
                anchor_reasons=vertex.anchor_reasons,
            )
            for position, vertex in enumerate(assembled)
        )
        sequence_number += len(stored_vertices)
        segments.append(
            TrajectorySegment(
                segment_index=base_segment.segment_index,
                vertices=stored_vertices,
                started_at=stored_vertices[0].recorded_at,
                ended_at=stored_vertices[-1].recorded_at,
            )
        )
    built_segments = tuple(segments)
    return TrajectoryRepresentation(
        variant_kind="map_matched",
        importance_algorithm="constrained_rdp_deviation_v1",
        segments=built_segments,
        chunks=_chunks_from_segments(built_segments, config),
    )


def _interpolate_vertex(
    left: TrajectoryVertex,
    right: TrajectoryVertex,
    recorded_at: datetime,
    reason: str,
) -> TrajectoryVertex:
    if recorded_at == left.recorded_at:
        return left
    if recorded_at == right.recorded_at:
        return right
    elapsed = (right.recorded_at - left.recorded_at).total_seconds()
    fraction = (recorded_at - left.recorded_at).total_seconds() / elapsed
    return TrajectoryVertex(
        sequence_number=None,
        point_index=None,
        recorded_at=recorded_at,
        latitude=left.latitude + (right.latitude - left.latitude) * fraction,
        longitude=left.longitude + (right.longitude - left.longitude) * fraction,
        importance_meters=None,
        is_interpolated=True,
        anchor_reasons=(reason,),
    )


def _append_distinct(vertices: list[TrajectoryVertex], vertex: TrajectoryVertex) -> None:
    if vertices and vertices[-1].recorded_at == vertex.recorded_at:
        if vertex.is_interpolated and not vertices[-1].is_interpolated:
            return
        vertices[-1] = vertex
        return
    vertices.append(vertex)


def _clip_vertices(
    vertices: Sequence[TrajectoryVertex],
    selected_range: TimeRange,
) -> tuple[TrajectoryVertex, ...]:
    if not vertices:
        return ()
    if len(vertices) == 1:
        vertex = vertices[0]
        return (vertex,) if selected_range.start <= vertex.recorded_at < selected_range.end else ()
    clipped: list[TrajectoryVertex] = []
    for left, right in zip(vertices, vertices[1:]):
        if right.recorded_at < selected_range.start or left.recorded_at >= selected_range.end:
            continue
        intersection_start = max(left.recorded_at, selected_range.start)
        intersection_end = min(right.recorded_at, selected_range.end)
        if intersection_start > intersection_end:
            continue
        _append_distinct(
            clipped,
            _interpolate_vertex(left, right, intersection_start, "query_range_start"),
        )
        _append_distinct(
            clipped,
            _interpolate_vertex(left, right, intersection_end, "query_range_end"),
        )
    return tuple(clipped)


def _deduplicate_chunk_vertices(
    chunks: Sequence[TrajectoryChunk],
) -> tuple[TrajectoryVertex, ...]:
    by_sequence: dict[int, TrajectoryVertex] = {}
    for chunk in chunks:
        for vertex in chunk.vertices:
            if vertex.sequence_number is not None:
                by_sequence[vertex.sequence_number] = vertex
    return tuple(by_sequence[index] for index in sorted(by_sequence))


def _budget_selection(
    paths: Sequence[tuple[TrajectoryVertex, ...]],
    vertex_budget: int | None,
    pixel_tolerance_meters: float,
) -> tuple[float, set[tuple[int, int]] | None]:
    if vertex_budget is None:
        return pixel_tolerance_meters, None
    if vertex_budget < 1:
        raise ValueError("vertex_budget must be positive")
    mandatory = 0
    optional_vertices: list[tuple[float, int, int, int]] = []
    for path_index, path in enumerate(paths):
        for position, vertex in enumerate(path):
            is_mandatory = (
                position in (0, len(path) - 1)
                or vertex.is_semantic_anchor
                or vertex.importance_meters is None
            )
            if is_mandatory:
                mandatory += 1
            elif (vertex.importance_meters or 0.0) >= pixel_tolerance_meters:
                optional_vertices.append(
                    (
                        vertex.importance_meters or 0.0,
                        -(vertex.sequence_number or 0),
                        path_index,
                        position,
                    )
                )
    allowed_optional = vertex_budget - mandatory
    if allowed_optional <= 0:
        threshold = (
            math.nextafter(optional_vertices[0][0], math.inf)
            if optional_vertices
            else pixel_tolerance_meters
        )
        return threshold, set()
    optional_vertices.sort(reverse=True)
    selected = optional_vertices[:allowed_optional]
    selected_positions = {(item[2], item[3]) for item in selected}
    threshold = (
        max(pixel_tolerance_meters, selected[-1][0])
        if selected
        else pixel_tolerance_meters
    )
    return threshold, selected_positions


def _apply_importance_threshold(
    vertices: Sequence[TrajectoryVertex],
    threshold: float,
    *,
    path_index: int,
    budget_selected_positions: set[tuple[int, int]] | None,
) -> tuple[TrajectoryVertex, ...]:
    if len(vertices) <= 2:
        return tuple(vertices)
    result: list[TrajectoryVertex] = []
    for position, vertex in enumerate(vertices):
        mandatory = (
            position in (0, len(vertices) - 1)
            or vertex.is_semantic_anchor
            or vertex.importance_meters is None
        )
        if mandatory:
            result.append(vertex)
        elif budget_selected_positions is not None:
            if (path_index, position) in budget_selected_positions:
                result.append(vertex)
        elif vertex.importance_meters >= threshold:
            result.append(vertex)
    return tuple(result)


def query_trajectory(
    representation: TrajectoryRepresentation,
    ranges: Sequence[TimeRange],
    *,
    pixel_tolerance_meters: float = 0.0,
    vertex_budget: int | None = None,
) -> tuple[VisiblePath, ...]:
    if pixel_tolerance_meters < 0:
        raise ValueError("pixel_tolerance_meters cannot be negative")
    normalized_ranges = normalize_time_ranges(ranges)
    raw_paths: list[
        tuple[int, int, TimeRange, tuple[int, ...], tuple[TrajectoryVertex, ...]]
    ] = []
    for range_index, selected_range in enumerate(normalized_ranges):
        candidate_chunks = [
            chunk
            for chunk in representation.chunks
            if chunk.started_at < selected_range.end
            and chunk.ended_at >= selected_range.start
        ]
        segment_indices = sorted({chunk.segment_index for chunk in candidate_chunks})
        for segment_index in segment_indices:
            chunks = sorted(
                (
                    chunk
                    for chunk in candidate_chunks
                    if chunk.segment_index == segment_index
                ),
                key=lambda chunk: chunk.chunk_number,
            )
            clipped = _clip_vertices(
                _deduplicate_chunk_vertices(chunks),
                selected_range,
            )
            if clipped:
                raw_paths.append(
                    (
                        range_index,
                        segment_index,
                        selected_range,
                        tuple(chunk.chunk_number for chunk in chunks),
                        clipped,
                    )
                )
    threshold, budget_selected_positions = _budget_selection(
        tuple(path[4] for path in raw_paths),
        vertex_budget,
        pixel_tolerance_meters,
    )
    return tuple(
        VisiblePath(
            selection_range_index=range_index,
            segment_index=segment_index,
            selected_range=selected_range,
            vertices=_apply_importance_threshold(
                vertices,
                threshold,
                path_index=path_index,
                budget_selected_positions=budget_selected_positions,
            ),
            source_chunk_numbers=chunk_numbers,
            applied_importance_threshold_meters=threshold,
        )
        for path_index, (
            range_index,
            segment_index,
            selected_range,
            chunk_numbers,
            vertices,
        ) in enumerate(raw_paths)
    )
