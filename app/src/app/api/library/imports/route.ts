import { createHash, randomUUID } from "node:crypto";
import { getDatabase } from "@/server/database";
import {
  getUserLibrary, LibraryRequestError, libraryErrorResponse, PRIVATE_HEADERS,
  readBoundedMultipart, requireSameOrigin,
} from "@/server/library-api";
import { storePrivateCsv } from "@/server/private-files";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const MAX_FILE_BYTES = 80 * 1024 * 1024;

export async function POST(request: Request) {
  try {
    requireSameOrigin(request);
    const { user, library } = await getUserLibrary(request);
    const form = await readBoundedMultipart(request, MAX_FILE_BYTES + 64 * 1024);
    const file = form.get("file");
    if (!(file instanceof File) || !file.name.toLowerCase().endsWith(".csv") || !file.size) {
      throw new LibraryRequestError(400, "invalid_csv", "请选择非空的 CSV 文件。");
    }
    if (file.size > MAX_FILE_BYTES) throw new LibraryRequestError(413, "upload_too_large", "单个 CSV 文件不能超过 80 MB。");
    const bytes = new Uint8Array(await file.arrayBuffer());
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    const sql = getDatabase();
    const known = await sql`SELECT id, source_storage, source_object_key FROM app.datasets WHERE user_id = ${user.id}::uuid AND source_sha256 = ${sha256}`;
    let datasetId: string;
    if (known.length) {
      datasetId = String(known[0].id);
      if (known[0].source_storage !== "local" || !String(known[0].source_object_key).startsWith(`raw/${user.id}/`)) {
        // A matching canonical/generated or interrupted-restore import is not
        // yet a retained upload. Preserve the bytes supplied in this request.
        const key = `raw/${user.id}/${randomUUID()}.csv`;
        await storePrivateCsv(key, bytes);
        await sql`UPDATE app.datasets SET source_storage='local', source_object_key=${key},
          original_filename=${file.name.replace(/[\\/\0]/g, "_").slice(0, 255)}
          WHERE id=${datasetId}::uuid AND user_id=${user.id}::uuid AND source_sha256=${sha256}
            AND (source_storage <> 'local' OR source_object_key NOT LIKE ${`raw/${user.id}/%`})`;
      }
    } else {
      const key = `raw/${user.id}/${randomUUID()}.csv`;
      await storePrivateCsv(key, bytes);
      const datasets = await sql`
        INSERT INTO app.datasets (user_id, original_filename, source_storage, source_object_key, source_sha256, source_size_bytes)
        VALUES (${user.id}::uuid, ${file.name.replace(/[\\/\0]/g, "_").slice(0, 255)}, 'local', ${key}, ${sha256}, ${file.size})
        ON CONFLICT (user_id, source_sha256) DO UPDATE SET source_sha256 = EXCLUDED.source_sha256
        RETURNING id
      `;
      datasetId = String(datasets[0].id);
    }
    const active = await sql`
      SELECT id FROM app.library_files WHERE library_id = ${library.id}::uuid
        AND dataset_id = ${datasetId}::uuid AND status = 'active'
    `;
    if (active.length) return Response.json({ reused: true, fileId: active[0].id }, { headers: PRIVATE_HEADERS });
    const jobs = await sql`
      INSERT INTO app.library_jobs (library_id, user_id, dataset_id, kind)
      VALUES (${library.id}::uuid, ${user.id}::uuid, ${datasetId}::uuid, 'import')
      ON CONFLICT (library_id, dataset_id)
        WHERE kind = 'import' AND status IN ('queued', 'running', 'awaiting_resolution')
      DO UPDATE SET dataset_id = EXCLUDED.dataset_id
      RETURNING id, status
    `;
    return Response.json({ job: jobs[0], reused: false }, { status: 202, headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
