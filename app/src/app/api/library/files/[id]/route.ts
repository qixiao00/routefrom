import { withDatabaseTransaction } from "@/server/database";
import { getUserLibrary, LibraryRequestError, libraryErrorResponse, PRIVATE_HEADERS, readBoundedJson, requireSameOrigin } from "@/server/library-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  try {
    requireSameOrigin(request);
    const { user, library } = await getUserLibrary(request);
    const { id } = await context.params;
    if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)) throw new LibraryRequestError(404, "file_not_found", "找不到这个文件。");
    const body = await readBoundedJson(request, 1024);
    if (body.action !== "withdraw" && body.action !== "restore") {
      throw new LibraryRequestError(400, "invalid_action", "请选择撤销或恢复。");
    }
    const result = await withDatabaseTransaction(async (client) => {
      await client.query("SELECT id FROM app.footprint_libraries WHERE id = $1 AND user_id = $2 FOR UPDATE", [library.id, user.id]);
      const files = await client.query("SELECT dataset_id, status FROM app.library_files WHERE id = $1 AND library_id = $2 AND user_id = $3 FOR UPDATE", [id, library.id, user.id]);
      if (!files.rows.length) throw new LibraryRequestError(404, "file_not_found", "找不到这个文件。");
      const file = files.rows[0];
      if (body.action === "restore") {
        if (file.status === "active") return { reused: true };
        const job = await client.query(`INSERT INTO app.library_jobs (library_id, user_id, dataset_id, kind)
          VALUES ($1, $2, $3, 'import') ON CONFLICT (library_id, dataset_id)
          WHERE kind = 'import' AND status IN ('queued', 'running', 'awaiting_resolution')
          DO UPDATE SET dataset_id = EXCLUDED.dataset_id RETURNING id, status`, [library.id, user.id, file.dataset_id]);
        return { job: job.rows[0] };
      }
      if (file.status === "withdrawn") return { reused: true };
      // Fence a concurrent importer before changing membership. It must not silently re-attach this file.
      await client.query(`UPDATE app.library_jobs SET status = 'superseded', stage = 'withdrawn',
        lease_until = NULL, completed_at = now(), updated_at = now()
        WHERE library_id = $1 AND dataset_id = $2 AND kind = 'import'
        AND status IN ('queued', 'running', 'awaiting_resolution')`, [library.id, file.dataset_id]);
      await client.query("UPDATE app.library_files SET status = 'withdrawn', withdrawn_at = now() WHERE id = $1", [id]);
      await client.query("UPDATE app.footprint_libraries SET revision = revision + 1, updated_at = now() WHERE id = $1", [library.id]);
      const job = await client.query("INSERT INTO app.library_jobs (library_id, user_id, kind) VALUES ($1, $2, 'reprocess') RETURNING id, status", [library.id, user.id]);
      return { job: job.rows[0] };
    });
    return Response.json(result, { status: result.reused ? 200 : 202, headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
