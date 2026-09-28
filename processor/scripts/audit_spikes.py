"""Aggregate short out-and-back observations without printing coordinates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from routefrom_pipeline.ingest import iter_linggan_csv
from routefrom_pipeline.quality import assess_points, build_point_features
from routefrom_pipeline.quality.features import haversine_meters


def distance(left: object, right: object) -> float:
    return haversine_meters(
        left.wgs_latitude,
        left.wgs_longitude,
        right.wgs_latitude,
        right.wgs_longitude,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    points = tuple(iter_linggan_csv(args.source))
    assessments = assess_points(build_point_features(points))
    kept_times: set[str] | None = None
    connected_pairs: set[tuple[str, str]] | None = None
    if args.preview:
        preview = json.loads(args.preview.read_text(encoding="utf-8"))
        kept_times = {
            vertex[0]
            for path in preview["paths"]
            for vertex in path["vertices"]
        }
        connected_pairs = {
            (before[0], after[0])
            for path in preview["paths"]
            for before, after in zip(path["vertices"], path["vertices"][1:])
        }

    candidates = []
    for index in range(1, len(points) - 1):
        previous, current, following = points[index - 1:index + 2]
        first = distance(previous, current)
        second = distance(current, following)
        direct = distance(previous, following)
        elapsed = (following.recorded_at - previous.recorded_at).total_seconds()
        if min(first, second) < 500 or direct > min(first, second) * 0.15 or elapsed > 600:
            continue
        previous_seconds = (current.recorded_at - previous.recorded_at).total_seconds()
        following_seconds = (following.recorded_at - current.recorded_at).total_seconds()
        assessment = assessments[index]
        instant = current.recorded_at.isoformat().replace("+00:00", "Z")
        previous_instant = previous.recorded_at.isoformat().replace("+00:00", "Z")
        following_instant = following.recorded_at.isoformat().replace("+00:00", "Z")
        candidates.append({
            "row": current.source_row_number,
            "instant": instant,
            "legMeters": [round(first), round(second)],
            "bypassMeters": round(direct),
            "elapsedSeconds": round(elapsed),
            "calculatedSpeedMps": [
                round(first / previous_seconds, 1) if previous_seconds > 0 else None,
                round(second / following_seconds, 1) if following_seconds > 0 else None,
            ],
            "recordedSpeedMps": [
                previous.recorded_speed_mps,
                current.recorded_speed_mps,
                following.recorded_speed_mps,
            ],
            "horizontalAccuracyMeters": [
                previous.horizontal_accuracy_meters,
                current.horizontal_accuracy_meters,
                following.horizontal_accuracy_meters,
            ],
            "anomalyProbability": round(assessment.anomaly_probability, 3),
            "exclusionSupported": assessment.exclusion_supported,
            "qualityStatus": assessment.quality_status.value,
            "inPreview": instant in kept_times if kept_times is not None else None,
            "connectedToNeighbors": [
                (previous_instant, instant) in connected_pairs,
                (instant, following_instant) in connected_pairs,
            ] if connected_pairs is not None else None,
        })
    candidates.sort(key=lambda candidate: min(candidate["legMeters"]), reverse=True)
    print(json.dumps({
        "count": len(candidates),
        "connectedCandidateCount": sum(
            any(candidate["connectedToNeighbors"] or ()) for candidate in candidates
        ) if connected_pairs is not None else None,
        "top": candidates[:args.limit],
    }, indent=2))


if __name__ == "__main__":
    main()
