import { getDatabase } from "@/server/database";
import { getUserLibrary, libraryErrorResponse, PRIVATE_HEADERS } from "@/server/library-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const { user, library } = await getUserLibrary(request);
    const sql = getDatabase();
    const files = await sql`
      SELECT file.id, dataset.original_filename AS name, dataset.point_count,
             dataset.source_size_bytes, file.status, file.added_at,
             dataset.recorded_from, dataset.recorded_to
      FROM app.library_files AS file JOIN app.datasets AS dataset ON dataset.id = file.dataset_id
      WHERE file.library_id = ${library.id}::uuid AND file.user_id = ${user.id}::uuid
      ORDER BY file.added_at DESC
    `;
    const jobs = await sql`
      SELECT job.id, job.kind, job.status, job.stage, job.progress, job.metrics,
             job.error_message, job.created_at, dataset.original_filename AS name
      FROM app.library_jobs AS job LEFT JOIN app.datasets AS dataset ON dataset.id = job.dataset_id
      WHERE job.library_id = ${library.id}::uuid AND job.user_id = ${user.id}::uuid
      ORDER BY job.created_at DESC LIMIT 50
    `;
    return Response.json({ library, files, jobs }, { headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
