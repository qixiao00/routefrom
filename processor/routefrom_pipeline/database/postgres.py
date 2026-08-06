from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Protocol
from uuid import UUID, uuid4, uuid5

from routefrom_pipeline.pipeline import ProcessedTrace
from routefrom_pipeline.quality import haversine_meters
from routefrom_pipeline.trajectory import TrajectoryRepresentation, TrajectoryVertex

_STAGES = (
    "quality",
    "continuity",
    "motion",
    "stays",
    "trips",
    "places",
    "modes",
    "map_matching",
    "trajectory",
)
_LOGICAL_NAMESPACE = UUID("f42be854-2b56-4ec7-89eb-a232164257c1")


class Cursor(Protocol):
    def execute(self, query: str, params: Sequence[object] = ()) -> Any: ...
    def executemany(self, query: str, params_seq: Iterable[Sequence[object]]) -> Any: ...
    def fetchall(self) -> Sequence[Sequence[object]]: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def transaction(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    processing_run_id: UUID
    stage_run_ids: Mapping[str, UUID]
    row_counts: Mapping[str, int]


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _nonempty_end(start: datetime, end: datetime) -> datetime:
    return end if end > start else start + timedelta(microseconds=1)


def _logical_id(dataset_id: UUID, entity_type: str, source_rows: Sequence[int]) -> UUID:
    encoded_rows = ",".join(str(row) for row in source_rows).encode("ascii")
    fingerprint = hashlib.sha256(encoded_rows).hexdigest()
    boundary = (
        f"{source_rows[0]}:{source_rows[-1]}:{len(source_rows)}:{fingerprint}"
        if source_rows
        else f"empty:{fingerprint}"
    )
    return uuid5(_LOGICAL_NAMESPACE, f"{dataset_id}:{entity_type}:{boundary}")


def _entity_id(run_id: UUID, table: str, logical_id: UUID | str) -> UUID:
    return uuid5(run_id, f"{table}:{logical_id}")


def _point_wkt(longitude: float, latitude: float) -> str:
    return f"POINT({longitude:.12g} {latitude:.12g})"


def _line_wkt(trace: ProcessedTrace, point_indices: Sequence[int]) -> str | None:
    if len(point_indices) < 2:
        return None
    coordinates = ",".join(
        f"{trace.points[index].wgs_longitude:.12g} {trace.points[index].wgs_latitude:.12g}"
        for index in point_indices
    )
    return f"LINESTRING({coordinates})"


def _execute_many(
    cursor: Cursor,
    row_counts: dict[str, int],
    name: str,
    query: str,
    rows: Sequence[Sequence[object]],
) -> None:
    if not rows:
        row_counts.setdefault(name, 0)
        return
    cursor.executemany(query, rows)
    row_counts[name] = row_counts.get(name, 0) + len(rows)


def _load_point_ids(
    cursor: Cursor,
    dataset_id: UUID,
    dataset_import_id: UUID,
    trace: ProcessedTrace,
) -> tuple[int, ...]:
    cursor.execute(
        """
        SELECT source_row_number, id
        FROM app.location_points
        WHERE dataset_id = %s AND dataset_import_id = %s
        ORDER BY source_row_number
        """,
        (dataset_id, dataset_import_id),
    )
    point_id_by_source_row = {int(row[0]): int(row[1]) for row in cursor.fetchall()}
    missing = [
        point.source_row_number
        for point in trace.points
        if point.source_row_number not in point_id_by_source_row
    ]
    if missing:
        preview = ", ".join(str(row) for row in missing[:5])
        raise ValueError(f"database is missing imported source rows: {preview}")
    return tuple(point_id_by_source_row[point.source_row_number] for point in trace.points)


def persist_processed_trace(
    connection: Connection,
    *,
    dataset_id: UUID,
    dataset_import_id: UUID,
    input_sha256: str,
    trace: ProcessedTrace,
    parameters: Mapping[str, object] | None = None,
    code_revision: str | None = None,
    activate: bool = True,
) -> PersistenceResult:
    """Persist one complete algorithm run and optionally activate it atomically.

    Source observations must already exist in ``app.location_points``. Every
    derived table is written inside the caller connection's transaction. A
    failed insert or deferred integrity constraint therefore leaves the prior
    active run untouched.
    """

    if len(input_sha256) != 64 or any(character not in "0123456789abcdefABCDEF" for character in input_sha256):
        raise ValueError("input_sha256 must contain exactly 64 hexadecimal characters")
    run_id = uuid4()
    stage_ids = {stage: uuid5(run_id, f"stage:{stage}") for stage in _STAGES}
    run_parameters = dict(parameters or {})
    row_counts: dict[str, int] = {}

    with connection.transaction():
        cursor = connection.cursor()
        point_ids = _load_point_ids(cursor, dataset_id, dataset_import_id, trace)
        cursor.execute(
            """
            INSERT INTO app.processing_runs (
              id, dataset_id, dataset_import_id, algorithm, algorithm_version,
              pipeline_version, code_revision, input_sha256, parameters,
              parameters_hash, status, publication_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'running', 'candidate')
            """,
            (
                run_id,
                dataset_id,
                dataset_import_id,
                "routefrom_explainable_pipeline",
                trace.algorithm_version,
                trace.algorithm_version,
                code_revision,
                input_sha256.lower(),
                _json(run_parameters),
                _hash_json(run_parameters),
            ),
        )
        cursor.executemany(
            """
            INSERT INTO app.processing_stage_runs (
              id, processing_run_id, stage_name, sequence_number, algorithm_name,
              algorithm_version, parameters_hash, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'running')
            """,
            [
                (
                    stage_ids[stage],
                    run_id,
                    stage,
                    sequence,
                    f"routefrom_{stage}",
                    trace.algorithm_version,
                    _hash_json({"stage": stage, **run_parameters}),
                )
                for sequence, stage in enumerate(_STAGES)
            ],
        )

        _persist_quality(cursor, row_counts, run_id, stage_ids, dataset_id, point_ids, trace)
        gap_ids = _persist_continuity(
            cursor, row_counts, run_id, stage_ids, dataset_id, point_ids, trace
        )
        episode_ids = _persist_motion(
            cursor, row_counts, run_id, stage_ids, dataset_id, point_ids, trace
        )
        event_ids = _persist_stays(
            cursor, row_counts, run_id, stage_ids, dataset_id, episode_ids, trace
        )
        place_version_ids = _persist_places(
            cursor, row_counts, run_id, dataset_id, trace
        )
        visit_ids = _persist_visits(
            cursor,
            row_counts,
            run_id,
            dataset_id,
            event_ids,
            place_version_ids,
            trace,
        )
        trip_ids = _persist_trips(
            cursor, row_counts, run_id, stage_ids, dataset_id, visit_ids, trace
        )
        leg_ids = _persist_modes(
            cursor, row_counts, run_id, stage_ids, dataset_id, trip_ids, trace
        )
        _persist_connections(
            cursor, row_counts, run_id, dataset_id, gap_ids, trip_ids, trace
        )
        _persist_trajectory(
            cursor,
            row_counts,
            run_id,
            stage_ids,
            dataset_id,
            point_ids,
            trip_ids,
            leg_ids,
            trace,
        )

        metrics = {
            "row_counts": row_counts,
            "point_count": len(trace.points),
            "map_matching": (
                {
                    "preferred": trace.map_matching.preferred,
                    "confidence": trace.map_matching.confidence,
                    "moving_coverage": trace.map_matching.moving_coverage,
                }
                if trace.map_matching is not None
                else {"status": "not_configured"}
            ),
        }
        cursor.execute(
            """
            UPDATE app.processing_stage_runs
            SET status = CASE
                  WHEN id = %s AND %s THEN 'skipped'
                  ELSE 'succeeded'
                END,
                completed_at = now()
            WHERE processing_run_id = %s
            """,
            (stage_ids["map_matching"], trace.map_matching is None, run_id),
        )
        cursor.execute(
            """
            UPDATE app.processing_runs
            SET status = 'succeeded', metrics = %s::jsonb, completed_at = now()
            WHERE id = %s
            """,
            (_json(metrics), run_id),
        )
        if activate:
            cursor.execute("SELECT app.activate_processing_run(%s)", (run_id,))

    return PersistenceResult(run_id, stage_ids, row_counts)


def _persist_quality(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    point_ids: Sequence[int],
    trace: ProcessedTrace,
) -> None:
    rows = [
        (
            run_id,
            dataset_id,
            point_ids[index],
            stages["quality"],
            assessment.quality_status.value,
            assessment.anomaly_probability,
            assessment.path_weight,
            assessment.stay_weight,
            assessment.mode_weight,
            assessment.exclusion_supported,
            list(assessment.reason_codes),
            _json(trace.features[index]),
            _json({"contributions": assessment.contributions}),
        )
        for index, assessment in enumerate(trace.assessments)
    ]
    _execute_many(
        cursor,
        counts,
        "point_assessments",
        """
        INSERT INTO app.point_assessments (
          processing_run_id, dataset_id, point_id, stage_run_id, quality_status,
          anomaly_probability, path_weight, stay_weight, mode_weight,
          exclusion_supported, reason_codes, features, explanation
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
        """,
        rows,
    )


def _persist_continuity(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    point_ids: Sequence[int],
    trace: ProcessedTrace,
) -> tuple[UUID, ...]:
    edge_rows = [
        (
            run_id,
            dataset_id,
            stages["continuity"],
            point_ids[edge.from_index],
            point_ids[edge.to_index],
            edge.kind,
            edge.elapsed_seconds,
            edge.displacement_meters,
            edge.calculated_speed_mps,
            edge.continuity_probability,
            list(edge.reason_codes),
        )
        for edge in trace.continuity.edges
    ]
    _execute_many(
        cursor,
        counts,
        "observation_edges",
        """
        INSERT INTO app.observation_edges (
          processing_run_id,dataset_id,stage_run_id,from_point_id,to_point_id,
          edge_kind,elapsed_seconds,displacement_meters,calculated_speed_mps,
          continuity_probability,reason_codes
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        edge_rows,
    )
    gap_ids: list[UUID] = []
    gap_rows: list[Sequence[object]] = []
    for gap in trace.observation_gaps:
        source_rows = (
            trace.points[gap.before_point_index].source_row_number,
            trace.points[gap.after_point_index].source_row_number,
        )
        logical_id = _logical_id(dataset_id, "observation_gap", source_rows)
        gap_id = _entity_id(run_id, "observation_gaps", logical_id)
        gap_ids.append(gap_id)
        gap_rows.append(
            (
                gap_id,
                logical_id,
                run_id,
                dataset_id,
                stages["continuity"],
                point_ids[gap.before_point_index],
                point_ids[gap.after_point_index],
                gap.started_at,
                _nonempty_end(gap.started_at, gap.ended_at),
                gap.cause.value,
                gap.context_before.value if gap.context_before else None,
                gap.context_after.value if gap.context_after else None,
                gap.same_place_probability,
                gap.confidence,
                _json(
                    {
                        "elapsed_seconds": gap.elapsed_seconds,
                        "displacement_meters": gap.displacement_meters,
                        "reason_codes": gap.reason_codes,
                    }
                ),
            )
        )
    _execute_many(
        cursor,
        counts,
        "observation_gaps",
        """
        INSERT INTO app.observation_gaps (
          id,logical_id,processing_run_id,dataset_id,stage_run_id,before_point_id,
          after_point_id,gap_range,cause,context_before,context_after,
          same_place_probability,confidence,evidence
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,tstzrange(%s,%s,'[)'),%s,%s,%s,%s,%s,%s::jsonb)
        """,
        gap_rows,
    )
    return tuple(gap_ids)


def _persist_motion(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    point_ids: Sequence[int],
    trace: ProcessedTrace,
) -> tuple[UUID, ...]:
    evidence_by_index = {evidence.point_index: evidence for evidence in trace.motion.evidence}
    episode_ids: list[UUID] = []
    episode_rows: list[Sequence[object]] = []
    point_rows: list[Sequence[object]] = []
    for sequence, episode in enumerate(trace.motion.episodes):
        source_rows = tuple(trace.points[index].source_row_number for index in episode.point_indices)
        logical_id = _logical_id(dataset_id, "motion_episode", source_rows)
        episode_id = _entity_id(run_id, "motion_episodes", logical_id)
        episode_ids.append(episode_id)
        episode_rows.append(
            (
                episode_id,
                logical_id,
                run_id,
                dataset_id,
                stages["motion"],
                sequence,
                episode.state.value,
                episode.observed_started_at,
                _nonempty_end(episode.observed_started_at, episode.observed_ended_at),
                episode.confidence,
                episode.start_boundary_confidence,
                episode.end_boundary_confidence,
                _json({"observed_duration_seconds": episode.observed_duration_seconds}),
            )
        )
        for point_sequence, point_index in enumerate(episode.point_indices):
            evidence = evidence_by_index[point_index]
            point_rows.append(
                (
                    run_id,
                    episode_id,
                    point_ids[point_index],
                    point_sequence,
                    evidence.stationary_probability,
                    evidence.moving_probability,
                    evidence.uncertain_probability,
                )
            )
    _execute_many(
        cursor,
        counts,
        "motion_episodes",
        """
        INSERT INTO app.motion_episodes (
          id,logical_id,processing_run_id,dataset_id,stage_run_id,sequence_number,
          motion_state,observed_range,confidence,start_boundary_confidence,
          end_boundary_confidence,evidence
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,tstzrange(%s,%s,'[)'),%s,%s,%s,%s::jsonb)
        """,
        episode_rows,
    )
    _execute_many(
        cursor,
        counts,
        "motion_episode_points",
        """
        INSERT INTO app.motion_episode_points (
          processing_run_id,motion_episode_id,point_id,sequence_number,
          stationary_probability,moving_probability,uncertain_probability
        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
        point_rows,
    )
    return tuple(episode_ids)


def _persist_stays(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    episode_ids: Sequence[UUID],
    trace: ProcessedTrace,
) -> tuple[UUID, ...]:
    episode_id_by_points = {
        episode.point_indices: episode_ids[index] for index, episode in enumerate(trace.motion.episodes)
    }
    event_ids: list[UUID] = []
    event_rows: list[Sequence[object]] = []
    for event_index, event in enumerate(trace.stays.events):
        source_rows = tuple(trace.points[index].source_row_number for index in event.point_indices)
        logical_id = _logical_id(dataset_id, "stationary_event", source_rows)
        event_id = _entity_id(run_id, "stationary_events", logical_id)
        event_ids.append(event_id)
        event_rows.append(
            (
                event_id,
                logical_id,
                run_id,
                dataset_id,
                stages["stays"],
                episode_id_by_points[event.point_indices],
                event.observed_started_at,
                _nonempty_end(event.observed_started_at, event.observed_ended_at),
                event.possible_started_at,
                _nonempty_end(event.possible_started_at, event.possible_ended_at),
                _point_wkt(event.centroid_longitude, event.centroid_latitude),
                event.spatial_radius_meters,
                event.adaptive_spatial_scale_meters,
                len(event.point_indices),
                event.effective_point_count,
                event.event_type.value,
                event.open_start,
                event.open_end,
                event.confidence,
                event.visit_probability,
                event.transport_pause_probability,
                event.uncertain_stop_probability,
                _json(
                    {
                        **event.evidence,
                        "arrival_confidence": event.arrival_confidence,
                        "departure_confidence": event.departure_confidence,
                    }
                ),
            )
        )
    _execute_many(
        cursor,
        counts,
        "stationary_events",
        """
        INSERT INTO app.stationary_events (
          id,logical_id,processing_run_id,dataset_id,stage_run_id,motion_episode_id,
          observed_range,possible_range,centroid,spatial_radius_meters,
          adaptive_spatial_scale_meters,point_count,effective_point_count,event_type,
          open_start,open_end,confidence,visit_probability,transport_pause_probability,
          uncertain_stop_probability,evidence
        ) VALUES (
          %s,%s,%s,%s,%s,%s,tstzrange(%s,%s,'[)'),tstzrange(%s,%s,'[)'),
          ST_GeomFromText(%s,4326),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
        )
        """,
        event_rows,
    )
    return tuple(event_ids)


def _persist_places(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    dataset_id: UUID,
    trace: ProcessedTrace,
) -> dict[int, UUID]:
    place_ids: list[UUID] = []
    place_version_ids: list[UUID] = []
    identity_rows: list[Sequence[object]] = []
    version_rows: list[Sequence[object]] = []
    for place_index, place in enumerate(trace.places.places):
        anchor_event = trace.stays.events[place.anchor_event_index]
        anchor_source_rows = tuple(
            trace.points[index].source_row_number for index in anchor_event.point_indices
        )
        place_id = _logical_id(dataset_id, "place", anchor_source_rows)
        place_version_id = _entity_id(run_id, "place_versions", place_id)
        place_ids.append(place_id)
        place_version_ids.append(place_version_id)
        centroid = _point_wkt(place.centroid_longitude, place.centroid_latitude)
        identity_rows.append((place_id, centroid, dataset_id))
        version_rows.append(
            (
                place_version_id,
                place_id,
                run_id,
                centroid,
                place.timezone,
                place.confidence,
                _json(
                    {
                        **place.evidence,
                        "adaptive_spatial_scale_meters": (
                            place.adaptive_spatial_scale_meters
                        ),
                        "anchor_event_index": place.anchor_event_index,
                        "distinct_local_date_count": place.distinct_local_date_count,
                        "first_visited_at": place.first_visited_at,
                        "frequent_probability": place.frequent_probability,
                        "last_visited_at": place.last_visited_at,
                        "observed_duration_seconds": place.observed_duration_seconds,
                        "spatial_radius_meters": place.spatial_radius_meters,
                        "visit_count": place.visit_count,
                        "visit_event_indices": place.visit_event_indices,
                    }
                ),
            )
        )
    _execute_many(
        cursor,
        counts,
        "places",
        """
        INSERT INTO app.places (id,user_id,centroid)
        SELECT %s,dataset.user_id,ST_GeomFromText(%s,4326)
        FROM app.datasets AS dataset
        WHERE dataset.id = %s
        ON CONFLICT (id) DO NOTHING
        """,
        identity_rows,
    )
    _execute_many(
        cursor,
        counts,
        "place_versions",
        """
        INSERT INTO app.place_versions (
          id,place_id,processing_run_id,centroid,timezone,source_kind,confidence,evidence
        ) VALUES (
          %s,%s,%s,ST_GeomFromText(%s,4326),%s,'trajectory_cluster',%s,%s::jsonb
        )
        """,
        version_rows,
    )
    version_by_event: dict[int, UUID] = {}
    for binding in trace.places.bindings:
        if binding.place_index is not None:
            version_by_event[binding.stationary_event_index] = place_version_ids[
                binding.place_index
            ]
    return version_by_event


def _persist_visits(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    dataset_id: UUID,
    event_ids: Sequence[UUID],
    place_version_ids: Mapping[int, UUID],
    trace: ProcessedTrace,
) -> dict[int, UUID]:
    binding_by_event = {
        binding.stationary_event_index: binding for binding in trace.places.bindings
    }
    visit_ids: dict[int, UUID] = {}
    visit_rows: list[Sequence[object]] = []
    for event_index in trace.trips.confirmed_visit_event_indices:
        event = trace.stays.events[event_index]
        source_rows = tuple(
            trace.points[index].source_row_number for index in event.point_indices
        )
        visit_logical_id = _logical_id(dataset_id, "visit", source_rows)
        visit_id = _entity_id(run_id, "visits", visit_logical_id)
        visit_ids[event_index] = visit_id
        binding = binding_by_event.get(event_index)
        visit_rows.append(
            (
                visit_id,
                visit_logical_id,
                run_id,
                dataset_id,
                event_ids[event_index],
                place_version_ids.get(event_index),
                event.arrival_confidence,
                event.departure_confidence,
                event.confidence,
                _json(
                    {
                        "place_binding": binding,
                        "visit_probability": event.visit_probability,
                    }
                ),
            )
        )
    _execute_many(
        cursor,
        counts,
        "visits",
        """
        INSERT INTO app.visits (
          id,logical_id,processing_run_id,dataset_id,stationary_event_id,
          place_version_id,status,arrival_confidence,departure_confidence,
          overall_confidence,evidence
        ) VALUES (%s,%s,%s,%s,%s,%s,'confirmed',%s,%s,%s,%s::jsonb)
        """,
        visit_rows,
    )
    return visit_ids


def _persist_trips(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    visit_ids: Mapping[int, UUID],
    trace: ProcessedTrace,
) -> tuple[UUID, ...]:
    trip_ids: list[UUID] = []
    rows: list[Sequence[object]] = []
    for trip in trace.trips.trips:
        source_rows = tuple(trace.points[index].source_row_number for index in trip.point_indices)
        logical_id = _logical_id(dataset_id, "trip", source_rows)
        trip_id = _entity_id(run_id, "trips", logical_id)
        trip_ids.append(trip_id)
        rows.append(
            (
                trip_id,
                logical_id,
                run_id,
                dataset_id,
                stages["trips"],
                trip.sequence_number,
                trip.boundary_state.value,
                visit_ids.get(trip.start_visit_event_index),
                visit_ids.get(trip.end_visit_event_index),
                trip.started_at,
                _nonempty_end(trip.started_at, trip.ended_at),
                trip.confirmed_duration_seconds,
                trip.unknown_duration_seconds,
                trip.confirmed_distance_meters,
                trip.confidence,
                _json(
                    {
                        **trip.evidence,
                        "inferred_distance_meters": trip.inferred_distance_meters,
                        "gap_indices": trip.gap_indices,
                    }
                ),
            )
        )
    _execute_many(
        cursor,
        counts,
        "trips",
        """
        INSERT INTO app.trips (
          id,logical_id,processing_run_id,dataset_id,stage_run_id,sequence_number,
          boundary_state,start_visit_id,end_visit_id,trip_range,
          confirmed_duration_seconds,unknown_duration_seconds,confirmed_distance_meters,
          confidence,evidence
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,tstzrange(%s,%s,'[)'),%s,%s,%s,%s,%s::jsonb)
        """,
        rows,
    )
    return tuple(trip_ids)


def _persist_modes(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    trip_ids: Sequence[UUID],
    trace: ProcessedTrace,
) -> tuple[UUID, ...]:
    leg_ids: list[UUID] = []
    leg_rows: list[Sequence[object]] = []
    score_rows: list[Sequence[object]] = []
    for leg_index, leg in enumerate(trace.modes.legs):
        source_rows = tuple(trace.points[index].source_row_number for index in leg.point_indices)
        logical_id = _logical_id(dataset_id, "mobility_leg", source_rows)
        leg_id = _entity_id(run_id, "mobility_legs", logical_id)
        leg_ids.append(leg_id)
        start = trace.points[leg.point_indices[0]]
        end = trace.points[leg.point_indices[-1]]
        leg_rows.append(
            (
                leg_id,
                logical_id,
                run_id,
                dataset_id,
                stages["modes"],
                trip_ids[leg.trip_index],
                leg.sequence_number,
                leg.observed_started_at,
                _nonempty_end(leg.observed_started_at, leg.observed_ended_at),
                _point_wkt(start.wgs_longitude, start.wgs_latitude),
                _point_wkt(end.wgs_longitude, end.wgs_latitude),
                leg.selected_mode_code,
                leg.selected_mode_level,
                leg.confidence,
                trace.modes.feature_schema_version,
                _json(leg.features),
                _json(leg.evidence),
            )
        )
        score_rows.extend(
            (
                leg_id,
                run_id,
                score.mode_code,
                score.probability,
                _json(score.contributions),
            )
            for score in leg.mode_scores
        )
    _execute_many(
        cursor,
        counts,
        "mobility_legs",
        """
        INSERT INTO app.mobility_legs (
          id,logical_id,processing_run_id,dataset_id,stage_run_id,trip_id,
          sequence_number,observed_range,start_position,end_position,selected_mode_code,
          selected_mode_level,confidence,feature_schema_version,features,evidence
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,tstzrange(%s,%s,'[)'),ST_GeomFromText(%s,4326),
          ST_GeomFromText(%s,4326),%s,%s,%s,%s,%s::jsonb,%s::jsonb
        )
        """,
        leg_rows,
    )
    _execute_many(
        cursor,
        counts,
        "leg_mode_scores",
        """
        INSERT INTO app.leg_mode_scores (
          mobility_leg_id,processing_run_id,mode_code,probability,contributions
        ) VALUES (%s,%s,%s,%s,%s::jsonb)
        """,
        score_rows,
    )
    return tuple(leg_ids)


def _persist_connections(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    dataset_id: UUID,
    gap_ids: Sequence[UUID],
    trip_ids: Sequence[UUID],
    trace: ProcessedTrace,
) -> None:
    trip_by_connection: dict[int, UUID] = {}
    for trip_index, trip in enumerate(trace.trips.trips):
        for connection_index in trip.inferred_connection_indices:
            trip_by_connection[connection_index] = trip_ids[trip_index]
    rows: list[Sequence[object]] = []
    for connection_index, connection in enumerate(trace.inferred_connections):
        before = trace.points[connection.before_point_index]
        after = trace.points[connection.after_point_index]
        rows.append(
            (
                _entity_id(run_id, "inferred_connections", str(connection_index)),
                run_id,
                dataset_id,
                gap_ids[connection_index],
                trip_by_connection.get(connection_index),
                connection.kind.value,
                connection.started_at,
                _nonempty_end(connection.started_at, connection.ended_at),
                _point_wkt(before.wgs_longitude, before.wgs_latitude),
                _point_wkt(after.wgs_longitude, after.wgs_latitude),
                connection.confidence,
                connection.displayable,
                _json(
                    {
                        "displacement_meters": connection.displacement_meters,
                        "estimated_path_distance_meters": connection.estimated_path_distance_meters,
                        "counts_toward_confirmed_distance": connection.counts_toward_confirmed_distance,
                        "counts_toward_confirmed_duration": connection.counts_toward_confirmed_duration,
                        "reason_codes": connection.reason_codes,
                    }
                ),
            )
        )
    _execute_many(
        cursor,
        counts,
        "inferred_connections",
        """
        INSERT INTO app.inferred_connections (
          id,processing_run_id,dataset_id,observation_gap_id,trip_id,connection_kind,
          hypothesis_kind,connection_range,start_position,end_position,confidence,
          displayable,evidence
        ) VALUES (
          %s,%s,%s,%s,%s,'gap',%s,tstzrange(%s,%s,'[)'),ST_GeomFromText(%s,4326),
          ST_GeomFromText(%s,4326),%s,%s,%s::jsonb
        )
        """,
        rows,
    )


def _trajectory_line_wkt(vertices: Sequence[TrajectoryVertex]) -> str | None:
    if len(vertices) < 2:
        return None
    coordinates = ",".join(
        f"{vertex.longitude:.12g} {vertex.latitude:.12g}" for vertex in vertices
    )
    return f"LINESTRING({coordinates})"


def _persist_trajectory(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    point_ids: Sequence[int],
    trip_ids: Sequence[UUID],
    leg_ids: Sequence[UUID],
    trace: ProcessedTrace,
) -> None:
    smoothed_point_count = len(trace.smoothing.points)
    smoothing_guard_rate = (
        trace.smoothing.displacement_limited_count / smoothed_point_count
        if smoothed_point_count
        else 1.0
    )
    smoothed_eligible = (
        trace.smoothing.confidence >= 0.65 and smoothing_guard_rate <= 0.10
    )
    map_preferred = bool(
        trace.map_matching is not None
        and trace.map_matching.preferred
        and trace.map_matched_trajectory is not None
    )
    _persist_trajectory_representation(
        cursor,
        counts,
        run_id,
        stages,
        dataset_id,
        point_ids,
        trip_ids,
        leg_ids,
        trace,
        trace.trajectory,
        confidence=None,
        preferred=not smoothed_eligible and not map_preferred,
        associate=not smoothed_eligible and not map_preferred,
        fallback_reason=None,
        map_snapshot_id=None,
    )
    _persist_trajectory_representation(
        cursor,
        counts,
        run_id,
        stages,
        dataset_id,
        point_ids,
        trip_ids,
        leg_ids,
        trace,
        trace.smoothed_trajectory,
        confidence=trace.smoothing.confidence,
        preferred=smoothed_eligible and not map_preferred,
        associate=smoothed_eligible and not map_preferred,
        fallback_reason=(
            None
            if smoothed_eligible
            else "smoothing_confidence_or_guard_rate"
        ),
        map_snapshot_id=None,
    )
    if trace.map_matching is not None and trace.map_matched_trajectory is not None:
        _persist_trajectory_representation(
            cursor,
            counts,
            run_id,
            stages,
            dataset_id,
            point_ids,
            trip_ids,
            leg_ids,
            trace,
            trace.map_matched_trajectory,
            confidence=trace.map_matching.confidence,
            preferred=map_preferred,
            associate=map_preferred,
            fallback_reason=trace.map_matching.fallback_reason,
            map_snapshot_id=trace.map_matching.snapshot.id,
        )


def _persist_trajectory_representation(
    cursor: Cursor,
    counts: dict[str, int],
    run_id: UUID,
    stages: Mapping[str, UUID],
    dataset_id: UUID,
    point_ids: Sequence[int],
    trip_ids: Sequence[UUID],
    leg_ids: Sequence[UUID],
    trace: ProcessedTrace,
    representation: TrajectoryRepresentation,
    *,
    confidence: float | None,
    preferred: bool,
    associate: bool,
    fallback_reason: str | None,
    map_snapshot_id: UUID | None,
) -> None:
    variant_id = _entity_id(run_id, "trajectory_variants", representation.variant_kind)
    cursor.execute(
        """
        INSERT INTO app.trajectory_variants (
          id,processing_run_id,dataset_id,stage_run_id,variant_kind,confidence,
          preferred_for_display,preferred_for_distance,fallback_reason,
          importance_algorithm,metadata,map_snapshot_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        """,
        (
            variant_id,
            run_id,
            dataset_id,
            stages["trajectory"],
            representation.variant_kind,
            confidence,
            preferred,
            preferred,
            fallback_reason,
            representation.importance_algorithm,
            _json(
                {
                    "time_partitioning": "calendar_independent_chunks",
                    "source_observations_immutable": True,
                    "smoothing_guard_rate": (
                        trace.smoothing.displacement_limited_count
                        / len(trace.smoothing.points)
                        if trace.smoothing.points
                        else 1.0
                    ),
                    "displacement_limited_count": (
                        trace.smoothing.displacement_limited_count
                        if representation.variant_kind == "smoothed_gps"
                        else 0
                    ),
                    "map_matching": (
                        {
                            "matcher": trace.map_matching.matcher_name,
                            "matcher_version": trace.map_matching.matcher_version,
                            "snapshot_version": trace.map_matching.snapshot.snapshot_version,
                            "eligible_point_count": trace.map_matching.eligible_point_count,
                            "accepted_point_count": trace.map_matching.accepted_point_count,
                            "moving_coverage": trace.map_matching.moving_coverage,
                            "segments": [
                                {
                                    "request_index": segment.request_index,
                                    "costing": segment.costing,
                                    "status": segment.status,
                                    "confidence": segment.confidence,
                                    "matched_fraction": segment.matched_fraction,
                                    "median_residual_meters": segment.median_residual_meters,
                                    "p95_residual_meters": segment.p95_residual_meters,
                                    "path_ratio": segment.path_ratio,
                                    "reason_codes": segment.reason_codes,
                                }
                                for segment in trace.map_matching.segments
                            ],
                        }
                        if representation.variant_kind == "map_matched"
                        and trace.map_matching is not None
                        else None
                    ),
                }
            ),
            map_snapshot_id,
        ),
    )
    counts["trajectory_variants"] = counts.get("trajectory_variants", 0) + 1
    segment_ids: dict[int, UUID] = {}
    segment_rows: list[Sequence[object]] = []
    vertex_rows: list[Sequence[object]] = []
    for segment in representation.segments:
        segment_id = _entity_id(
            run_id,
            "trajectory_segments",
            f"{representation.variant_kind}:{segment.segment_index}",
        )
        segment_ids[segment.segment_index] = segment_id
        distance = sum(
            haversine_meters(
                left.latitude,
                left.longitude,
                right.latitude,
                right.longitude,
            )
            for left, right in zip(segment.vertices, segment.vertices[1:])
        )
        segment_rows.append(
            (
                segment_id,
                variant_id,
                run_id,
                segment.segment_index,
                segment.started_at,
                _nonempty_end(segment.started_at, segment.ended_at),
                _trajectory_line_wkt(segment.vertices),
                len(segment.vertices),
                distance,
                segment.segment_index > 0,
                segment.segment_index + 1 < len(representation.segments),
            )
        )
        for segment_sequence, vertex in enumerate(segment.vertices):
            if vertex.sequence_number is None:
                raise ValueError("stored trajectory vertices require sequence numbers")
            vertex_rows.append(
                (
                    variant_id,
                    segment_id,
                    dataset_id,
                    vertex.sequence_number,
                    segment_sequence,
                    vertex.recorded_at,
                    _point_wkt(vertex.longitude, vertex.latitude),
                    point_ids[vertex.point_index] if vertex.point_index is not None else None,
                    vertex.is_interpolated,
                    vertex.is_semantic_anchor,
                    list(vertex.anchor_reasons),
                    vertex.importance_meters,
                )
            )
    _execute_many(
        cursor,
        counts,
        "trajectory_segments",
        """
        INSERT INTO app.trajectory_segments (
          id,trajectory_variant_id,processing_run_id,sequence_number,observed_range,
          path,point_count,distance_meters,has_gap_before,has_gap_after
        ) VALUES (
          %s,%s,%s,%s,tstzrange(%s,%s,'[)'),
          CASE WHEN %s IS NULL THEN NULL ELSE ST_GeomFromText(%s,4326) END,
          %s,%s,%s,%s
        )
        """,
        [row[:7] + (row[6],) + row[7:] for row in segment_rows],
    )
    _execute_many(
        cursor,
        counts,
        "trajectory_vertices",
        """
        INSERT INTO app.trajectory_vertices (
          trajectory_variant_id,trajectory_segment_id,dataset_id,sequence_number,
          segment_sequence_number,recorded_at,position,source_point_id,is_interpolated,
          is_semantic_anchor,anchor_reasons,importance_meters
        ) VALUES (%s,%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326),%s,%s,%s,%s,%s)
        """,
        vertex_rows,
    )
    chunk_rows: list[Sequence[object]] = []
    for chunk in representation.chunks:
        finite_importance = [
            vertex.importance_meters
            for vertex in chunk.vertices
            if vertex.importance_meters is not None
        ]
        chunk_rows.append(
            (
                _entity_id(
                    run_id,
                    "trajectory_chunks",
                    f"{representation.variant_kind}:{chunk.chunk_number}",
                ),
                variant_id,
                segment_ids[chunk.segment_index],
                chunk.chunk_number,
                chunk.started_at,
                _nonempty_end(chunk.started_at, chunk.ended_at),
                chunk.first_sequence_number,
                chunk.last_sequence_number,
                len(chunk.vertices),
                chunk.estimated_bytes,
                chunk.has_leading_overlap,
                _trajectory_line_wkt(chunk.vertices),
                min(finite_importance) if finite_importance else None,
                max(finite_importance) if finite_importance else None,
            )
        )
    _execute_many(
        cursor,
        counts,
        "trajectory_chunks",
        """
        INSERT INTO app.trajectory_chunks (
          id,trajectory_variant_id,trajectory_segment_id,chunk_number,time_range,
          first_vertex_sequence,last_vertex_sequence,vertex_count,estimated_bytes,
          has_leading_overlap,path,min_importance_meters,max_importance_meters
        ) VALUES (
          %s,%s,%s,%s,tstzrange(%s,%s,'[)'),%s,%s,%s,%s,%s,
          CASE WHEN %s IS NULL THEN NULL ELSE ST_GeomFromText(%s,4326) END,%s,%s
        )
        """,
        [row[:12] + (row[11],) + row[12:] for row in chunk_rows],
    )

    if not associate:
        return
    segment_by_point: dict[int, UUID] = {}
    for segment in representation.segments:
        for vertex in segment.vertices:
            if vertex.point_index is not None:
                segment_by_point[vertex.point_index] = segment_ids[segment.segment_index]
    trip_segment_rows: list[Sequence[object]] = []
    for trip_index, trip in enumerate(trace.trips.trips):
        for sequence, track_segment in enumerate(trip.track_segments):
            segment_id = segment_by_point[track_segment.point_indices[0]]
            trip_segment_rows.append(
                (
                    trip_ids[trip_index],
                    run_id,
                    segment_id,
                    sequence,
                    track_segment.started_at,
                    _nonempty_end(track_segment.started_at, track_segment.ended_at),
                )
            )
    _execute_many(
        cursor,
        counts,
        "trip_track_segments",
        """
        INSERT INTO app.trip_track_segments (
          trip_id,processing_run_id,trajectory_segment_id,sequence_number,included_range
        ) VALUES (%s,%s,%s,%s,tstzrange(%s,%s,'[)'))
        """,
        trip_segment_rows,
    )
    leg_segment_rows = [
        (
            leg_ids[index],
            run_id,
            segment_by_point[leg.point_indices[0]],
            0,
            leg.observed_started_at,
            _nonempty_end(leg.observed_started_at, leg.observed_ended_at),
        )
        for index, leg in enumerate(trace.modes.legs)
    ]
    _execute_many(
        cursor,
        counts,
        "mobility_leg_segments",
        """
        INSERT INTO app.mobility_leg_segments (
          mobility_leg_id,processing_run_id,trajectory_segment_id,sequence_number,included_range
        ) VALUES (%s,%s,%s,%s,tstzrange(%s,%s,'[)'))
        """,
        leg_segment_rows,
    )
