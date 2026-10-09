import { withDatabaseTransaction } from "@/server/database";
import { getUserLibrary, LibraryRequestError, libraryErrorResponse, PRIVATE_HEADERS, readBoundedJson, requireSameOrigin } from "@/server/library-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  try {
    requireSameOrigin(request);
    const { user, library } = await getUserLibrary(request);
    const { id } = await context.params;
    if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)) throw new LibraryRequestError(404, "job_not_found", "找不到这个任务。");
    const body = await readBoundedJson(request);
    const result = await withDatabaseTransaction(async (client) => {
      await client.query("SELECT id FROM app.footprint_libraries WHERE id = $1 AND user_id = $2 FOR UPDATE", [library.id, user.id]);
      const jobs = await client.query("SELECT status, metrics FROM app.library_jobs WHERE id = $1 AND library_id = $2 AND user_id = $3 FOR UPDATE", [id, library.id, user.id]);
      if (!jobs.rows.length) throw new LibraryRequestError(404, "job_not_found", "找不到这个任务。");
      const job = jobs.rows[0];
      if (body.action === "retry" && job.status === "failed") {
        // A failed import can already have active membership. Retrying still rebuilds the map.
        const pending = await client.query(`SELECT id FROM app.library_jobs WHERE library_id = $1
          AND id <> $2 AND kind = 'import' AND dataset_id = (SELECT dataset_id FROM app.library_jobs WHERE id = $2)
          AND status IN ('queued', 'running', 'awaiting_resolution')`, [library.id, id]);
        if (pending.rows.length) throw new LibraryRequestError(409, "already_queued", "同一文件已有待处理任务。");
        await client.query("UPDATE app.library_jobs SET status = 'queued', stage = 'queued', error_message = NULL, completed_at = NULL WHERE id = $1", [id]);
        return { id, status: "queued" };
      }
      if (body.action !== "resolve" || job.status !== "awaiting_resolution") {
        throw new LibraryRequestError(409, "job_not_waiting", "任务当前不需要冲突选择，请刷新列表。");
      }
      const decisions = body.resolutions;
      if (!decisions || typeof decisions !== "object" || Array.isArray(decisions) || !Object.keys(decisions).length || Object.keys(decisions).length > 200) {
        throw new LibraryRequestError(400, "invalid_decisions", "请至少选择一组冲突中的观测。");
      }
      const conflicts = job.metrics.conflicts ?? [];
      for (const [epoch, fingerprint] of Object.entries(decisions)) {
        const conflict = conflicts.find((item: { epochMs: number }) => String(item.epochMs) === epoch);
        if (typeof fingerprint !== "string" || !conflict?.candidates.some((candidate: { fingerprint: string }) => candidate.fingerprint === fingerprint)) {
          throw new LibraryRequestError(400, "invalid_decisions", "选择的观测不属于当前冲突候选。");
        }
      }
      await client.query(`UPDATE app.library_jobs SET resolutions = resolutions || $2::jsonb,
        status = 'queued', stage = 'queued', completed_at = NULL, updated_at = now() WHERE id = $1`, [id, JSON.stringify(decisions)]);
      return { id, status: "queued" };
    });
    return Response.json({ job: result }, { status: 202, headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
