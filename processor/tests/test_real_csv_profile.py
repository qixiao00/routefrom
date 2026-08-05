from __future__ import annotations

import unittest
from itertools import islice
from pathlib import Path

from routefrom_pipeline.continuity import select_continuity
from routefrom_pipeline.ingest import iter_linggan_csv
from routefrom_pipeline.ingest.linggan import profile_linggan_csv
from routefrom_pipeline.model import QualityStatus
from routefrom_pipeline.quality import assess_points, build_point_features


class RealCsvProfileTests(unittest.TestCase):
    def test_private_linggan_export_matches_the_locked_import_baseline(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "data" / "raw" / "灵敢足迹（定位点2023.04.21-2026.08.05）.csv"
        if not path.exists():
            self.skipTest("private Linggan CSV is not available")

        profile = profile_linggan_csv(path)

        self.assertEqual(
            profile.sha256,
            "ACA94A290A1E2A32E8E4869492FDCE1C2FA2308D80B7B319F12AAFFA710F2F2C",
        )
        self.assertEqual(profile.row_count, 102_142)
        self.assertTrue(profile.timestamps_strictly_increasing)
        self.assertEqual(profile.duplicate_timestamp_count, 0)
        self.assertEqual(profile.sentinel_counts["course"], 17_059)
        self.assertEqual(profile.sentinel_counts["speed"], 16_802)
        self.assertEqual(profile.sentinel_counts["horizontalAccuracy"], 63)
        self.assertEqual(profile.sentinel_counts["verticalAccuracy"], 994)

    def test_real_changsha_xiamen_teleports_are_excluded_and_bypassed(self) -> None:
        raw_directory = Path(__file__).resolve().parents[2] / "data" / "raw"
        candidates = tuple(raw_directory.glob("*.csv"))
        if len(candidates) != 1:
            self.skipTest("one private Linggan CSV is required for this regression")

        points = list(islice(iter_linggan_csv(candidates[0]), 92_550, 92_575))
        assessments = assess_points(build_point_features(points))
        local_intervals = [None] + [
            (current.recorded_at - previous.recorded_at).total_seconds()
            for previous, current in zip(points, points[1:])
        ]
        result = select_continuity(
            points,
            assessments,
            local_intervals_seconds=local_intervals,
        )

        excluded_rows = {
            point.source_row_number
            for point, assessment in zip(points, assessments)
            if assessment.quality_status == QualityStatus.EXCLUDED
        }
        skipped_rows = {
            points[index].source_row_number for index in result.skipped_point_indices
        }
        bypass_rows = {
            points[index].source_row_number
            for edge in result.edges
            if edge.kind == "bypass"
            for index in range(edge.from_index + 1, edge.to_index)
        }

        self.assertEqual(excluded_rows, {92_562, 92_570, 92_573})
        self.assertEqual(skipped_rows, excluded_rows)
        self.assertEqual(bypass_rows, excluded_rows)
        self.assertEqual(len(result.segments), 1)


if __name__ == "__main__":
    unittest.main()
