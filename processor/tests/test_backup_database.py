from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from pglast import parse_sql

from routefrom_pipeline.backup_archive import remap_backup_evidence, verify_backup
from routefrom_pipeline.backup_manifest import BackupError
from routefrom_pipeline.database.backups import (
    _READ_BACKUP,
    export_library_backup,
    read_backup_snapshot,
)
from routefrom_pipeline.database.library import withdraw_library_file
from routefrom_pipeline.ingest.linggan import EXPECTED_COLUMNS
from routefrom_pipeline.library_worker import private_path, run_once
from tests.test_backup_archive import map_config


class BackupSqlTests(unittest.TestCase):
    def test_owner_scoped_snapshot_is_one_valid_postgres_statement(self):
        sql = _READ_BACKUP.replace("%s", "$1", 1).replace("%s", "$2", 1)
        self.assertEqual(len(parse_sql(sql)), 1)


@unittest.skipUnless(
    os.environ.get("ROUTEFROM_RUN_DATABASE_TESTS") == "true", "requires isolated PostGIS"
)
class BackupDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        database = os.environ.get("ROUTEFROM_TEST_DATABASE", "routefrom_integration")
        if not database.startswith("routefrom_integration"):
            raise ValueError("isolated integration database required")
        self.connection = psycopg.connect(os.environ["DATABASE_URL_DIRECT"], dbname=database)
        self.addCleanup(self.connection.close)  # all database fixtures roll back
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.user, self.library = uuid4(), uuid4()
        self.connection.execute("INSERT INTO app.users (id) VALUES (%s)", (self.user,))
        self.connection.execute(
            "INSERT INTO app.footprint_libraries (id,user_id) VALUES (%s,%s)",
            (self.library, self.user),
        )

    def enqueue(self, body):
        key = f"raw/{self.user}/{uuid4()}.csv"
        source = private_path(self.root, key)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(body)
        dataset = self.connection.execute(
            """INSERT INTO app.datasets (user_id,original_filename,source_object_key,source_sha256,source_size_bytes)
          VALUES (%s,'合成备份.csv',%s,%s,%s) RETURNING id""",
            (self.user, key, hashlib.sha256(body).hexdigest(), len(body)),
        ).fetchone()[0]
        job = self.connection.execute(
            "INSERT INTO app.library_jobs (library_id,user_id,dataset_id,kind) VALUES (%s,%s,%s,'import') RETURNING id",
            (self.library, self.user, dataset),
        ).fetchone()[0]
        return dataset, job

    def process(self):
        self.assertTrue(run_once(self.connection, self.root, library_id=self.library))

    def test_export_empty_library_refuses_foreign_owner_without_writing_output(self):
        path = self.root / "empty.zip"
        backup = export_library_backup(
            self.connection,
            user_id=self.user,
            library_id=self.library,
            data_root=self.root,
            output=path,
        )
        self.assertEqual(backup.manifest["files"], [])
        self.assertEqual(backup.manifest["library"]["name"], "我的足迹")
        with self.assertRaises(BackupError):
            export_library_backup(
                self.connection,
                user_id=uuid4(),
                library_id=self.library,
                data_root=self.root,
                output=self.root / "foreign.zip",
            )
        self.assertFalse((self.root / "foreign.zip").exists())

    def test_real_files_decisions_notes_configs_and_failed_originals_form_a_portable_archive(self):
        base = 1682028286912
        header = ",".join(EXPECTED_COLUMNS) + "\n"
        rows = lambda longitude: (
            f"{base},30.9,{longitude},30.9,{longitude},50,-1,5,-1,-1,1682006400000,0,,1\n"
        )
        first, _ = self.enqueue((header + rows("118.7000000000001")).encode())
        self.process()
        second, conflict_job = self.enqueue((header + rows("118.7000000000002")).encode())
        self.process()
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM app.library_jobs WHERE id=%s", (conflict_job,)
            ).fetchone()[0],
            "awaiting_resolution",
        )
        point = self.connection.execute(
            """SELECT observation.id, observation.source_id, observation.fingerprint
          FROM app.library_observations observation JOIN app.library_observation_origins origin ON origin.observation_id=observation.id
          WHERE origin.dataset_id=%s""",
            (first,),
        ).fetchone()
        self.connection.execute(
            "UPDATE app.library_jobs SET status='queued',resolutions=%s::jsonb WHERE id=%s",
            (json.dumps({str(base): point[2]}), conflict_job),
        )
        self.process()
        failed, _ = self.enqueue(b"not,a,valid,csv\n")
        self.process()
        pending, _ = self.enqueue(
            b"queued,original,csv\n"
        )  # backing up needn't destroy/wait for queued uploads
        note = self.connection.execute(
            """INSERT INTO app.library_place_notes (library_id,user_id,label,position)
          VALUES (%s,%s,'家',ST_SetSRID(ST_Point(118.7000000000001,30.9),4326)) RETURNING id""",
            (self.library, self.user),
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO app.library_place_note_anchors VALUES (%s,%s,%s,%s)",
            (note, self.library, point[1], point[0]),
        )
        config = json.dumps(map_config())
        self.connection.execute(
            "INSERT INTO app.library_workspace_state (library_id,user_id,revision,config) VALUES (%s,%s,3,%s::jsonb)",
            (self.library, self.user, config),
        )
        self.connection.execute(
            "INSERT INTO app.library_saved_views (library_id,user_id,title,config) VALUES (%s,%s,'合成视图',%s::jsonb)",
            (self.library, self.user, config),
        )
        file_id = self.connection.execute(
            "SELECT id FROM app.library_files WHERE dataset_id=%s", (first,)
        ).fetchone()[0]
        withdraw_library_file(
            self.connection, user_id=self.user, library_id=self.library, file_id=file_id
        )
        backup = export_library_backup(
            self.connection,
            user_id=self.user,
            library_id=self.library,
            data_root=self.root,
            output=self.root / "real.zip",
        )
        files = {file["id"]: file for file in backup.manifest["files"]}
        self.assertEqual(set(files), {str(first), str(second), str(failed), str(pending)})
        self.assertEqual(files[str(first)]["status"], "withdrawn")
        self.assertEqual(files[str(second)]["status"], "active")
        self.assertEqual(files[str(failed)]["status"], "unprocessed")
        self.assertEqual(files[str(failed)]["jobStatus"], "failed")
        self.assertEqual(files[str(pending)]["jobStatus"], "queued")
        self.assertEqual(files[str(pending)]["datasetStatus"], "pending")
        self.assertEqual(backup.manifest["notes"][0]["label"], "家")
        self.assertEqual(backup.manifest["workspace"]["config"], map_config())
        self.assertEqual(backup.manifest["views"][0]["title"], "合成视图")
        self.assertEqual(backup.manifest["decisions"][0]["fingerprint"], point[2])
        self.assertNotIn("raw/" + str(self.user), json.dumps(backup.manifest))
        self.assertEqual(verify_backup(self.root / "real.zip").manifest, backup.manifest)
        target = str(uuid4())
        portable = remap_backup_evidence(backup, {str(point[1]): target})
        self.assertNotEqual(portable["decisions"][base], point[2])
        self.assertEqual(portable["notes"][0]["anchors"][0]["sourceId"], target)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM app.library_jobs WHERE status='queued' AND library_id=%s",
                (self.library,),
            ).fetchone()[0],
            1,
        )
        self.connection.execute("UPDATE app.datasets SET source_storage='object' WHERE id=%s", (pending,))
        with self.assertRaisesRegex(BackupError, "storage backend"):
            read_backup_snapshot(self.connection, user_id=self.user, library_id=self.library)
        self.connection.execute("UPDATE app.datasets SET source_storage='local' WHERE id=%s", (pending,))
        # An unresolved/corrupt reference cannot be silently omitted by export.
        self.connection.execute(
            "UPDATE app.library_jobs SET resolutions=%s::jsonb WHERE dataset_id=%s",
            (json.dumps({str(base): "0" * 64}), pending),
        )
        with self.assertRaisesRegex(BackupError, "source evidence"):
            read_backup_snapshot(self.connection, user_id=self.user, library_id=self.library)
