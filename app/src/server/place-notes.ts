import "server-only";

import { classifyPlaceNotes, type PlaceNoteCollection } from "@/lib/place-note-records";
import { getDatabase, withDatabaseTransaction } from "./database";
import { LibraryRequestError } from "./library-api";
import { resolvePublishedWorkspace, type UserLibrary } from "./workspace-access";

const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export async function readPlaceNotes(owner: UserLibrary, runId?: string): Promise<PlaceNoteCollection> {
  const snapshot = await resolvePublishedWorkspace(owner);
  const run = snapshot?.processing_run_id ?? null;
  if (runId && runId !== run) throw new LibraryRequestError(409, "snapshot_changed", "足迹版本已更新，请刷新地图后读取备注。");
  const sql = getDatabase();
  const [rows, manifests] = await Promise.all([
    sql`
      SELECT note.id, note.label, note.revision, ST_X(note.position) AS longitude, ST_Y(note.position) AS latitude,
        COALESCE((SELECT jsonb_agg(DISTINCT evidence.place_id)
          FROM app.library_place_note_anchors AS anchor
          JOIN app.library_place_observations AS evidence ON evidence.library_id = anchor.library_id
            AND evidence.observation_id = anchor.observation_id AND evidence.processing_run_id = ${run}::uuid
          WHERE anchor.note_id = note.id AND anchor.library_id = note.library_id), '[]'::jsonb) AS place_ids
      FROM app.library_place_notes AS note
      WHERE note.library_id = ${owner.library.id}::uuid AND note.user_id = ${owner.user.id}::uuid
      ORDER BY note.created_at, note.id
    `,
    sql`SELECT processing_run_id FROM app.library_processing_inputs
        WHERE processing_run_id = ${run}::uuid AND library_id = ${owner.library.id}::uuid AND user_id = ${owner.user.id}::uuid`,
  ]);
  return { runId: run, lineageReady: !run || manifests.length === 1, notes: classifyPlaceNotes(rows.map(row => ({
    id: row.id, label: row.label, revision: Number(row.revision), position: [Number(row.longitude), Number(row.latitude)], placeIds: row.place_ids,
  }))) };
}

export async function savePlaceNote(owner: UserLibrary, body: Record<string, unknown>) {
  if (typeof body.label !== "string" || body.label.trim().length > 80 || !Number.isSafeInteger(body.revision)
    || Number(body.revision) < 0 || (body.id !== null && (typeof body.id !== "string" || !uuid.test(body.id)))
    || (body.placeId !== null && (typeof body.placeId !== "string" || !uuid.test(body.placeId)))) {
    throw new LibraryRequestError(400, "invalid_note", "备注需要不超过 80 个字符及有效的保存版本。");
  }
  const label = body.label.trim();
  if (!body.id && (!label || body.revision !== 0 || !body.placeId)) throw new LibraryRequestError(400, "invalid_note", "请先选择地点并填写备注。");
  return withDatabaseTransaction(async client => {
    const libraries = await client.query("SELECT active_snapshot_id FROM app.footprint_libraries WHERE id = $1 AND user_id = $2 FOR UPDATE", [owner.library.id, owner.user.id]);
    if (body.id) {
      const current = await client.query("SELECT revision FROM app.library_place_notes WHERE id = $1 AND library_id = $2 AND user_id = $3 FOR UPDATE", [body.id, owner.library.id, owner.user.id]);
      if (!current.rows.length) throw new LibraryRequestError(404, "note_not_found", "备注已不存在，请重新读取。");
      if (Number(current.rows[0].revision) !== body.revision) throw new LibraryRequestError(409, "note_conflict", "其他窗口或设备已修改备注。请重新读取后确认，草稿未保存。");
      if (!label) {
        await client.query("DELETE FROM app.library_place_notes WHERE id = $1 AND library_id = $2 AND user_id = $3", [body.id, owner.library.id, owner.user.id]);
        return { deleted: true };
      }
    }
    let anchors: { observation_id: string; source_id: string }[] = [];
    let position: { longitude: number; latitude: number } | undefined;
    if (body.placeId) {
      if (typeof body.runId !== "string" || !uuid.test(body.runId)) throw new LibraryRequestError(400, "invalid_note", "处理版本无效。");
      const active = await client.query("SELECT processing_run_id FROM app.library_snapshots WHERE id = $1 AND library_id = $2 AND user_id = $3 AND status = 'active'", [libraries.rows[0].active_snapshot_id, owner.library.id, owner.user.id]);
      if (active.rows[0]?.processing_run_id !== body.runId) throw new LibraryRequestError(409, "snapshot_changed", "足迹版本已更新，请刷新地图后再备注。草稿未保存。");
      const manifests = await client.query("SELECT processing_run_id FROM app.library_processing_inputs WHERE processing_run_id = $1 AND library_id = $2 AND user_id = $3", [body.runId, owner.library.id, owner.user.id]);
      if (!manifests.rows.length) throw new LibraryRequestError(409, "lineage_upgrade_required", "当前旧版本缺少地点来源，请先重新处理足迹，再保存备注。");
      const places = await client.query("SELECT ST_X(centroid) AS longitude, ST_Y(centroid) AS latitude FROM app.place_versions WHERE processing_run_id = $1 AND place_id = $2", [body.runId, body.placeId]);
      if (!places.rows.length) throw new LibraryRequestError(404, "place_not_found", "这个地点不属于当前足迹版本。");
      position = places.rows[0];
      // Sample up to 12 time-distributed exact observations, not rounded coordinates.
      const selected = await client.query(`SELECT DISTINCT ON (bucket) observation_id, source_id FROM (
        SELECT evidence.observation_id, evidence.source_id,
          ntile(12) OVER (ORDER BY observation.recorded_at, evidence.observation_id) AS bucket,
          observation.recorded_at
        FROM app.library_place_observations AS evidence JOIN app.library_observations AS observation ON observation.id = evidence.observation_id
        WHERE evidence.library_id = $1 AND evidence.processing_run_id = $2 AND evidence.place_id = $3
      ) AS ranked ORDER BY bucket, recorded_at, observation_id`, [owner.library.id, body.runId, body.placeId]);
      anchors = selected.rows;
      if (!anchors.length) throw new LibraryRequestError(409, "place_evidence_missing", "地点缺少可验证来源，无法关联备注。");
      const occupied = await client.query(`SELECT note.id FROM app.library_place_notes AS note
        JOIN app.library_place_note_anchors AS anchor ON anchor.note_id = note.id
        JOIN app.library_place_observations AS evidence ON evidence.library_id = anchor.library_id AND evidence.observation_id = anchor.observation_id
        WHERE note.library_id = $1 AND note.user_id = $2 AND evidence.processing_run_id = $3 AND evidence.place_id = $4
          AND ($5::uuid IS NULL OR note.id <> $5) LIMIT 1`, [owner.library.id, owner.user.id, body.runId, body.placeId, body.id]);
      if (occupied.rows.length) throw new LibraryRequestError(409, "place_note_collision", "这个地点已有其他备注。请在备注列表中确认并处理，不能自动覆盖。");
    }
    let id = body.id;
    if (!id) {
      const count = await client.query("SELECT count(*) FROM app.library_place_notes WHERE library_id = $1 AND user_id = $2", [owner.library.id, owner.user.id]);
      if (Number(count.rows[0].count) >= 1000) throw new LibraryRequestError(409, "note_limit", "最多保存 1000 条备注，请先整理不再需要的备注。");
      const inserted = await client.query("INSERT INTO app.library_place_notes (library_id, user_id, label, position) VALUES ($1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5), 4326)) RETURNING id", [owner.library.id, owner.user.id, label, position!.longitude, position!.latitude]);
      id = inserted.rows[0].id;
    } else {
      await client.query("UPDATE app.library_place_notes SET label = $1, revision = revision + 1, updated_at = now() WHERE id = $2 AND library_id = $3 AND user_id = $4", [label, id, owner.library.id, owner.user.id]);
    }
    if (position) {
      await client.query("UPDATE app.library_place_notes SET position = ST_SetSRID(ST_MakePoint($1, $2), 4326) WHERE id = $3", [position.longitude, position.latitude, id]);
      await client.query("DELETE FROM app.library_place_note_anchors WHERE note_id = $1 AND library_id = $2", [id, owner.library.id]);
      await client.query(`INSERT INTO app.library_place_note_anchors (note_id, library_id, source_id, observation_id)
        SELECT $1, $2, incoming.source_id, incoming.observation_id
        FROM jsonb_to_recordset($3::jsonb) AS incoming(source_id uuid, observation_id uuid)`, [id, owner.library.id, JSON.stringify(anchors)]);
    }
    return { id };
  });
}
