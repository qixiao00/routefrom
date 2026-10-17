"""Restore preview and non-destructive, owner-scoped human-state receipts."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from routefrom_pipeline.backup_archive import remap_backup_evidence
from routefrom_pipeline.backup_manifest import BackupError


def semantic_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


_DESTINATION = """
SELECT jsonb_build_object('revision',library.revision,'snapshotId',library.active_snapshot_id,
  'name',library.name,'timezone',owner.timezone,
  'sources',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',source.id,'key',source.source_key,'name',source.name,'parser',source.parser_name) ORDER BY source.id)
    FROM app.library_sources source WHERE source.library_id=library.id),'[]'::jsonb),
  'files',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',file.id,'datasetId',file.dataset_id,'importId',file.dataset_import_id,
    'sourceId',file.source_id,'status',file.status,'sha256',dataset.source_sha256) ORDER BY file.id)
    FROM app.library_files file JOIN app.datasets dataset ON dataset.id=file.dataset_id AND dataset.user_id=file.user_id
    WHERE file.library_id=library.id AND file.user_id=library.user_id),'[]'::jsonb),
  'jobs',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',job.id,'kind',job.kind,'status',job.status,
    'datasetId',job.dataset_id,'resolutions',job.resolutions) ORDER BY job.created_at,job.id)
    FROM app.library_jobs job WHERE job.library_id=library.id AND job.user_id=library.user_id
      AND job.status IN ('queued','running','awaiting_resolution')),'[]'::jsonb),
  'notes',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',note.id,'revision',note.revision,'label',note.label,
    'position',jsonb_build_array(ST_X(note.position),ST_Y(note.position)),
    'anchors',COALESCE((SELECT jsonb_agg(jsonb_build_object('sourceId',obs.source_id,'fingerprint',obs.fingerprint) ORDER BY obs.source_id,obs.fingerprint)
      FROM app.library_place_note_anchors anchor JOIN app.library_observations obs ON obs.id=anchor.observation_id AND obs.library_id=anchor.library_id AND obs.source_id=anchor.source_id
      WHERE anchor.note_id=note.id AND anchor.library_id=library.id),'[]'::jsonb)) ORDER BY note.id)
    FROM app.library_place_notes note WHERE note.library_id=library.id AND note.user_id=library.user_id),'[]'::jsonb),
  'workspace',(SELECT jsonb_build_object('revision',state.revision,'config',state.config) FROM app.library_workspace_state state WHERE state.library_id=library.id AND state.user_id=library.user_id),
  'views',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',view.id,'title',view.title,'config',view.config) ORDER BY view.id)
    FROM app.library_saved_views view WHERE view.library_id=library.id AND view.user_id=library.user_id),'[]'::jsonb),
  'receipts',COALESCE((SELECT jsonb_agg(jsonb_build_object('kind',receipt.object_kind,'originId',receipt.origin_id,
    'targetId',receipt.target_id,'hash',receipt.imported_hash) ORDER BY receipt.object_kind,receipt.origin_id)
    FROM app.library_restore_receipts receipt WHERE receipt.library_id=library.id AND receipt.user_id=library.user_id),'[]'::jsonb))
FROM app.footprint_libraries library JOIN app.users owner ON owner.id=library.user_id
WHERE library.id=%s AND library.user_id=%s
"""


def destination_state(connection, job):
    with connection.transaction():
        row = connection.execute(_DESTINATION, (job.library_id, job.user_id)).fetchone()
    if not row:
        raise BackupError("destination library does not belong to this account")
    return row[0]


def build_restore_plan(connection, job, backup):
    state = destination_state(connection, job)
    sources = {source["id"]: source for source in state["sources"]}
    by_key = {source["key"]: source for source in state["sources"]}
    by_hash = {file["sha256"]: file for file in state["files"]}
    mapping = {}
    definitions = []
    for source in backup.manifest["sources"]:
        existing = {
            by_hash[file["sha256"]]["sourceId"]
            for file in backup.manifest["files"]
            if file["sourceId"] == source["id"] and file["sha256"] in by_hash
        }
        if len(existing) > 1:
            raise BackupError("one archive source maps to several existing recording sources")
        target = sources[next(iter(existing))] if existing else by_key.get(source["key"])
        if target and target["parser"] != source["parser"]:
            raise BackupError("recording source parser conflict")
        target_id = target["id"] if target else str(uuid4())
        mapping[source["id"]] = target_id
        definitions.append(
            {
                "id": target_id,
                "key": target["key"] if target else source["key"],
                "name": target["name"] if target else source["name"],
                "existing": target is not None,
                "createdAt": source["createdAt"],
            }
        )
    if len(set(mapping.values())) != len(mapping):
        raise BackupError("different recording sources cannot be silently collapsed")
    plan = {
        "stateHash": semantic_hash(state),
        "policy": "append-preserve-existing",
        "files": [
            {
                "name": file["name"],
                "status": file["status"],
                "action": "keep_existing" if file["sha256"] in by_hash else "add",
            }
            for file in backup.manifest["files"]
        ],
        "notes": len(backup.manifest["notes"]),
        "views": len(backup.manifest["views"]),
        "existingNotes": len(state["notes"]),
        "existingViews": len(state["views"]),
        "preserveCurrentWorkspace": state["workspace"] is not None,
        "sourceCount": len(mapping),
        "librarySettings": {
            "name": backup.manifest["library"]["name"],
            "timezone": backup.manifest["library"]["timezone"],
            "action": "restore_defaults"
            if (
                not state["files"]
                and not state["notes"]
                and not state["views"]
                and state["workspace"] is None
                and state["name"] == "我的足迹"
                and state["timezone"] == "Asia/Shanghai"
            )
            else "keep_existing",
        },
    }
    return plan, state, mapping, definitions


def note_content(note):
    return {
        "label": note["label"],
        "position": note["position"],
        "anchors": sorted(
            note["anchors"], key=lambda anchor: (anchor["sourceId"], anchor["fingerprint"])
        ),
    }


def restore_human_state(connection, job, backup, mapping, state):
    """Caller holds the destination library lock and an active archive lease."""
    evidence = remap_backup_evidence(backup, mapping)
    receipts = {(row["kind"], row["originId"]): row for row in state["receipts"]}
    existing_notes = {note["id"]: note for note in state["notes"]}
    note_count, view_count = len(state["notes"]), len(state["views"])
    added_notes = added_views = 0

    def remember(kind, origin_id, target_id, digest):
        connection.execute(
            """INSERT INTO app.library_restore_receipts(library_id,user_id,object_kind,origin_id,target_id,imported_hash)
          VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(library_id,object_kind,origin_id) DO UPDATE
          SET target_id=EXCLUDED.target_id,imported_hash=EXCLUDED.imported_hash,updated_at=clock_timestamp()""",
            (job.library_id, job.user_id, kind, origin_id, target_id, digest),
        )

    for original, note in zip(backup.manifest["notes"], evidence["notes"]):
        digest = semantic_hash(note_content(original))
        previous = receipts.get(("note", note["originId"]))
        # Same source content is already handled, even if the user renamed or
        # deleted the destination note afterwards. Never resurrect it silently.
        if previous and previous["hash"] == digest:
            continue
        own = existing_notes.get(note["originId"])
        if not previous and own and note_content(own) == note_content(note):
            remember("note", note["originId"], own["id"], digest)
            continue
        if note_count >= 1000:
            raise BackupError("restore would exceed the account note limit")
        target = uuid4()
        connection.execute(
            """INSERT INTO app.library_place_notes(id,library_id,user_id,label,position,created_at,updated_at)
          VALUES (%s,%s,%s,%s,ST_SetSRID(ST_Point(%s,%s),4326),%s,%s)""",
            (
                target,
                job.library_id,
                job.user_id,
                note["label"],
                *note["position"],
                note["createdAt"],
                note["updatedAt"],
            ),
        )
        for anchor in note["anchors"]:
            row = connection.execute(
                """INSERT INTO app.library_place_note_anchors(note_id,library_id,source_id,observation_id)
              SELECT %s,%s,obs.source_id,obs.id FROM app.library_observations obs
              WHERE obs.library_id=%s AND obs.source_id=%s AND obs.fingerprint=%s AND obs.identity_version=%s RETURNING observation_id""",
                (
                    target,
                    job.library_id,
                    job.library_id,
                    anchor["sourceId"],
                    anchor["fingerprint"],
                    backup.manifest["identityVersion"],
                ),
            ).fetchone()
            if not row:
                raise BackupError("restored note has no destination observation")
        remember("note", note["originId"], target, digest)
        note_count += 1
        added_notes += 1

    def add_view(kind, origin_id, title, config, created_at):
        nonlocal view_count, added_views
        digest = semantic_hash({"title": title, "config": config})
        previous = receipts.get((kind, origin_id))
        if previous and previous["hash"] == digest:
            return
        own = next(
            (
                view
                for view in state["views"]
                if view["id"] == origin_id and view["title"] == title and view["config"] == config
            ),
            None,
        )
        if not previous and own:
            remember(kind, origin_id, own["id"], digest)
            return
        if view_count >= 100:
            raise BackupError("restore would exceed the account saved-view limit")
        target = uuid4()
        connection.execute(
            """INSERT INTO app.library_saved_views(id,library_id,user_id,title,config,created_at)
          VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
            (target, job.library_id, job.user_id, title, json.dumps(config), created_at),
        )
        remember(kind, origin_id, target, digest)
        view_count += 1
        added_views += 1

    for view in backup.manifest["views"]:
        add_view("view", view["id"], view["title"], view["config"], view["createdAt"])
    workspace = backup.manifest["workspace"]
    if workspace:
        origin_id = str(
            uuid5(
                NAMESPACE_URL, "routefrom:restored-workspace:" + semantic_hash(workspace["config"])
            )
        )
        previous = receipts.get(("workspace", origin_id))
        digest = semantic_hash({"title": "备份中的最近视图", "config": workspace["config"]})
        if (
            not previous
            and state["workspace"] is None
            and not state["files"]
            and not state["notes"]
        ):
            connection.execute(
                """INSERT INTO app.library_workspace_state(library_id,user_id,revision,config)
              VALUES (%s,%s,1,%s::jsonb) ON CONFLICT(library_id) DO NOTHING""",
                (job.library_id, job.user_id, json.dumps(workspace["config"])),
            )
            remember("workspace", origin_id, None, digest)
        else:
            add_view(
                "workspace",
                origin_id,
                "备份中的最近视图",
                workspace["config"],
                workspace["updatedAt"],
            )
    return evidence["decisions"], {"addedNotes": added_notes, "addedViews": added_views}
