from __future__ import annotations

import hashlib
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from uuid import UUID

from routefrom_pipeline.database import import_linggan_csv


HEADER = (
    "geoTime,latitude,longitude,wgsLatitude,wgsLongitude,altitude,course,"
    "horizontalAccuracy,verticalAccuracy,speed,dayTimeMills,networkType,"
    "networkName,locationType\n"
)


class FakeCursor:
    def __init__(
        self,
        dataset_row: tuple[object, ...],
        *,
        existing_import: tuple[object, ...] | None = None,
        max_import_number: int = 0,
    ) -> None:
        self.dataset_row = dataset_row
        self.existing_import = existing_import
        self.max_import_number = max_import_number
        self.next_row: tuple[object, ...] | None = None
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.many: list[tuple[str, list[tuple[object, ...]]]] = []

    def execute(self, query: str, params: Any = ()) -> None:
        normalized = " ".join(query.split())
        self.executions.append((normalized, tuple(params)))
        if "SELECT source_sha256, source_size_bytes" in normalized:
            self.next_row = self.dataset_row
        elif "SELECT id, status, row_count" in normalized:
            self.next_row = self.existing_import
        elif "SELECT COALESCE(max(import_number)" in normalized:
            self.next_row = (self.max_import_number,)
        else:
            self.next_row = None

    def executemany(self, query: str, params_seq: Any) -> None:
        self.many.append((query, [tuple(params) for params in params_seq]))

    def fetchone(self) -> tuple[object, ...] | None:
        return self.next_row


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.fake_cursor = cursor
        self.transaction_count = 0

    def transaction(self) -> Any:
        self.transaction_count += 1
        return nullcontext()

    def cursor(self) -> FakeCursor:
        return self.fake_cursor


class DatabaseImportTests(unittest.TestCase):
    dataset_id = UUID("ab96de99-f2d3-402b-ad2b-c756e05d4d62")

    def _write_csv(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "linggan.csv"
        path.write_text(
            HEADER
            + "1682028286912,30.9,118.7,30.91,118.70,50.46,-1,-1,-1,-1,"
            "1682006400000,0,,1\n"
            + "1682028296912,30.91,118.71,30.92,118.72,51,20,8,12,2,"
            "1682006400000,1,5G,2\n",
            encoding="utf-8",
        )
        return path

    def _dataset_row(self, path: Path) -> tuple[object, ...]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return (digest, path.stat().st_size, "private/linggan.csv")

    def test_imports_points_in_batches_and_preserves_source_values(self) -> None:
        path = self._write_csv()
        cursor = FakeCursor(self._dataset_row(path), max_import_number=2)
        connection = FakeConnection(cursor)

        result = import_linggan_csv(
            connection,
            dataset_id=self.dataset_id,
            source_path=path,
            timezone="Asia/Shanghai",
            batch_size=1,
        )

        self.assertFalse(result.reused)
        self.assertEqual(result.inserted_point_count, 2)
        self.assertEqual(result.profile.row_count, 2)
        self.assertEqual(connection.transaction_count, 1)
        self.assertEqual(len(cursor.many), 2)
        rows = [row for _, batch in cursor.many for row in batch]
        self.assertTrue(all(len(row) == 27 for row in rows))
        self.assertTrue(all(query.count("%s") == 27 for query, _ in cursor.many))
        self.assertEqual(rows[0][0], self.dataset_id)
        self.assertEqual(rows[0][1], result.dataset_import_id)
        self.assertEqual(rows[0][6], "POINT(118.7 30.91)")
        self.assertIsNone(rows[0][10])
        self.assertEqual(
            set(rows[0][26]),
            {
                "course_missing_sentinel",
                "horizontal_accuracy_missing_sentinel",
                "vertical_accuracy_missing_sentinel",
                "speed_missing_sentinel",
            },
        )
        import_insert = next(
            params
            for query, params in cursor.executions
            if "INSERT INTO app.dataset_imports" in query
        )
        self.assertEqual(import_insert[2], 3)
        self.assertTrue(
            any("UPDATE app.datasets" in query for query, _ in cursor.executions)
        )

    def test_reuses_a_successful_import_of_identical_bytes(self) -> None:
        path = self._write_csv()
        existing_id = UUID("3a2b810e-ce30-408a-a29b-9e6c9bf026e5")
        cursor = FakeCursor(
            self._dataset_row(path),
            existing_import=(existing_id, "succeeded", 2, 0),
        )
        connection = FakeConnection(cursor)

        result = import_linggan_csv(
            connection,
            dataset_id=self.dataset_id,
            source_path=path,
        )

        self.assertTrue(result.reused)
        self.assertEqual(result.dataset_import_id, existing_id)
        self.assertEqual(result.inserted_point_count, 0)
        self.assertEqual(cursor.many, [])
        self.assertFalse(
            any("INSERT INTO app.dataset_imports" in query for query, _ in cursor.executions)
        )

    def test_rejects_source_bytes_that_do_not_match_dataset_metadata(self) -> None:
        path = self._write_csv()
        cursor = FakeCursor(("f" * 64, path.stat().st_size, "private/linggan.csv"))
        connection = FakeConnection(cursor)

        with self.assertRaisesRegex(ValueError, "sha256"):
            import_linggan_csv(
                connection,
                dataset_id=self.dataset_id,
                source_path=path,
            )

        self.assertFalse(
            any("INSERT INTO app.dataset_imports" in query for query, _ in cursor.executions)
        )
        self.assertEqual(cursor.many, [])


if __name__ == "__main__":
    unittest.main()
