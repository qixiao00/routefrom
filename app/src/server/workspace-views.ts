import "server-only";

import { parseMapViewConfig, type MapViewCollection } from "@/lib/map-view-config";
import { getDatabase, withDatabaseTransaction } from "./database";
import { LibraryRequestError } from "./library-api";
import type { UserLibrary } from "./workspace-access";

export function requireViewAccount(owner: UserLibrary, request: Request) {
  if (request.headers.get("x-routefrom-account") !== owner.user.subject) {
    throw new LibraryRequestError(409, "account_changed", "账号已切换，请刷新页面后再操作。");
  }
}

function configFromRequest(value: unknown) {
  try { return parseMapViewConfig(value); }
  catch { throw new LibraryRequestError(400, "invalid_config", "地图配置不完整或版本不受支持。"); }
}

export async function readWorkspaceViews(owner: UserLibrary): Promise<MapViewCollection> {
  const sql = getDatabase();
  // One SQL statement / snapshot, so state and bookmarks are a consistent read.
  const [row] = await sql`
    SELECT state.revision, state.config, state.updated_at,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('id', view.id, 'title', view.title,
        'config', view.config, 'createdAt', view.created_at) ORDER BY view.created_at DESC, view.id)
        FROM app.library_saved_views AS view WHERE view.library_id = library.id AND view.user_id = library.user_id), '[]'::jsonb) AS views
    FROM app.footprint_libraries AS library LEFT JOIN app.library_workspace_state AS state ON state.library_id = library.id AND state.user_id = library.user_id
    WHERE library.id = ${owner.library.id}::uuid AND library.user_id = ${owner.user.id}::uuid
  `;
  return { revision: Number(row.revision ?? 0), config: row.config ? parseMapViewConfig(row.config) : null,
    updatedAt: row.updated_at ? new Date(row.updated_at).toISOString() : null,
    views: row.views.map((view: MapViewCollection["views"][number]) => ({ ...view, config: parseMapViewConfig(view.config) })) };
}

export async function saveWorkspaceView(owner: UserLibrary, body: Record<string, unknown>) {
  const config = configFromRequest(body.config);
  if (!Number.isSafeInteger(body.revision) || Number(body.revision) < 0) {
    throw new LibraryRequestError(400, "invalid_revision", "地图保存版本无效。");
  }
  return withDatabaseTransaction(async client => {
    await client.query("SELECT id FROM app.footprint_libraries WHERE id = $1 AND user_id = $2 FOR UPDATE", [owner.library.id, owner.user.id]);
    const previous = await client.query("SELECT revision FROM app.library_workspace_state WHERE library_id = $1 AND user_id = $2", [owner.library.id, owner.user.id]);
    if (Number(previous.rows[0]?.revision ?? 0) !== body.revision) {
      throw new LibraryRequestError(409, "view_conflict", "其他窗口或设备已修改视图。请先读取账号视图，再选择恢复或保存本地视图。");
    }
    const result = await client.query(`
      INSERT INTO app.library_workspace_state (library_id, user_id, revision, config) VALUES ($1, $2, 1, $3::jsonb)
      ON CONFLICT (library_id) DO UPDATE SET revision = library_workspace_state.revision + 1, config = EXCLUDED.config, updated_at = now()
      RETURNING revision, updated_at`, [owner.library.id, owner.user.id, JSON.stringify(config)]);
    return { revision: Number(result.rows[0].revision), updatedAt: new Date(result.rows[0].updated_at).toISOString(), config };
  });
}

export async function createSavedView(owner: UserLibrary, body: Record<string, unknown>) {
  const config = configFromRequest(body.config);
  const title = typeof body.title === "string" ? body.title.trim() : "";
  if (!title || title.length > 80) throw new LibraryRequestError(400, "invalid_title", "视图名称需要 1–80 个字符。");
  return withDatabaseTransaction(async client => {
    await client.query("SELECT id FROM app.footprint_libraries WHERE id = $1 AND user_id = $2 FOR UPDATE", [owner.library.id, owner.user.id]);
    const count = await client.query("SELECT count(*) FROM app.library_saved_views WHERE library_id = $1 AND user_id = $2", [owner.library.id, owner.user.id]);
    if (Number(count.rows[0].count) >= 100) throw new LibraryRequestError(409, "view_limit", "最多保存 100 个命名视图，请先删除不再需要的视图。");
    const result = await client.query(`INSERT INTO app.library_saved_views (library_id, user_id, title, config) VALUES ($1, $2, $3, $4::jsonb)
      RETURNING id, title, config, created_at`, [owner.library.id, owner.user.id, title, JSON.stringify(config)]);
    const view = result.rows[0];
    return { id: view.id, title: view.title, config: parseMapViewConfig(view.config), createdAt: new Date(view.created_at).toISOString() };
  });
}

export async function deleteSavedView(owner: UserLibrary, id: string) {
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id)) throw new LibraryRequestError(404, "not_found", "视图不存在。");
  const deleted = await getDatabase()`DELETE FROM app.library_saved_views WHERE id = ${id}::uuid AND library_id = ${owner.library.id}::uuid
    AND user_id = ${owner.user.id}::uuid RETURNING id`;
  if (!deleted.length) throw new LibraryRequestError(404, "not_found", "视图不存在。");
}
