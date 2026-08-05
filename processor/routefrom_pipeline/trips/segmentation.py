from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Sequence

from routefrom_pipeline.continuity.gaps import InferredConnection, ObservationGap
from routefrom_pipeline.continuity.graph import ContinuityResult
from routefrom_pipeline.motion import MotionResult, MotionState
from routefrom_pipeline.quality import LocatedObservation, haversine_meters
from routefrom_pipeline.stays import StayResult, StationaryEvent, StationaryEventType


class TripBoundaryState(StrEnum):
    CLOSED = "closed"
    OPEN_START = "open_start"
    OPEN_END = "open_end"
    OPEN_BOTH = "open_both"


@dataclass(frozen=True, slots=True)
class TripConfig:
    confirmed_visit_probability_min: float = 0.55
    confirmed_visit_confidence_min: float = 0.45
    moving_point_probability_required: bool = True


@dataclass(frozen=True, slots=True)
class TripTrackSegment:
    point_indices: tuple[int, ...]
    started_at: datetime
    ended_at: datetime
    confirmed_duration_seconds: float
    confirmed_distance_meters: float


@dataclass(frozen=True, slots=True)
class Trip:
    sequence_number: int
    boundary_state: TripBoundaryState
    start_visit_event_index: int | None
    end_visit_event_index: int | None
    point_indices: tuple[int, ...]
    track_segments: tuple[TripTrackSegment, ...]
    gap_indices: tuple[int, ...]
    inferred_connection_indices: tuple[int, ...]
    transport_pause_event_indices: tuple[int, ...]
    started_at: datetime
    ended_at: datetime
    elapsed_seconds: float
    confirmed_duration_seconds: float
    unknown_duration_seconds: float
    confirmed_distance_meters: float
    inferred_distance_meters: float
    confidence: float
    evidence: dict[str, float | int | str]


@dataclass(frozen=True, slots=True)
class TripResult:
    confirmed_visit_event_indices: tuple[int, ...]
    trips: tuple[Trip, ...]


def _is_confirmed_visit(event: StationaryEvent, config: TripConfig) -> bool:
    return (
        event.event_type == StationaryEventType.VISIT
        and event.visit_probability >= config.confirmed_visit_probability_min
        and event.confidence >= config.confirmed_visit_confidence_min
    )


def _boundary_state(
    start_event_index: int | None,
    end_event_index: int | None,
) -> TripBoundaryState:
    if start_event_index is not None and end_event_index is not None:
        return TripBoundaryState.CLOSED
    if start_event_index is None and end_event_index is not None:
        return TripBoundaryState.OPEN_START
    if start_event_index is not None:
        return TripBoundaryState.OPEN_END
    return TripBoundaryState.OPEN_BOTH


def _window_bounds(
    point_count: int,
    events: Sequence[StationaryEvent],
    start_event_index: int | None,
    end_event_index: int | None,
) -> tuple[int, int]:
    lower = (
        events[start_event_index].point_indices[-1]
        if start_event_index is not None
        else 0
    )
    upper = (
        events[end_event_index].point_indices[0]
        if end_event_index is not None
        else point_count - 1
    )
    return lower, upper


def _has_movement(
    indices: Sequence[int],
    motion: MotionResult,
    config: TripConfig,
) -> bool:
    states = [motion.state_by_point[index] for index in indices]
    if MotionState.MOVING in states:
        return True
    if not config.moving_point_probability_required and MotionState.UNCERTAIN in states:
        return True
    return False


def _track_segments(
    points: Sequence[LocatedObservation],
    continuity: ContinuityResult,
    included_indices: set[int],
) -> tuple[TripTrackSegment, ...]:
    result: list[TripTrackSegment] = []
    for continuity_segment in continuity.segments:
        indices = tuple(index for index in continuity_segment if index in included_indices)
        if not indices:
            continue
        distance = sum(
            haversine_meters(
                points[previous].wgs_latitude,
                points[previous].wgs_longitude,
                points[current].wgs_latitude,
                points[current].wgs_longitude,
            )
            for previous, current in zip(indices, indices[1:])
        )
        duration = sum(
            max(
                0.0,
                (points[current].recorded_at - points[previous].recorded_at).total_seconds(),
            )
            for previous, current in zip(indices, indices[1:])
        )
        result.append(
            TripTrackSegment(
                point_indices=indices,
                started_at=points[indices[0]].recorded_at,
                ended_at=points[indices[-1]].recorded_at,
                confirmed_duration_seconds=duration,
                confirmed_distance_meters=distance,
            )
        )
    return tuple(result)


def _trip_confidence(
    events: Sequence[StationaryEvent],
    start_event_index: int | None,
    end_event_index: int | None,
    moving_probability_by_index: dict[int, float],
    moving_indices: Sequence[int],
    gap_count: int,
) -> float:
    boundary_confidences: list[float] = []
    if start_event_index is not None:
        boundary_confidences.append(events[start_event_index].departure_confidence)
    if end_event_index is not None:
        boundary_confidences.append(events[end_event_index].arrival_confidence)
    boundary_confidence = (
        sum(boundary_confidences) / len(boundary_confidences)
        if boundary_confidences
        else 0.45
    )
    moving_confidences = [
        moving_probability_by_index.get(index, 0.0)
        for index in moving_indices
    ]
    movement_confidence = (
        sum(moving_confidences) / len(moving_confidences)
        if moving_confidences
        else 0.0
    )
    gap_factor = 1.0 / (1.0 + gap_count * 0.15)
    return max(
        0.0,
        min(1.0, (0.55 * boundary_confidence + 0.45 * movement_confidence) * gap_factor),
    )


def segment_trips(
    points: Sequence[LocatedObservation],
    continuity: ContinuityResult,
    motion: MotionResult,
    stays: StayResult,
    gaps: Sequence[ObservationGap],
    inferred_connections: Sequence[InferredConnection],
    *,
    config: TripConfig = TripConfig(),
) -> TripResult:
    if not points:
        return TripResult((), ())
    events = stays.events
    confirmed_visits = tuple(
        index for index, event in enumerate(events) if _is_confirmed_visit(event, config)
    )
    windows: list[tuple[int | None, int | None]] = []
    if confirmed_visits:
        windows.append((None, confirmed_visits[0]))
        windows.extend(zip(confirmed_visits, confirmed_visits[1:]))
        windows.append((confirmed_visits[-1], None))
    else:
        windows.append((None, None))

    selected_indices = set(continuity.selected_point_indices)
    moving_probability_by_index = {
        evidence.point_index: evidence.moving_probability for evidence in motion.evidence
    }
    trips: list[Trip] = []
    for start_event_index, end_event_index in windows:
        lower, upper = _window_bounds(
            len(points), events, start_event_index, end_event_index
        )
        if upper <= lower:
            continue
        included = tuple(
            index for index in sorted(selected_indices) if lower <= index <= upper
        )
        movement_indices = tuple(
            index
            for index in included
            if motion.state_by_point[index] == MotionState.MOVING
        )
        if not _has_movement(included, motion, config):
            continue
        track_segments = _track_segments(points, continuity, set(included))
        if not track_segments:
            continue
        gap_indices = tuple(
            index
            for index, gap in enumerate(gaps)
            if lower <= gap.before_point_index and gap.after_point_index <= upper
        )
        connection_indices = tuple(
            index
            for index, connection in enumerate(inferred_connections)
            if lower <= connection.before_point_index
            and connection.after_point_index <= upper
        )
        pause_indices = tuple(
            index
            for index, event in enumerate(events)
            if event.event_type == StationaryEventType.TRANSPORT_PAUSE
            and lower < event.point_indices[0]
            and event.point_indices[-1] < upper
        )
        confirmed_duration = sum(
            segment.confirmed_duration_seconds for segment in track_segments
        )
        confirmed_distance = sum(
            segment.confirmed_distance_meters for segment in track_segments
        )
        unknown_duration = sum(gaps[index].elapsed_seconds for index in gap_indices)
        inferred_distance = sum(
            inferred_connections[index].estimated_path_distance_meters
            for index in connection_indices
            if inferred_connections[index].displayable
        )
        started_at = points[included[0]].recorded_at
        ended_at = points[included[-1]].recorded_at
        confidence = _trip_confidence(
            events,
            start_event_index,
            end_event_index,
            moving_probability_by_index,
            movement_indices,
            len(gap_indices),
        )
        trips.append(
            Trip(
                sequence_number=len(trips),
                boundary_state=_boundary_state(start_event_index, end_event_index),
                start_visit_event_index=start_event_index,
                end_visit_event_index=end_event_index,
                point_indices=included,
                track_segments=track_segments,
                gap_indices=gap_indices,
                inferred_connection_indices=connection_indices,
                transport_pause_event_indices=pause_indices,
                started_at=started_at,
                ended_at=ended_at,
                elapsed_seconds=(ended_at - started_at).total_seconds(),
                confirmed_duration_seconds=confirmed_duration,
                unknown_duration_seconds=unknown_duration,
                confirmed_distance_meters=confirmed_distance,
                inferred_distance_meters=inferred_distance,
                confidence=confidence,
                evidence={
                    "boundary_rule": "confirmed_visits_only",
                    "moving_point_count": len(movement_indices),
                    "track_segment_count": len(track_segments),
                    "gap_count": len(gap_indices),
                    "transport_pause_count": len(pause_indices),
                },
            )
        )
    return TripResult(
        confirmed_visit_event_indices=confirmed_visits,
        trips=tuple(trips),
    )
