import { getUserLibrary, libraryErrorResponse, PRIVATE_HEADERS, requireSameOrigin } from "@/server/library-api";
import { requireViewAccount } from "@/server/workspace-views";
import { withDatabaseTransaction } from "@/server/database";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const owner = await getUserLibrary(request);
    requireSameOrigin(request);
    requireViewAccount(owner, request);
    const job = await withDatabaseTransaction(async client => {
      await client.query("SELECT id FROM app.footprint_libraries WHERE id = $1 AND user_id = $2 FOR UPDATE", [owner.library.id, owner.user.id]);
      const pending = await client.query("SELECT id, status FROM app.library_jobs WHERE library_id = $1 AND user_id = $2 AND status IN ('queued', 'running', 'awaiting_resolution') ORDER BY created_at LIMIT 1", [owner.library.id, owner.user.id]);
      if (pending.rows.length) return pending.rows[0];
      const inserted = await client.query("INSERT INTO app.library_jobs (library_id, user_id, kind) VALUES ($1, $2, 'reprocess') RETURNING id, status", [owner.library.id, owner.user.id]);
      return inserted.rows[0];
    });
    return Response.json({ job }, { status: 202, headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
