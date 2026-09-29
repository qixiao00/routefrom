"""Read-only audit of short returns contradicted by Linggan's speed readings."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from routefrom_pipeline.continuity.sampling import (
    SamplingModelConfig,
    _multi_point_return_conflicts_with_reported_speeds,
    _return_conflicts_with_reported_speeds,
)
from routefrom_pipeline.ingest import iter_linggan_csv
from routefrom_pipeline.quality import build_point_features


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--start", type=datetime.fromisoformat)
    parser.add_argument("--end", type=datetime.fromisoformat)
    args = parser.parse_args()
    points = tuple(iter_linggan_csv(args.source))
    features = build_point_features(points)
    current = SamplingModelConfig()
    prior = replace(current, sensor_conflict_return_min_leg_meters=500.0)
    narrow = replace(current, multi_point_return_anchor_radius_meters=80.0)
    old_apexes = []
    new_apexes = []
    multi_point_intervals = []
    additional_multi_point_intervals = []
    for feature in features:
        instant = points[feature.point_index].recorded_at
        if args.start and instant < args.start:
            continue
        if args.end and instant >= args.end:
            continue
        if _return_conflicts_with_reported_speeds(points, feature, prior):
            old_apexes.append(feature.point_index)
        if _return_conflicts_with_reported_speeds(points, feature, current):
            new_apexes.append(feature.point_index)
        if _multi_point_return_conflicts_with_reported_speeds(points, feature, current):
            multi_point_intervals.append(feature.point_index)
            if not _multi_point_return_conflicts_with_reported_speeds(
                points, feature, narrow
            ):
                additional_multi_point_intervals.append(feature.point_index)
    print(json.dumps({
        "points": len(points),
        "old_apexes": len(old_apexes),
        "new_apexes": len(new_apexes),
        "additional_apexes": len(set(new_apexes) - set(old_apexes)),
        "additional_affected_intervals": len({
            interval for index in set(new_apexes) - set(old_apexes)
            for interval in (index - 1, index)
        }),
        "multi_point_conflicted_intervals": len(multi_point_intervals),
        "additional_multi_point_intervals_over_narrow_radius": len(
            additional_multi_point_intervals
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
