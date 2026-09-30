from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from routefrom_pipeline.database.imports import import_linggan_csv
from routefrom_pipeline.database.library import (
    attach_library_import,
    load_library_snapshot,
    withdraw_library_file,
)
from routefrom_pipeline.ingest.linggan import EXPECTED_COLUMNS


@unittest.skipUnless(os.environ.get("ROUTEFROM_RUN_DATABASE_TESTS") == "true",
                     "requires an isolated, migrated PostGIS test database")
class LibraryPostgresIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg

        # Never run fixture writes in the production/default database.
        database = os.environ.get("ROUTEFROM_TEST_DATABASE", "routefrom_integration")
        if not database.startswith("routefrom_integration"):
            raise ValueError("integration tests require a routefrom_integration* database")
        self.connection = psycopg.connect(os.environ["DATABASE_URL_DIRECT"], dbname=database)
        self.addCleanup(self.connection.close)  # uncommitted fixtures are rolled back
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.user = uuid4()
        self.other_user = uuid4()
        self.connection.execute("INSERT INTO app.users (id) VALUES (%s), (%s)",
                                (self.user, self.other_user))

    def csv(self, entries: list[tuple[int, str]]) -> Path:
        path = Path(self.directory.name) / f"{uuid4()}.csv"
        body = ",".join(EXPECTED_COLUMNS) + "\n"
        for timestamp, longitude in entries:
            body += (f"{timestamp},30.9,{longitude},30.9,{longitude},50,-1,5,-1,-1,"
                     "1682006400000,0,,1\n")
        path.write_text(body, encoding="utf-8")
        return path

    def raw_import(self, path: Path):
        dataset = uuid4()
        self.connection.execute(
            """INSERT INTO app.datasets
               (id, user_id, original_filename, source_object_key, source_sha256, source_size_bytes)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (dataset, self.user, path.name, "integration/" + path.name,
             hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size),
        )
        imported = import_linggan_csv(self.connection, dataset_id=dataset, source_path=path)
        return dataset, imported.dataset_import_id

    def attach(self, path: Path, *, batch_size: int = 2000):
        dataset, imported = self.raw_import(path)
        return attach_library_import(self.connection, user_id=self.user,
                                     dataset_id=dataset, dataset_import_id=imported,
                                     source_path=path, batch_size=batch_size)

    def test_overlap_withdrawal_reactivation_and_source_payload_roundtrip(self):
        base = 1682028286912
        a = self.attach(self.csv([(base, "118.7000000000001"), (base + 1000, "118.71")]),
                        batch_size=1)
        b_path = self.csv([(base + 1000, "118.71"), (base + 2000, "118.72")])
        dataset, imported = self.raw_import(b_path)
        b = attach_library_import(self.connection, user_id=self.user, dataset_id=dataset,
                                  dataset_import_id=imported, source_path=b_path)
        self.assertEqual((a.added_observation_count, b.added_observation_count), (2, 1))
        self.assertEqual(b.duplicate_row_count, 1)
        revision, snapshot = load_library_snapshot(self.connection, user_id=self.user,
                                                  library_id=a.library_id)
        self.assertEqual(revision, 2)
        self.assertEqual(len(snapshot.observations), 3)
        self.assertEqual(snapshot.observations[0].point.wgs_longitude, 118.7000000000001)
        self.assertIsNone(snapshot.observations[0].point.recorded_speed_mps)
        self.assertEqual(snapshot.observations[0].point.source_speed, -1)
        repeated = attach_library_import(self.connection, user_id=self.user, dataset_id=dataset,
                                         dataset_import_id=imported, source_path=b_path)
        self.assertTrue(repeated.reused)
        self.assertEqual(repeated.revision, 2)
        self.assertEqual(withdraw_library_file(self.connection, user_id=self.user,
                                              library_id=a.library_id, file_id=a.file_id), 3)
        _, remaining = load_library_snapshot(self.connection, user_id=self.user,
                                             library_id=a.library_id)
        self.assertEqual(len(remaining.observations), 2)
        self.assertEqual(withdraw_library_file(self.connection, user_id=self.user,
                                              library_id=a.library_id, file_id=a.file_id), 3)
        # Re-activate the original file and report only newly active observations.
        row = self.connection.execute(
            "SELECT dataset_id, dataset_import_id FROM app.library_files WHERE id = %s",
            (a.file_id,),
        ).fetchone()
        original_path = next(path for path in Path(self.directory.name).glob("*.csv")
                             if path != b_path)
        restored = attach_library_import(self.connection, user_id=self.user, dataset_id=row[0],
                                         dataset_import_id=row[1], source_path=original_path)
        self.assertEqual((restored.revision, restored.added_observation_count), (4, 1))
        _, full = load_library_snapshot(self.connection, user_id=self.user, library_id=a.library_id)
        self.assertEqual(full.content_sha256, snapshot.content_sha256)

    def test_conflicts_survive_database_roundtrip(self):
        base = 1682028286912
        a = self.attach(self.csv([(base, "118.7")]))
        self.attach(self.csv([(base, "118.70000001")]))
        _, snapshot = load_library_snapshot(self.connection, user_id=self.user,
                                             library_id=a.library_id)
        self.assertEqual(len(snapshot.conflicts), 1)
        with self.assertRaisesRegex(ValueError, "resolution"):
            snapshot.processing_points()

    def test_duplicate_rows_within_one_file_keep_both_origins(self):
        a = self.attach(self.csv([(1682028286912, "118.7"), (1682028286912, "118.7")]))
        self.assertEqual((a.added_observation_count, a.duplicate_row_count), (1, 1))
        _, snapshot = load_library_snapshot(self.connection, user_id=self.user,
                                             library_id=a.library_id)
        self.assertEqual(len(snapshot.observations[0].origins), 2)

    def test_other_user_cannot_attach_read_or_withdraw(self):
        path = self.csv([(1682028286912, "118.7")])
        dataset, imported = self.raw_import(path)
        with self.assertRaisesRegex(ValueError, "this user"):
            attach_library_import(self.connection, user_id=self.other_user, dataset_id=dataset,
                                  dataset_import_id=imported, source_path=path)
        a = attach_library_import(self.connection, user_id=self.user, dataset_id=dataset,
                                  dataset_import_id=imported, source_path=path)
        with self.assertRaises(ValueError):
            load_library_snapshot(self.connection, user_id=self.other_user, library_id=a.library_id)
        with self.assertRaises(ValueError):
            withdraw_library_file(self.connection, user_id=self.other_user,
                                  library_id=a.library_id, file_id=a.file_id)
        _, snapshot = load_library_snapshot(self.connection, user_id=self.user,
                                             library_id=a.library_id)
        self.assertEqual(len(snapshot.observations), 1)

    def test_missing_raw_row_rolls_back_entire_library_attachment(self):
        path = self.csv([(1682028286912, "118.7"), (1682028287912, "118.71")])
        dataset, imported = self.raw_import(path)
        self.connection.execute(
            "DELETE FROM app.location_points WHERE dataset_import_id = %s AND source_row_number = 3",
            (imported,),
        )
        with self.assertRaisesRegex(ValueError, "missing source rows"):
            attach_library_import(self.connection, user_id=self.user, dataset_id=dataset,
                                  dataset_import_id=imported, source_path=path)
        count = self.connection.execute(
            "SELECT count(*) FROM app.footprint_libraries WHERE user_id = %s", (self.user,),
        ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
