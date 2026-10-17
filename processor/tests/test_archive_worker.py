from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from pglast import parse_sql

from routefrom_pipeline.archive_worker import process_archive_job
from routefrom_pipeline.archive_worker import run_once as run_archive
from routefrom_pipeline.backup_archive import write_backup
from routefrom_pipeline.database.archive_jobs import (
    ArchiveLeaseLost,
    approve_restore,
    claim_archive_job,
)
from routefrom_pipeline.database.restore_library import _OBSERVATIONS, _ORIGINS
from routefrom_pipeline.database.restore_plan import _DESTINATION
from routefrom_pipeline.ingest.linggan import EXPECTED_COLUMNS
from routefrom_pipeline.library_worker import claim_job
from routefrom_pipeline.library_worker import run_once as run_processing
from routefrom_pipeline.private_storage import private_archive_path, private_csv_path
from tests import test_backup_archive as archive_fixtures
from tests.test_backup_archive import map_config


class RestoreSqlTests(unittest.TestCase):
    def test_both_claimers_recheck_other_queue_after_locking_the_library(self):
        archive_row = (uuid4(), uuid4(), uuid4(), "backup", None, None, None, None)
        normal_row = (uuid4(), uuid4(), uuid4(), None, "reprocess", {})
        for claim, row in ((claim_archive_job, archive_row), (claim_job, normal_row)):
            connection = MagicMock()
            first = MagicMock()
            first.fetchone.return_value = row
            second = MagicMock()
            second.fetchone.return_value = (1,)
            connection.execute.side_effect = [first, second]
            self.assertIsNone(claim(connection))
            statements = [call.args[0] for call in connection.execute.call_args_list]
            self.assertIn("FOR UPDATE", statements[0])
            self.assertEqual(len(statements), 2)
            self.assertNotIn("UPDATE app.", statements[1])

    def test_restore_queries_parse(self):
        for template in (_OBSERVATIONS, _ORIGINS, _DESTINATION):
            sql = template
            position = 1
            while "%s" in sql:
                sql = sql.replace("%s", f"${position}", 1)
                position += 1
            self.assertEqual(len(parse_sql(sql)), 1)


@unittest.skipUnless(
    os.environ.get("ROUTEFROM_RUN_DATABASE_TESTS") == "true", "requires isolated PostGIS"
)
class ArchiveWorkerTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        database = os.environ.get("ROUTEFROM_TEST_DATABASE", "routefrom_integration")
        if not database.startswith("routefrom_integration"):
            raise ValueError("isolated integration database required")
        self.connection = psycopg.connect(os.environ["DATABASE_URL_DIRECT"], dbname=database)
        self.addCleanup(self.connection.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source_user, self.source_library = self.account()
        self.target_user, self.target_library = self.account()

    def account(self):
        user, library = uuid4(), uuid4()
        self.connection.execute("INSERT INTO app.users(id) VALUES (%s)", (user,))
        self.connection.execute(
            "INSERT INTO app.footprint_libraries(id,user_id) VALUES (%s,%s)", (library, user)
        )
        return user, library

    def source_backup(self):
        self.connection.execute(
            "UPDATE app.footprint_libraries SET name='来源库' WHERE id=%s", (self.source_library,)
        )
        self.connection.execute(
            "UPDATE app.users SET timezone='Europe/London' WHERE id=%s", (self.source_user,)
        )
        key = f"raw/{self.source_user}/{uuid4()}.csv"
        path = private_csv_path(self.root, key)
        path.parent.mkdir(parents=True)
        body = ",".join(EXPECTED_COLUMNS) + "\n"
        for index in range(3):
            body += f"{1682028286912 + index * 10000},30.9,{118.7 + index * 0.0001},30.9,{118.7 + index * 0.0001},50,-1,5,-1,1,1682006400000,0,,1\n"
        path.write_bytes(body.encode())
        dataset = self.connection.execute(
            """INSERT INTO app.datasets(user_id,original_filename,source_object_key,source_sha256,source_size_bytes)
          VALUES (%s,'source.csv',%s,%s,%s) RETURNING id""",
            (self.source_user, key, hashlib.sha256(body.encode()).hexdigest(), len(body.encode())),
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO app.library_jobs(library_id,user_id,dataset_id,kind) VALUES (%s,%s,%s,'import')",
            (self.source_library, self.source_user, dataset),
        )
        self.assertTrue(run_processing(self.connection, self.root, library_id=self.source_library))
        point = self.connection.execute(
            "SELECT id,source_id FROM app.library_observations WHERE library_id=%s ORDER BY geo_time_epoch_ms LIMIT 1",
            (self.source_library,),
        ).fetchone()
        self.source_note = self.connection.execute(
            """INSERT INTO app.library_place_notes(library_id,user_id,label,position)
          VALUES (%s,%s,'来源备注',ST_SetSRID(ST_Point(118.7,30.9),4326)) RETURNING id""",
            (self.source_library, self.source_user),
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO app.library_place_note_anchors VALUES (%s,%s,%s,%s)",
            (self.source_note, self.source_library, point[1], point[0]),
        )
        config = json.dumps(map_config())
        self.connection.execute(
            "INSERT INTO app.library_workspace_state(library_id,user_id,revision,config) VALUES (%s,%s,1,%s::jsonb)",
            (self.source_library, self.source_user, config),
        )
        self.connection.execute(
            "INSERT INTO app.library_saved_views(library_id,user_id,title,config) VALUES (%s,%s,'来源视图',%s::jsonb)",
            (self.source_library, self.source_user, config),
        )
        job = self.connection.execute(
            "INSERT INTO app.library_archive_jobs(library_id,user_id,kind) VALUES (%s,%s,'backup') RETURNING id",
            (self.source_library, self.source_user),
        ).fetchone()[0]
        self.assertTrue(run_archive(self.connection, self.root, library_id=self.source_library))
        status, key, digest, size = self.connection.execute(
            "SELECT status,output_key,output_sha256,output_size_bytes FROM app.library_archive_jobs WHERE id=%s",
            (job,),
        ).fetchone()
        self.assertEqual(status, "succeeded")
        self.archive_bytes = private_archive_path(self.root, key).read_bytes()
        self.assertEqual(hashlib.sha256(self.archive_bytes).hexdigest(), digest)
        self.assertEqual(len(self.archive_bytes), size)
        return self.new_restore()

    def new_restore(self):
        key = f"archives/{self.target_user}/{uuid4()}.zip"
        path = private_archive_path(self.root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.archive_bytes)
        return self.connection.execute(
            """INSERT INTO app.library_archive_jobs(library_id,user_id,kind,input_key,input_sha256,input_size_bytes)
          VALUES (%s,%s,'restore',%s,%s,%s) RETURNING id""",
            (
                self.target_library,
                self.target_user,
                key,
                hashlib.sha256(self.archive_bytes).hexdigest(),
                len(self.archive_bytes),
            ),
        ).fetchone()[0]

    def archive_status(self, job):
        return self.connection.execute(
            "SELECT status,plan,error_code,processing_job_id FROM app.library_archive_jobs WHERE id=%s",
            (job,),
        ).fetchone()

    def review(self, job):
        self.assertTrue(run_archive(self.connection, self.root, library_id=self.target_library))
        status, plan, error, _ = self.archive_status(job)
        self.assertEqual(status, "awaiting_review", error)
        with self.assertRaises(ValueError):
            approve_restore(
                self.connection,
                job_id=job,
                library_id=self.target_library,
                user_id=self.source_user,
                state_hash=plan["stateHash"],
            )
        approve_restore(
            self.connection,
            job_id=job,
            library_id=self.target_library,
            user_id=self.target_user,
            state_hash=plan["stateHash"],
        )
        approve_restore(
            self.connection,
            job_id=job,
            library_id=self.target_library,
            user_id=self.target_user,
            state_hash=plan["stateHash"],
        )

    def apply(self, job):
        self.assertTrue(run_archive(self.connection, self.root, library_id=self.target_library))
        state = self.archive_status(job)
        self.assertEqual(state[0], "processing", state[2])
        self.assertIsNotNone(state[3])
        self.assertTrue(run_processing(self.connection, self.root, library_id=self.target_library))
        self.assertTrue(run_archive(self.connection, self.root, library_id=self.target_library))
        self.assertEqual(self.archive_status(job)[0], "succeeded")

    def counts(self):
        return tuple(
            self.connection.execute(
                f"SELECT count(*) FROM app.{table} WHERE library_id=%s", (self.target_library,)
            ).fetchone()[0]
            for table in ("library_files", "library_place_notes", "library_saved_views")
        )

    def test_restore_is_reviewed_published_and_idempotent_after_manual_edits(self):
        job = self.source_backup()
        self.review(job)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.apply(job)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(
            self.connection.execute(
                "SELECT name FROM app.footprint_libraries WHERE id=%s", (self.target_library,)
            ).fetchone()[0],
            "来源库",
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT timezone FROM app.users WHERE id=%s", (self.target_user,)
            ).fetchone()[0],
            "Europe/London",
        )
        target_note = self.connection.execute(
            "SELECT id FROM app.library_place_notes WHERE library_id=%s", (self.target_library,)
        ).fetchone()[0]
        self.assertNotEqual(target_note, self.source_note)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM app.library_place_note_anchors WHERE library_id=%s",
                (self.target_library,),
            ).fetchone()[0],
            1,
        )
        self.connection.execute(
            "UPDATE app.library_place_notes SET label='本机人工修改',revision=revision+1 WHERE id=%s",
            (target_note,),
        )
        self.connection.execute(
            "DELETE FROM app.library_saved_views WHERE library_id=%s", (self.target_library,)
        )
        again = self.new_restore()
        self.review(again)
        self.apply(again)
        self.assertEqual(self.counts(), (1, 1, 0))
        self.assertEqual(
            self.connection.execute(
                "SELECT label FROM app.library_place_notes WHERE id=%s", (target_note,)
            ).fetchone()[0],
            "本机人工修改",
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT label FROM app.library_place_notes WHERE id=%s", (self.source_note,)
            ).fetchone()[0],
            "来源备注",
        )

    def test_changed_review_and_failed_merge_leave_the_published_library_unchanged(self):
        job = self.source_backup()
        self.review(job)
        self.connection.execute(
            "UPDATE app.footprint_libraries SET name='在确认后改名' WHERE id=%s",
            (self.target_library,),
        )
        self.assertTrue(run_archive(self.connection, self.root, library_id=self.target_library))
        self.assertEqual(self.archive_status(job)[0], "awaiting_review")
        plan = self.archive_status(job)[1]
        approve_restore(
            self.connection,
            job_id=job,
            library_id=self.target_library,
            user_id=self.target_user,
            state_hash=plan["stateHash"],
        )
        self.connection.execute(
            """INSERT INTO app.library_jobs(library_id,user_id,kind,status)
          VALUES (%s,%s,'reprocess','awaiting_resolution')""",
            (self.target_library, self.target_user),
        )
        self.assertTrue(run_archive(self.connection, self.root, library_id=self.target_library))
        self.assertEqual(self.archive_status(job)[0], "awaiting_review")
        updated_plan = self.archive_status(job)[1]
        self.assertNotEqual(updated_plan["stateHash"], plan["stateHash"])
        approve_restore(
            self.connection,
            job_id=job,
            library_id=self.target_library,
            user_id=self.target_user,
            state_hash=updated_plan["stateHash"],
        )
        with patch(
            "routefrom_pipeline.database.restore_library.restore_human_state",
            side_effect=RuntimeError("injected failure after observations"),
        ):
            self.assertTrue(run_archive(self.connection, self.root, library_id=self.target_library))
        self.assertEqual(self.archive_status(job)[0], "failed")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertIsNone(
            self.connection.execute(
                "SELECT active_snapshot_id FROM app.footprint_libraries WHERE id=%s",
                (self.target_library,),
            ).fetchone()[0]
        )
        self.connection.execute(
            "UPDATE app.library_archive_jobs SET status='queued' WHERE id=%s", (job,)
        )
        self.apply(job)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_conflict_choices_withdrawn_and_invalid_originals_and_existing_view_survive_restore(
        self,
    ):
        fixture = archive_fixtures.BackupArchiveTests(
            "test_original_bytes_notes_configs_withdrawn_and_failed_files_roundtrip"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.manifest["files"][1].update(status="active", withdrawnAt=None)
        inactive = dict(fixture.manifest["files"][0])
        origin_id = str(uuid4())
        original_bytes = fixture.paths[inactive["id"]].read_bytes()
        inactive_bytes = original_bytes.replace(b"1682028286912", b"1682028186912")
        inactive.update(
            id=origin_id,
            member=f"raw/{origin_id}.csv",
            status="withdrawn",
            withdrawnAt=inactive["addedAt"],
            size=len(inactive_bytes),
            sha256=hashlib.sha256(inactive_bytes).hexdigest(),
        )
        path = fixture.root / f"{origin_id}.csv"
        path.write_bytes(inactive_bytes)
        fixture.paths[origin_id] = path
        fixture.manifest["files"].append(inactive)
        write_backup(fixture.archive, fixture.manifest, fixture.paths)
        self.archive_bytes = fixture.archive.read_bytes()
        job = self.new_restore()
        self.review(job)
        self.apply(job)
        self.assertEqual(self.counts(), (3, 2, 1))
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM app.library_files WHERE library_id=%s AND status='withdrawn'",
                (self.target_library,),
            ).fetchone()[0],
            1,
        )
        snapshot = self.connection.execute(
            """SELECT snapshot.point_count,snapshot.decisions FROM app.library_snapshots snapshot
          JOIN app.footprint_libraries library ON library.active_snapshot_id=snapshot.id WHERE library.id=%s""",
            (self.target_library,),
        ).fetchone()
        self.assertEqual(snapshot[0], 2)
        self.assertEqual(len(snapshot[1]), 1)
        self.assertNotEqual(
            next(iter(snapshot[1].values())), fixture.manifest["decisions"][0]["fingerprint"]
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM app.library_jobs WHERE library_id=%s AND stage='restored_original' AND status='failed'",
                (self.target_library,),
            ).fetchone()[0],
            1,
        )
        edited = map_config()
        edited["map"]["camera"]["zoom"] = 5
        self.connection.execute(
            "UPDATE app.library_workspace_state SET config=%s::jsonb,revision=revision+1 WHERE library_id=%s",
            (json.dumps(edited), self.target_library),
        )
        epoch, previous_choice = next(iter(snapshot[1].items()))
        pending_choice = self.connection.execute(
            "SELECT fingerprint FROM app.library_observations WHERE library_id=%s AND geo_time_epoch_ms=%s AND fingerprint<>%s LIMIT 1",
            (self.target_library, int(epoch), previous_choice),
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO app.library_jobs(library_id,user_id,kind,status,resolutions)
          VALUES (%s,%s,'reprocess','awaiting_resolution',%s::jsonb)""",
            (self.target_library, self.target_user, json.dumps({epoch: pending_choice})),
        )
        again = self.new_restore()
        self.review(again)
        self.apply(again)
        self.assertEqual(self.counts(), (3, 2, 1))
        self.assertEqual(
            self.connection.execute(
                """SELECT snapshot.decisions FROM app.library_snapshots snapshot
              JOIN app.footprint_libraries library ON library.active_snapshot_id=snapshot.id WHERE library.id=%s""",
                (self.target_library,),
            ).fetchone()[0][epoch],
            pending_choice,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT config FROM app.library_workspace_state WHERE library_id=%s",
                (self.target_library,),
            ).fetchone()[0],
            edited,
        )

    def test_leases_and_shared_claim_lock_prevent_cross_queue_or_stale_publication(self):
        job_id = self.connection.execute(
            "INSERT INTO app.library_archive_jobs(library_id,user_id,kind) VALUES (%s,%s,'backup') RETURNING id",
            (self.target_library, self.target_user),
        ).fetchone()[0]
        job = claim_archive_job(self.connection, self.target_library)
        self.assertEqual(job.id, job_id)
        self.connection.execute(
            "INSERT INTO app.library_jobs(library_id,user_id,kind) VALUES (%s,%s,'reprocess')",
            (self.target_library, self.target_user),
        )
        self.assertIsNone(claim_job(self.connection, library_id=self.target_library))
        self.connection.execute(
            "UPDATE app.library_archive_jobs SET worker_token=%s WHERE id=%s", (uuid4(), job_id)
        )
        with self.assertRaises(ArchiveLeaseLost):
            process_archive_job(self.connection, job, self.root)
        self.assertEqual(self.archive_status(job_id)[0], "running")
        self.assertIsNone(
            self.connection.execute(
                "SELECT output_key FROM app.library_archive_jobs WHERE id=%s", (job_id,)
            ).fetchone()[0]
        )

    def test_concurrent_connections_cannot_claim_both_queues_for_one_library(self):
        import psycopg

        database = os.environ.get("ROUTEFROM_TEST_DATABASE", "routefrom_integration")
        options = {
            "dbname": database,
            "autocommit": True,
            "connect_timeout": 15,
            "options": "-c statement_timeout=15000",
            "application_name": "routefrom-archive-claim-test",
        }
        owner, library = uuid4(), uuid4()
        with (
            psycopg.connect(os.environ["DATABASE_URL_DIRECT"], **options) as control,
            psycopg.connect(os.environ["DATABASE_URL_DIRECT"], **options) as normal,
            psycopg.connect(os.environ["DATABASE_URL_DIRECT"], **options) as archive,
        ):
            control.execute(
                "INSERT INTO app.users(id,display_name) VALUES (%s,'Archive claim race test')",
                (owner,),
            )
            try:
                control.execute(
                    "INSERT INTO app.footprint_libraries(id,user_id) VALUES (%s,%s)",
                    (library, owner),
                )
                control.execute(
                    "INSERT INTO app.library_jobs(library_id,user_id,kind) VALUES (%s,%s,'reprocess')",
                    (library, owner),
                )
                control.execute(
                    "INSERT INTO app.library_archive_jobs(library_id,user_id,kind) VALUES (%s,%s,'backup')",
                    (library, owner),
                )
                with ThreadPoolExecutor(max_workers=2) as pool:
                    for _ in range(8):
                        control.execute(
                            "UPDATE app.library_jobs SET status='queued',worker_token=NULL,lease_until=NULL WHERE library_id=%s",
                            (library,),
                        )
                        control.execute(
                            "UPDATE app.library_archive_jobs SET status='queued',worker_token=NULL,lease_until=NULL WHERE library_id=%s",
                            (library,),
                        )
                        barrier = threading.Barrier(2)

                        def processing_claim(barrier=barrier):
                            barrier.wait(timeout=10)
                            return claim_job(normal, library_id=library)

                        def archive_claim(barrier=barrier):
                            barrier.wait(timeout=10)
                            return claim_archive_job(archive, library)

                        first = pool.submit(processing_claim)
                        second = pool.submit(archive_claim)
                        first.result(timeout=30)
                        second.result(timeout=30)
                        count = control.execute(
                            """SELECT (SELECT count(*) FROM app.library_jobs WHERE library_id=%s AND status='running')+
                          (SELECT count(*) FROM app.library_archive_jobs WHERE library_id=%s AND status='running')""",
                            (library, library),
                        ).fetchone()[0]
                        self.assertEqual(count, 1)
            finally:
                control.execute(
                    "DELETE FROM app.library_archive_jobs WHERE library_id=%s AND user_id=%s",
                    (library, owner),
                )
                control.execute(
                    "DELETE FROM app.library_jobs WHERE library_id=%s AND user_id=%s",
                    (library, owner),
                )
                control.execute(
                    "DELETE FROM app.footprint_libraries WHERE id=%s AND user_id=%s",
                    (library, owner),
                )
                control.execute(
                    "DELETE FROM app.users WHERE id=%s AND display_name='Archive claim race test'",
                    (owner,),
                )
