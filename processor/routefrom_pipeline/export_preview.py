from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid5

from routefrom_pipeline.ingest import iter_linggan_csv
from routefrom_pipeline.pipeline import process_trace
from routefrom_pipeline.trajectory import TimeRange, query_trajectory

_PREVIEW_NAMESPACE = UUID("a2647e23-d805-4a64-843d-f605dd1004e6")


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def export_workspace_preview(
    source: Path,
    output: Path,
    *,
    vertex_budget: int = 15_000,
) -> dict[str, object]:
    started = perf_counter()
    points = tuple(iter_linggan_csv(source))
    if not points:
        raise ValueError("source CSV contains no points")
    trace = process_trace(points)
    preferred = (
        trace.smoothed_trajectory
        if trace.smoothing.confidence >= 0.65
        and trace.smoothing.displacement_limited_count / len(trace.smoothing.points) <= 0.10
        else trace.trajectory
    )
    selected_range = TimeRange(
        points[0].recorded_at,
        points[-1].recorded_at + timedelta(microseconds=1),
    )
    visible = query_trajectory(
        preferred,
        [selected_range],
        pixel_tolerance_meters=1.0,
        vertex_budget=vertex_budget,
    )
    dataset_key = f"{source.name}:{len(points)}:{points[0].recorded_at}:{points[-1].recorded_at}"
    dataset_id = uuid5(_PREVIEW_NAMESPACE, dataset_key)
    run_id = uuid5(dataset_id, trace.algorithm_version)
    last = points[-1].recorded_at + timedelta(seconds=1)
    suggested_start = max(points[0].recorded_at, last - timedelta(days=14))
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "dataset": {
            "id": str(dataset_id),
            "name": "灵敢足迹",
            "sourceName": source.name,
            "pointCount": len(points),
            "startedAt": _instant(points[0].recorded_at),
            "endedAt": _instant(last),
        },
        "processing": {
            "runId": str(run_id),
            "algorithmVersion": trace.algorithm_version,
            "trajectoryVariant": preferred.variant_kind,
            "smoothingConfidence": round(trace.smoothing.confidence, 6),
        },
        "summary": {
            "gapCount": len(trace.observation_gaps),
            "stayCount": len(trace.stays.events),
            "tripCount": len(trace.trips.trips),
            "placeCount": len(trace.places.places),
            "modeLegCount": len(trace.modes.legs),
        },
        "suggestedRanges": [
            {"start": _instant(suggested_start), "end": _instant(last)},
        ],
        "paths": [
            {
                "segmentIndex": path.segment_index,
                "vertices": [
                    [
                        _instant(vertex.recorded_at),
                        round(vertex.longitude, 6),
                        round(vertex.latitude, 6),
                    ]
                    for vertex in path.vertices
                ],
            }
            for path in visible
        ],
        "gaps": [
            {
                "id": f"gap-{index}",
                "start": _instant(gap.started_at),
                "end": _instant(gap.ended_at),
                "cause": gap.cause.value,
                "confidence": round(gap.confidence, 4),
                "startPosition": [
                    round(points[gap.before_point_index].wgs_longitude, 6),
                    round(points[gap.before_point_index].wgs_latitude, 6),
                ],
                "endPosition": [
                    round(points[gap.after_point_index].wgs_longitude, 6),
                    round(points[gap.after_point_index].wgs_latitude, 6),
                ],
            }
            for index, gap in enumerate(trace.observation_gaps)
        ],
        "stays": [
            {
                "id": f"stay-{index}",
                "start": _instant(event.observed_started_at),
                "end": _instant(event.observed_ended_at + timedelta(microseconds=1)),
                "kind": event.event_type.value,
                "position": [
                    round(event.centroid_longitude, 6),
                    round(event.centroid_latitude, 6),
                ],
                "confidence": round(event.confidence, 4),
                "visitProbability": round(event.visit_probability, 4),
                "durationSeconds": round(event.observed_duration_seconds, 1),
            }
            for index, event in enumerate(trace.stays.events)
        ],
        "trips": [
            {
                "id": f"trip-{trip.sequence_number}",
                "start": _instant(trip.started_at),
                "end": _instant(trip.ended_at + timedelta(microseconds=1)),
                "boundary": trip.boundary_state.value,
                "confidence": round(trip.confidence, 4),
                "distanceMeters": round(trip.confirmed_distance_meters, 1),
                "unknownSeconds": round(trip.unknown_duration_seconds, 1),
            }
            for trip in trace.trips.trips
        ],
        "modeLegs": [
            {
                "id": f"leg-{index}",
                "start": _instant(leg.observed_started_at),
                "end": _instant(leg.observed_ended_at + timedelta(microseconds=1)),
                "mode": leg.selected_mode_code,
                "level": leg.selected_mode_level,
                "confidence": round(leg.confidence, 4),
            }
            for index, leg in enumerate(trace.modes.legs)
        ],
        "places": [
            {
                "id": f"place-{index}",
                "name": f"常去地点 {index + 1}",
                "position": [
                    round(place.centroid_longitude, 6),
                    round(place.centroid_latitude, 6),
                ],
                "visitCount": place.visit_count,
                "dwellSeconds": round(place.observed_duration_seconds, 1),
                "confidence": round(place.confidence, 4),
                "frequentProbability": round(place.frequent_probability, 4),
                "firstVisitedAt": _instant(place.first_visited_at),
                "lastVisitedAt": _instant(place.last_visited_at),
            }
            for index, place in enumerate(trace.places.places)
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "output": str(output),
        "bytes": output.stat().st_size,
        "points": len(points),
        "paths": len(visible),
        "seconds": round(perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a private local frontend preview artifact")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/generated/workspace-preview.json"))
    parser.add_argument("--vertex-budget", type=int, default=15_000)
    args = parser.parse_args()
    result = export_workspace_preview(args.source, args.output, vertex_budget=args.vertex_budget)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
