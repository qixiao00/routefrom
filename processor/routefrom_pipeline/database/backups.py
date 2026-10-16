"""Read an owner-scoped, single-statement library backup snapshot, without auth secrets."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from routefrom_pipeline.backup_archive import write_backup
from routefrom_pipeline.backup_manifest import FORMAT, BackupError, validate_manifest
from routefrom_pipeline.ingest.library import IDENTITY_VERSION
from routefrom_pipeline.private_storage import private_csv_path

# A single statement means files, pending decisions, notes and views all use one
# MVCC snapshot. Runtime callers use autocommit; no library row lock or explicit
# long transaction is required while reading a ZIP. Test callers may wrap fixtures.
_READ_BACKUP = """
SELECT
  jsonb_build_object('name', library.name, 'timezone', owner.timezone, 'revision', library.revision),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('id', source.id, 'key', source.source_key,
    'name', source.name, 'parser', source.parser_name, 'createdAt', source.created_at) ORDER BY source.id)
    FROM app.library_sources AS source WHERE source.library_id = library.id), '[]'::jsonb),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('id', dataset.id, 'sourceId', file.source_id,
    'status', COALESCE(file.status, 'unprocessed'), 'datasetStatus', dataset.status,
    'jobStatus', (SELECT job.status FROM app.library_jobs AS job WHERE job.dataset_id = dataset.id
      AND job.library_id = library.id AND job.user_id = library.user_id AND job.kind = 'import'
      ORDER BY job.created_at DESC, job.id DESC LIMIT 1),
    'name', dataset.original_filename, 'size', dataset.source_size_bytes, 'sha256', dataset.source_sha256,
    'member', 'raw/' || dataset.id::text || '.csv', 'uploadedAt', dataset.uploaded_at,
    'addedAt', file.added_at, 'withdrawnAt', file.withdrawn_at,
    'privateKey', dataset.source_object_key, 'storage', dataset.source_storage) ORDER BY dataset.uploaded_at, dataset.id)
    FROM app.datasets AS dataset LEFT JOIN app.library_files AS file ON file.dataset_id = dataset.id
      AND file.library_id = library.id AND file.user_id = library.user_id
    WHERE dataset.user_id = library.user_id AND (dataset.source_object_key LIKE 'raw/%%'
      OR file.id IS NOT NULL OR EXISTS (SELECT 1 FROM app.library_jobs AS job
        WHERE job.dataset_id = dataset.id AND job.library_id = library.id
          AND job.user_id = library.user_id AND job.kind = 'import'))), '[]'::jsonb),
  COALESCE((SELECT jsonb_agg(snapshot.decisions) FROM app.library_snapshots AS snapshot
    WHERE snapshot.id = library.active_snapshot_id AND snapshot.library_id = library.id
      AND snapshot.user_id = library.user_id), '[]'::jsonb)
    || COALESCE((SELECT jsonb_agg(job.resolutions ORDER BY job.created_at, job.id)
      FROM app.library_jobs AS job WHERE job.library_id = library.id AND job.user_id = library.user_id
        AND job.status IN ('queued', 'running', 'awaiting_resolution')), '[]'::jsonb),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('id', note.id, 'label', note.label,
    'position', jsonb_build_array(ST_X(note.position), ST_Y(note.position)), 'revision', note.revision,
    'createdAt', note.created_at, 'updatedAt', note.updated_at,
    'anchors', COALESCE((SELECT jsonb_agg(jsonb_build_object('sourceId', observation.source_id,
      'fingerprint', observation.fingerprint) ORDER BY observation.id)
      FROM app.library_place_note_anchors AS anchor JOIN app.library_observations AS observation
        ON observation.id = anchor.observation_id AND observation.library_id = anchor.library_id
        AND observation.source_id = anchor.source_id
      WHERE anchor.note_id = note.id AND anchor.library_id = library.id), '[]'::jsonb))
    ORDER BY note.created_at, note.id) FROM app.library_place_notes AS note
    WHERE note.library_id = library.id AND note.user_id = library.user_id), '[]'::jsonb),
  (SELECT jsonb_build_object('config', state.config, 'revision', state.revision, 'updatedAt', state.updated_at)
    FROM app.library_workspace_state AS state WHERE state.library_id = library.id AND state.user_id = library.user_id),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('id', view.id, 'title', view.title,
    'config', view.config, 'createdAt', view.created_at) ORDER BY view.created_at, view.id)
    FROM app.library_saved_views AS view WHERE view.library_id = library.id AND view.user_id = library.user_id), '[]'::jsonb),
  COALESCE((SELECT jsonb_object_agg(observation.fingerprint, jsonb_build_object('sourceId', observation.source_id,
    'epochMs', observation.geo_time_epoch_ms, 'fingerprint', observation.fingerprint))
    FROM app.library_observations AS observation WHERE observation.library_id = library.id AND observation.fingerprint IN (
      SELECT decision.value FROM app.library_snapshots AS snapshot CROSS JOIN LATERAL jsonb_each_text(snapshot.decisions) AS decision
      WHERE snapshot.id = library.active_snapshot_id AND snapshot.library_id = library.id AND snapshot.user_id = library.user_id
      UNION
      SELECT decision.value FROM app.library_jobs AS job CROSS JOIN LATERAL jsonb_each_text(job.resolutions) AS decision
      WHERE job.library_id = library.id AND job.user_id = library.user_id
        AND job.status IN ('queued', 'running', 'awaiting_resolution'))), '{}'::jsonb)
FROM app.footprint_libraries AS library JOIN app.users AS owner ON owner.id = library.user_id
WHERE library.id = %s AND library.user_id = %s
"""


def read_backup_snapshot(connection, *, user_id: UUID, library_id: UUID):
    row = connection.execute(_READ_BACKUP, (library_id, user_id)).fetchone()
    if row is None:
        raise BackupError("library was not found for this owner")
    library, sources, files, decision_sets, notes, workspace, views, observations = row
    private_keys = {}
    for file in files:
        if file.pop("storage") != "local":
            raise BackupError("original storage backend is not supported by backup v1")
        private_keys[file["id"]] = file.pop("privateKey")
    effective = {}
    for decisions in decision_sets:
        if not isinstance(decisions, dict):
            raise BackupError("stored decisions are malformed")
        effective.update(decisions)
    portable = []
    for epoch, fingerprint in effective.items():
        evidence = observations.get(fingerprint)
        if not evidence or str(evidence["epochMs"]) != epoch:
            raise BackupError("stored decision has no matching source evidence")
        portable.append(evidence)
    manifest = {
        "format": FORMAT,
        "version": 1,
        "identityVersion": IDENTITY_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "library": library,
        "sources": sources,
        "files": files,
        "decisions": sorted(portable, key=lambda item: item["epochMs"]),
        "notes": notes,
        "workspace": workspace,
        "views": views,
    }
    validate_manifest(manifest)
    return manifest, private_keys


def export_library_backup(
    connection, *, user_id: UUID, library_id: UUID, data_root: Path, output: Path
):
    manifest, keys = read_backup_snapshot(connection, user_id=user_id, library_id=library_id)
    paths = {}
    for file_id, key in keys.items():
        if not key.startswith(f"raw/{user_id}/"):
            raise BackupError("original storage does not belong to the owner")
        paths[file_id] = private_csv_path(data_root, key)
    return write_backup(output, manifest, paths)
