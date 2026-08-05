from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from routefrom_pipeline.ingest.linggan import LingganCsvError, iter_linggan_csv


HEADER = (
    "geoTime,latitude,longitude,wgsLatitude,wgsLongitude,altitude,course,"
    "horizontalAccuracy,verticalAccuracy,speed,dayTimeMills,networkType,"
    "networkName,locationType\n"
)


class LingganCsvTests(unittest.TestCase):
    def _write_csv(self, body: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "points.csv"
        path.write_text(HEADER + body, encoding="utf-8")
        return path

    def test_preserves_source_sentinels_and_normalizes_them_to_none(self) -> None:
        path = self._write_csv(
            "1682028286912,30.9,118.7,30.91,118.70,50.46,-1,-1,-1,-1,"
            "1682006400000,0,,1\n"
        )

        point = next(iter_linggan_csv(path))

        self.assertEqual(point.source_course, -1)
        self.assertIsNone(point.course_degrees)
        self.assertEqual(point.source_speed, -1)
        self.assertIsNone(point.recorded_speed_mps)
        self.assertEqual(
            set(point.normalization_flags),
            {
                "course_missing_sentinel",
                "horizontal_accuracy_missing_sentinel",
                "vertical_accuracy_missing_sentinel",
                "speed_missing_sentinel",
            },
        )

    def test_rejects_invalid_coordinates_as_structural_errors(self) -> None:
        path = self._write_csv(
            "1682028286912,30.9,118.7,130.91,118.70,50.46,10,5,5,1,"
            "1682006400000,0,,1\n"
        )

        with self.assertRaisesRegex(LingganCsvError, "wgsLatitude"):
            list(iter_linggan_csv(path))

    def test_rejects_an_unexpected_schema(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "points.csv"
        path.write_text("geoTime,latitude\n1,2\n", encoding="utf-8")

        with self.assertRaisesRegex(LingganCsvError, "unexpected CSV columns"):
            list(iter_linggan_csv(path))


if __name__ == "__main__":
    unittest.main()
