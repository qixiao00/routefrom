from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from routefrom_pipeline.database.library import withdraw_library_file
from routefrom_pipeline.ingest.export_csv import write_linggan_csv
from routefrom_pipeline.ingest.library import observation_fingerprint
from routefrom_pipeline.ingest.linggan import EXPECTED_COLUMNS, iter_linggan_csv
from routefrom_pipeline.library_worker import (
    LeaseLostError,
    claim_job,
    private_path,
    progress,
    run_once,
)


class CsvExportTests(unittest.TestCase):
    def test_exact_raw_fields_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            source.write_text(
                ",".join(EXPECTED_COLUMNS) + '\n1682028286912,30.9,118.7000000000001,'
                '30.9000000000001,118.7000000000001,50.46,-1,-1,-1,-1,'
                '1682006400000,0,"wifi,中文",1\n', encoding="utf-8",
            )
            original = list(iter_linggan_csv(source))
            output = Path(directory) / "export.csv"
            with output.open("w", encoding="utf-8", newline="") as handle:
                self.assertEqual(write_linggan_csv(handle, original), 1)
            restored = list(iter_linggan_csv(output))
            self.assertEqual(restored, original)
            self.assertEqual(observation_fingerprint(restored[0], "test"),
                             observation_fingerprint(original[0], "test"))

    def test_private_key_rejects_traversal(self):
        for key in ("../secrets.csv", "raw/a/b.csv", "C:/secrets.csv"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                private_path(Path("data"), key)


@unittest.skipUnless(os.environ.get("ROUTEFROM_RUN_DATABASE_TESTS") == "true",
                     "requires an isolated, migrated PostGIS test database")
class WorkerPostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        database = os.environ.get("ROUTEFROM_TEST_DATABASE", "routefrom_integration")
        if not database.startswith("routefrom_integration"):
            raise ValueError("isolated integration database required")
        self.connection = psycopg.connect(os.environ["DATABASE_URL_DIRECT"], dbname=database)
        self.addCleanup(self.connection.close)  # rollback all fixtures
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.user, self.library = uuid4(), uuid4()
        self.connection.execute("INSERT INTO app.users (id) VALUES (%s)", (self.user,))
        self.connection.execute(
            "INSERT INTO app.footprint_libraries (id, user_id) VALUES (%s, %s)",
            (self.library, self.user),
        )

    def enqueue(self, entries=None, *, body=None):
        key = f"raw/{self.user}/{uuid4()}.csv"
        path = private_path(self.root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if body is None:
            body = ",".join(EXPECTED_COLUMNS) + "\n"
            for timestamp, longitude in entries:
                body += (f"{timestamp},30.9,{longitude},30.9,{longitude},50,-1,5,-1,-1,"
                         "1682006400000,0,,1\n")
        path.write_text(body, encoding="utf-8")
        dataset = self.connection.execute(
            """INSERT INTO app.datasets (user_id, original_filename, source_object_key,
                 source_sha256, source_size_bytes) VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (self.user, path.name, key, hashlib.sha256(path.read_bytes()).hexdigest(),
             path.stat().st_size),
        ).fetchone()[0]
        return self.connection.execute(
            """INSERT INTO app.library_jobs (library_id, user_id, dataset_id, kind)
               VALUES (%s, %s, %s, 'import') RETURNING id""", (self.library, self.user, dataset),
        ).fetchone()[0]

    def job(self, job_id):
        return self.connection.execute(
            "SELECT status, metrics, error_message FROM app.library_jobs WHERE id = %s", (job_id,),
        ).fetchone()

    def active(self):
        return self.connection.execute(
            "SELECT active_snapshot_id FROM app.footprint_libraries WHERE id = %s", (self.library,),
        ).fetchone()[0]

    def process(self):
        self.assertTrue(run_once(self.connection, self.root, library_id=self.library))

    def test_full_processing_overlap_and_failure_preserves_published_map(self):
        base = 1682028286912
        first = self.enqueue([(base + i * 10000, str(118.7 + i * 0.0001)) for i in range(5)])
        self.process()
        state = self.job(first)
        self.assertEqual(state[0], "succeeded", state[2])
        self.assertEqual(state[1]["point_count"], 5)
        old = self.active()
        overlapping = self.enqueue([(base + 40000, "118.7004"), (base + 50000, "118.7005")])
        self.process()
        state = self.job(overlapping)
        self.assertEqual(state[0], "succeeded", state[2])
        self.assertEqual(state[1]["duplicate_rows"], 1)
        self.assertEqual(state[1]["point_count"], 6)
        self.assertNotEqual(old, self.active())
        old = self.active()
        invalid = self.enqueue(body="not,a,linggan,csv\n")
        self.process()
        self.assertEqual(self.job(invalid)[0], "failed")
        self.assertEqual(old, self.active())
        self.assertFalse(run_once(self.connection, self.root, library_id=self.library))

    def test_conflict_pauses_and_resolves_then_decision_survives_new_import(self):
        base = 1682028286912
        first = self.enqueue([(base, "118.7"), (base + 10000, "118.7001")])
        self.process()
        self.assertEqual(self.job(first)[0], "succeeded", self.job(first)[2])
        old = self.active()
        conflict = self.enqueue([(base, "118.70001")])
        self.process()
        state = self.job(conflict)
        self.assertEqual(state[0], "awaiting_resolution", state[2])
        self.assertEqual(self.active(), old)
        candidate = state[1]["conflicts"][0]["candidates"][0]["fingerprint"]
        self.connection.execute(
            """UPDATE app.library_jobs SET resolutions = jsonb_build_object(%s::text, %s::text),
                 status = 'queued' WHERE id = %s""", (str(base), candidate, conflict),
        )
        self.process()
        self.assertEqual(self.job(conflict)[0], "succeeded", self.job(conflict)[2])
        newer = self.enqueue([(base + 20000, "118.7002")])
        self.process()
        self.assertEqual(self.job(newer)[0], "succeeded", self.job(newer)[2])
        self.assertEqual(self.job(newer)[1]["point_count"], 3)
        conflict_file = self.connection.execute(
            """SELECT file.id, file.dataset_id FROM app.library_files AS file
               JOIN app.library_jobs AS job ON job.dataset_id = file.dataset_id WHERE job.id = %s""",
            (conflict,),
        ).fetchone()
        withdraw_library_file(self.connection, user_id=self.user, library_id=self.library,
                              file_id=conflict_file[0])
        self.connection.execute(
            "INSERT INTO app.library_jobs (library_id, user_id, kind) VALUES (%s, %s, 'reprocess')",
            (self.library, self.user),
        )
        self.process()
        remembered = self.connection.execute(
            "SELECT decisions FROM app.library_snapshots WHERE id = %s", (self.active(),),
        ).fetchone()[0]
        self.assertEqual(remembered[str(base)], candidate, "dormant decisions survive withdrawal")
        restored = self.connection.execute(
            """INSERT INTO app.library_jobs (library_id, user_id, dataset_id, kind)
               VALUES (%s, %s, %s, 'import') RETURNING id""",
            (self.library, self.user, conflict_file[1]),
        ).fetchone()[0]
        self.process()
        self.assertEqual(self.job(restored)[0], "succeeded", self.job(restored)[2])

    def test_withdraw_last_file_publishes_empty_snapshot(self):
        first = self.enqueue([(1682028286912, "118.7")])
        self.process()
        self.assertEqual(self.job(first)[0], "succeeded", self.job(first)[2])
        file_id = self.connection.execute(
            "SELECT id FROM app.library_files WHERE library_id = %s", (self.library,),
        ).fetchone()[0]
        withdraw_library_file(self.connection, user_id=self.user, library_id=self.library,
                              file_id=file_id)
        job = self.connection.execute(
            """INSERT INTO app.library_jobs (library_id, user_id, kind)
               VALUES (%s, %s, 'reprocess') RETURNING id""", (self.library, self.user),
        ).fetchone()[0]
        self.process()
        self.assertEqual(self.job(job)[0], "succeeded", self.job(job)[2])
        state = self.connection.execute(
            "SELECT point_count, dataset_id, processing_run_id FROM app.library_snapshots WHERE id = %s",
            (self.active(),),
        ).fetchone()
        self.assertEqual(state, (0, None, None))

    def test_expired_job_is_reclaimed_and_old_token_is_fenced(self):
        self.enqueue([(1682028286912, "118.7")])
        old = claim_job(self.connection, library_id=self.library)
        self.connection.execute(
            "UPDATE app.library_jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
            (old[0],),
        )
        replacement = claim_job(self.connection, library_id=self.library)
        self.assertEqual(old[0], replacement[0])
        self.assertNotEqual(old[-1], replacement[-1])
        with self.assertRaises(LeaseLostError):
            progress(self.connection, old, "publish", 95)

    def test_revision_changed_during_processing_cannot_replace_active_map(self):
        first = self.enqueue([(1682028286912, "118.7")])
        self.process()
        self.assertEqual(self.job(first)[0], "succeeded", self.job(first)[2])
        old = self.active()
        second = self.enqueue([(1682028296912, "118.7001")])
        from routefrom_pipeline.library_worker import process_trace

        def change_revision(points):
            trace = process_trace(points)
            self.connection.execute(
                "UPDATE app.footprint_libraries SET revision = revision + 1 WHERE id = %s",
                (self.library,),
            )
            return trace

        with patch("routefrom_pipeline.library_worker.process_trace", side_effect=change_revision):
            self.process()
        self.assertEqual(self.job(second)[0], "superseded", self.job(second)[2])
        self.assertEqual(self.active(), old)

    def test_cancelled_import_cannot_reactivate_membership(self):
        job_id = self.enqueue([(1682028286912, "118.7")])
        from routefrom_pipeline.library_worker import import_linggan_csv

        def cancel_after_raw_import(*args, **kwargs):
            result = import_linggan_csv(*args, **kwargs)
            self.connection.execute(
                "UPDATE app.library_jobs SET status = 'superseded', lease_until = NULL WHERE id = %s",
                (job_id,),
            )
            return result

        with patch("routefrom_pipeline.library_worker.import_linggan_csv",
                   side_effect=cancel_after_raw_import):
            self.process()
        self.assertEqual(self.job(job_id)[0], "superseded")
        count = self.connection.execute(
            "SELECT count(*) FROM app.library_files WHERE library_id = %s", (self.library,),
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertIsNone(self.active())
