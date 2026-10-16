from __future__ import annotations

import contextlib
import copy
import csv
import hashlib
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from routefrom_pipeline.backup_archive import remap_backup_evidence, verify_backup, write_backup
from routefrom_pipeline.backup_manifest import (
    FORMAT,
    BackupError,
    BackupLimits,
    validate_manifest,
    validate_map_config,
)
from routefrom_pipeline.ingest.library import IDENTITY_VERSION, observation_fingerprint
from routefrom_pipeline.ingest.linggan import (
    EXPECTED_COLUMNS,
    iter_linggan_csv,
    iter_linggan_stream,
)
from routefrom_pipeline.private_storage import private_csv_path

NOW = "2026-10-16T08:00:00+00:00"


def map_config():
    return {
        "schemaVersion": 1,
        "map": {
            "projection": "globe",
            "style": "openfreemap-dark-v1",
            "terrain": False,
            "buildings": False,
            "camera": {"longitude": 105, "latitude": 28, "zoom": 1.5, "pitch": 0, "bearing": 0},
        },
        "ranges": [
            {"start": "2026-01-01T03:00:01Z", "end": "2026-01-01T03:01:02Z"},
            {"start": "2026-05-05T02:03:00+08:00", "end": "2026-05-05T08:03:00+08:00"},
        ],
        "visibleLayers": {
            "track": True,
            "sparse": True,
            "highSpeed": False,
            "stays": False,
            "gaps": True,
            "places": True,
        },
        "panels": {"layers": True, "inspector": False},
    }


class BackupArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.sources = [str(uuid4()), str(uuid4())]
        self.paths = {}
        self.blobs = {}
        files = []
        for index, status in enumerate(("active", "withdrawn", "unprocessed")):
            file_id = str(uuid4())
            handle = io.StringIO(newline="")
            writer = csv.writer(handle)
            writer.writerow(EXPECTED_COLUMNS)
            for offset in range(2 if index == 0 else 1):
                writer.writerow(
                    [
                        1682028286912 + offset * 10000,
                        30.9,
                        118.7000000000001 + offset * 0.0000000000001,
                        30.9,
                        118.7000000000001 + offset * 0.0000000000001,
                        50.46,
                        -1,
                        -1,
                        -1,
                        -1,
                        1682006400000,
                        0,
                        '中文,"WiFi"\nnetwork',
                        1,
                    ]
                )
            blob = (
                ("\ufeff" + handle.getvalue()).encode("utf-8")
                if index < 2
                else b"invalid,original,csv\r\n"
            )
            target = self.root / f"{file_id}.csv"
            target.write_bytes(blob)
            self.paths[file_id] = target
            self.blobs[f"raw/{file_id}.csv"] = blob
            files.append(
                {
                    "id": file_id,
                    "sourceId": self.sources[index] if index < 2 else None,
                    "status": status,
                    "datasetStatus": "failed" if index == 2 else "ready",
                    "jobStatus": "failed" if index == 2 else "succeeded",
                    "name": f"原始记录-{index}.csv",
                    "size": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "member": f"raw/{file_id}.csv",
                    "uploadedAt": NOW,
                    "addedAt": NOW if index < 2 else None,
                    "withdrawnAt": NOW if index == 1 else None,
                }
            )
        self.points = [list(iter_linggan_csv(self.paths[file["id"]])) for file in files[:2]]
        anchors = [
            {"sourceId": source, "fingerprint": observation_fingerprint(points[0], source)}
            for source, points in zip(self.sources, self.points)
        ]
        self.manifest = {
            "format": FORMAT,
            "version": 1,
            "identityVersion": IDENTITY_VERSION,
            "createdAt": NOW,
            "library": {"name": "我的足迹", "timezone": "Asia/Shanghai", "revision": 7},
            "sources": [
                {
                    "id": source,
                    "key": f"device-{index}",
                    "name": f"设备 {index}",
                    "parser": "linggan_footprint_csv",
                    "createdAt": NOW,
                }
                for index, source in enumerate(self.sources)
            ],
            "files": files,
            "decisions": [{"epochMs": self.points[0][0].geo_time_epoch_ms, **anchors[0]}],
            "notes": [
                {
                    "id": str(uuid4()),
                    "label": "家",
                    "position": [118.7000000000001, 30.9],
                    "anchors": anchors,
                    "revision": 3,
                    "createdAt": NOW,
                    "updatedAt": NOW,
                },
                {
                    "id": str(uuid4()),
                    "label": "待确认旧地点",
                    "position": [118.7, 30.9],
                    "anchors": [],
                    "revision": 1,
                    "createdAt": NOW,
                    "updatedAt": NOW,
                },
            ],
            "workspace": {"config": map_config(), "revision": 4, "updatedAt": NOW},
            "views": [
                {
                    "id": str(uuid4()),
                    "title": "不连续的旅程",
                    "config": map_config(),
                    "createdAt": NOW,
                }
            ],
        }
        self.archive = self.root / "library.rfbackup.zip"

    def forged(self, manifest=None, overrides=None, extra=None):
        value = self.manifest if manifest is None else manifest
        data = (
            json.dumps(value, ensure_ascii=False).encode("utf-8")
            if not isinstance(value, bytes)
            else value
        )
        with warnings.catch_warnings(), zipfile.ZipFile(self.archive, "w") as archive:
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("manifest.json", data)
            for member, blob in self.blobs.items():
                archive.writestr(member, (overrides or {}).get(member, blob))
            if extra:
                for member, blob in extra:
                    archive.writestr(member, blob)

    def test_original_bytes_notes_configs_withdrawn_and_failed_files_roundtrip(self):
        result = write_backup(self.archive, self.manifest, self.paths)
        self.assertEqual(result.manifest, self.manifest)
        self.assertEqual(sorted(result.point_counts.values()), [1, 2])
        self.assertEqual(
            result.archive_sha256, hashlib.sha256(self.archive.read_bytes()).hexdigest()
        )
        with zipfile.ZipFile(self.archive) as archive:
            for name, blob in self.blobs.items():
                self.assertEqual(archive.read(name), blob)
            self.assertNotIn("auth_subject", archive.read("manifest.json").decode())
        self.assertEqual(len(result.references), 2)
        self.assertNotEqual(self.points[0][0].source_longitude, self.points[0][1].source_longitude)

    def test_exact_source_remap_rehashes_decisions_and_note_anchors(self):
        result = write_backup(self.archive, self.manifest, self.paths)
        targets = {source: str(uuid4()) for source in self.sources}
        mapped = remap_backup_evidence(result, targets)
        self.assertEqual(
            mapped["decisions"][self.points[0][0].geo_time_epoch_ms],
            observation_fingerprint(self.points[0][0], targets[self.sources[0]]),
        )
        self.assertNotEqual(
            mapped["decisions"][self.points[0][0].geo_time_epoch_ms],
            self.manifest["decisions"][0]["fingerprint"],
        )
        self.assertNotIn("id", mapped["notes"][0])
        self.assertEqual(mapped["notes"][0]["originId"], self.manifest["notes"][0]["id"])
        self.assertEqual(mapped["notes"][1]["anchors"], [])
        for anchor, source, points in zip(mapped["notes"][0]["anchors"], self.sources, self.points):
            self.assertEqual(
                anchor,
                {
                    "sourceId": targets[source],
                    "fingerprint": observation_fingerprint(points[0], targets[source]),
                },
            )
        with self.assertRaises(BackupError):
            remap_backup_evidence(result, {self.sources[0]: targets[self.sources[0]]})
        with self.assertRaises(BackupError):
            remap_backup_evidence(result, dict.fromkeys(self.sources, targets[self.sources[0]]))

    def test_nearby_position_cannot_replace_missing_fingerprint_evidence(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["notes"][0]["anchors"][0]["fingerprint"] = "0" * 64
        self.forged(manifest)
        with self.assertRaisesRegex(BackupError, "exact original evidence"):
            verify_backup(self.archive)
        with self.assertRaises(BackupError):
            write_backup(self.root / "incomplete.zip", manifest, self.paths)
        self.assertFalse((self.root / "incomplete.zip").exists())
        self.assertTrue(all(path.exists() for path in self.paths.values()))

    def test_wrong_epoch_decision_rejected_even_with_a_real_fingerprint(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["decisions"][0]["epochMs"] += 1
        self.forged(manifest)
        with self.assertRaisesRegex(BackupError, "timestamp"):
            verify_backup(self.archive)

    def test_changed_original_rejected_and_existing_outputs_never_overwritten(self):
        first = self.manifest["files"][0]
        blob = self.blobs[first["member"]]
        self.forged(overrides={first["member"]: b"X" + blob[1:]})
        with self.assertRaisesRegex(BackupError, "digest"):
            verify_backup(self.archive)
        self.archive.write_bytes(b"existing-user-backup")
        with self.assertRaises(FileExistsError):
            write_backup(self.archive, self.manifest, self.paths)
        self.assertEqual(self.archive.read_bytes(), b"existing-user-backup")
        self.paths[first["id"]].write_bytes(blob + b"changed")
        with self.assertRaises(BackupError):
            write_backup(self.root / "new.zip", self.manifest, self.paths)
        self.assertFalse((self.root / "new.zip").exists())

    def test_paths_unknown_files_symlinks_and_duplicate_zip_members_are_rejected(self):
        link = zipfile.ZipInfo("secret.csv")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in [
            [("../../.env.local", b"x")],
            [("manifest.json", b"{}")],
            [(link, b"../secret")],
        ]:
            with self.subTest(extra=extra):
                self.forged(extra=extra)
                with self.assertRaises(BackupError):
                    verify_backup(self.archive)
        manifest = copy.deepcopy(self.manifest)
        manifest["files"][0]["member"] = "../outside.csv"
        self.forged(manifest)
        with self.assertRaises(BackupError):
            verify_backup(self.archive)

    def test_limits_apply_to_directory_metadata_files_observations_and_total_bytes(self):
        write_backup(self.archive, self.manifest, self.paths)
        for limits in [
            replace(BackupLimits(), manifest_bytes=10),
            replace(BackupLimits(), file_bytes=1),
            replace(BackupLimits(), files=1),
            replace(BackupLimits(), total_bytes=1),
            replace(BackupLimits(), references=1),
            replace(BackupLimits(), points=1),
        ]:
            with self.subTest(limits=limits), self.assertRaises(BackupError):
                verify_backup(self.archive, limits=limits)
        data = bytearray(self.archive.read_bytes())
        struct.pack_into("<H", data, len(data) - 12, 60000)
        self.archive.write_bytes(data)
        with (
            patch(
                "routefrom_pipeline.backup_archive.zipfile.ZipFile",
                side_effect=AssertionError("unbounded directory parsed"),
            ),
            self.assertRaises(BackupError),
        ):
            verify_backup(self.archive)

    def test_strict_metadata_rejects_auth_fields_duplicate_json_nonfinite_and_wrong_types(self):
        for mutate in [
            lambda m: m.update(auth={"password": "not-allowed"}),
            lambda m: m.update(version=True),
            lambda m: m["notes"][0].update(label="🌍" * 41),
            lambda m: m["workspace"]["config"]["map"]["camera"].update(zoom=float("nan")),
            lambda m: m["workspace"]["config"]["panels"].update(inspector=1),
            lambda m: m["workspace"]["config"]["ranges"][0].update(start="2026-01-01T00:00:00"),
        ]:
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(BackupError):
                validate_manifest(manifest)
        duplicate = b'{"version":1,' + json.dumps(self.manifest).encode()[1:]
        self.forged(duplicate)
        with self.assertRaisesRegex(BackupError, "duplicate JSON"):
            verify_backup(self.archive)

    def test_empty_library_and_orphan_notes_are_not_silently_dropped(self):
        manifest = copy.deepcopy(self.manifest)
        manifest.update(sources=[], files=[], decisions=[], notes=[manifest["notes"][1]])
        result = write_backup(self.archive, manifest, {})
        self.assertEqual(result.manifest["notes"], manifest["notes"])
        self.assertEqual(result.point_counts, {})

    def test_stream_parser_is_the_same_parser_as_file_import(self):
        file = self.manifest["files"][0]
        with io.TextIOWrapper(
            io.BytesIO(self.blobs[file["member"]]), encoding="utf-8-sig", newline=""
        ) as stream:
            self.assertEqual(list(iter_linggan_stream(stream)), self.points[0])

    def test_private_storage_rejects_links_to_another_asset_even_inside_the_root(self):
        owner = str(uuid4())
        asset = str(uuid4())
        link = self.root / "raw" / owner
        target = self.root / "other-assets"
        target.mkdir()
        (target / f"{asset}.csv").write_bytes(b"not this owner's asset")
        link.parent.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("this OS account cannot create directory symlinks")
            # Windows directory junctions do not need the symlink privilege.
            # Both explicit paths are this test's own temporary fixture children.
            escaped_link = str(link).replace("'", "''")
            escaped_target = str(target).replace("'", "''")
            command = f"New-Item -ItemType Junction -Path '{escaped_link}' -Target '{escaped_target}' | Out-Null"
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if result.returncode:
                self.skipTest("this OS account cannot create directory junctions")
        with self.assertRaisesRegex(ValueError, "redirect"):
            private_csv_path(self.root, f"raw/{owner}/{asset}.csv")

    def test_cli_verifies_without_database_and_reports_no_private_labels_or_coordinates(self):
        from routefrom_pipeline.backup import main

        write_backup(self.archive, self.manifest, self.paths)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["verify", str(self.archive)]), 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["files"], 3)
        self.assertTrue(summary["verified"])
        self.assertNotIn("118.7", output.getvalue())
        self.assertNotIn("label", summary)

    def test_shared_website_map_config_wire_fixtures(self):
        path = Path(__file__).resolve().parents[2] / "contracts" / "map-view-v1-fixtures.json"
        fixtures = json.loads(path.read_text(encoding="utf-8"))
        validate_map_config(fixtures["base"])
        for item in fixtures["cases"]:
            config = copy.deepcopy(fixtures["base"])
            target = config
            for key in item["path"][:-1]:
                target = target[key]
            target[item["path"][-1]] = item["value"]
            with self.subTest(path=item["path"]):
                if item["valid"]:
                    validate_map_config(config)
                else:
                    with self.assertRaises(BackupError):
                        validate_map_config(config)

    def test_archive_mutation_during_evidence_scan_cannot_return_a_verified_digest(self):
        write_backup(self.archive, self.manifest, self.paths)
        changed = False

        def scan_and_change(stream):
            nonlocal changed
            yield from iter_linggan_stream(stream)
            if not changed:
                with self.archive.open("ab") as handle:
                    handle.write(b"changed-during-scan")
                changed = True

        with (
            patch("routefrom_pipeline.backup_archive.iter_linggan_stream", scan_and_change),
            self.assertRaisesRegex(BackupError, "changed during verification"),
        ):
            verify_backup(self.archive)

    def test_operator_export_uses_autocommit_and_does_not_print_connection_credentials(self):
        from routefrom_pipeline.backup import main

        verified = write_backup(self.archive, self.manifest, self.paths)
        output = io.StringIO()
        url = "postgresql://test.invalid/example"
        connect = MagicMock()
        with (
            patch.dict(os.environ, {"DATABASE_URL_DIRECT": url}),
            patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}),
            patch(
                "routefrom_pipeline.database.backups.export_library_backup", return_value=verified
            ),
            contextlib.redirect_stdout(output),
        ):
            code = main(
                [
                    "export",
                    "--user-id",
                    str(uuid4()),
                    "--library-id",
                    str(uuid4()),
                    "--data-dir",
                    str(self.root),
                    "--output",
                    str(self.root / "operator.zip"),
                ]
            )
            self.assertEqual(code, 0)
            connect.assert_called_once_with(url, autocommit=True)
        self.assertNotIn(url, output.getvalue())
