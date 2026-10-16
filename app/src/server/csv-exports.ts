import "server-only";

import { open, realpath } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { csvDownloadName, csvRow, OBSERVATION_COLUMNS, OBSERVATION_FIELDS, TRAJECTORY_COLUMNS, type CsvExportQuery } from "@/lib/csv-export";
import { getDatabase } from "./database";
import { LibraryRequestError, PRIVATE_HEADERS } from "./library-api";
import { privateDataRoot, privateFilePath } from "./private-files";
import { resolvePublishedWorkspace, type UserLibrary } from "./workspace-access";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const BATCH_SIZE = 1000;
const SOURCE_CELLS = OBSERVATION_FIELDS.map(field => `observation.payload->>'${field}'`).join(",");
const headersFor = (name: string) => ({ ...PRIVATE_HEADERS, "Content-Type": "text/csv; charset=utf-8",
  "Content-Disposition": csvDownloadName(name), "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" });

export async function originalCsvResponse(owner: UserLibrary, fileId: string, request: Request): Promise<Response> {
  if (!UUID.test(fileId)) throw new LibraryRequestError(404, "not_found", "原始文件不存在。");
  const [file] = await getDatabase()`SELECT dataset.source_object_key, dataset.source_size_bytes, dataset.original_filename, dataset.source_sha256
    FROM app.library_files AS file JOIN app.datasets AS dataset ON dataset.id = file.dataset_id AND dataset.user_id = file.user_id
    WHERE file.id = ${fileId}::uuid AND file.library_id = ${owner.library.id}::uuid AND file.user_id = ${owner.user.id}::uuid
      AND dataset.source_storage = 'local'`;
  if (!file) throw new LibraryRequestError(404, "not_found", "原始文件不存在。");
  let handle;
  try {
    if (!String(file.source_object_key).startsWith("raw/")) throw new Error("not an original");
    const target = privateFilePath(file.source_object_key);
    const root = await realpath(privateDataRoot());
    const actual = await realpath(target);
    const expected = path.join(root, ...file.source_object_key.split("/"));
    // Mounted root is allowed; links below it cannot redirect to another asset.
    if (actual !== expected) throw new Error("redirected private file");
    handle = await open(actual, "r");
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size !== Number(file.source_size_bytes)) throw new Error("source unavailable");
  } catch {
    await handle?.close();
    throw new LibraryRequestError(503, "source_unavailable", "原始文件暂时无法读取，请检查私有存储后重试。");
  }
  const headers = { ...headersFor(file.original_filename), "Content-Length": String(file.source_size_bytes), "X-Routefrom-Source-Sha256": file.source_sha256 };
  if (request.method === "HEAD") { await handle.close(); return new Response(null, { headers }); }
  const stream = handle.createReadStream({ autoClose: true });
  const abort = () => stream.destroy(new Error("download cancelled"));
  if (request.signal.aborted) abort(); else request.signal.addEventListener("abort", abort, { once: true });
  stream.once("close", () => request.signal.removeEventListener("abort", abort));
  return new Response(Readable.toWeb(stream) as ReadableStream<Uint8Array>, { headers });
}

export async function selectedCsvResponse(owner: UserLibrary, query: CsvExportQuery, request: Request): Promise<Response> {
  const snapshot = await resolvePublishedWorkspace(owner, query);
  if (!snapshot) throw new LibraryRequestError(404, "not_found", "足迹版本不存在。");
  const sql = getDatabase();
  const [manifest] = await sql`SELECT point_count FROM app.library_processing_inputs
    WHERE processing_run_id = ${snapshot.processing_run_id}::uuid AND library_id = ${owner.library.id}::uuid AND user_id = ${owner.user.id}::uuid`;
  if (!manifest) throw new LibraryRequestError(409, "lineage_upgrade_required", "旧版本没有完整来源，请在地点备注面板重新处理足迹后导出。");
  if (query.kind !== "observations") {
    const [variant] = await sql`SELECT id FROM app.trajectory_variants WHERE processing_run_id = ${snapshot.processing_run_id}::uuid AND variant_kind = ${query.kind}`;
    if (!variant) throw new LibraryRequestError(409, "variant_unavailable", "这个处理版本没有所选轨迹，请改用去噪 GPS。");
  }
  const cancellation = new AbortController();
  const signal = AbortSignal.any([request.signal, cancellation.signal, AbortSignal.timeout(300_000)]);
  const ranges = JSON.stringify(query.timeSelection.ranges);
  // Keyset pages of immutable published results: no map LOD, inferred lines,
  // range-boundary interpolation, overlapping-slice duplication or SELECT *.
  let last = "-1";
  const page = async () => {
    const rows = await sql.query(query.kind === "observations" ? `
      SELECT mapping.point_id::text AS cursor,
        jsonb_build_array(${SOURCE_CELLS}) AS cells
      FROM app.library_run_observations AS mapping
      JOIN app.library_observations AS observation ON observation.id = mapping.observation_id AND observation.library_id = mapping.library_id AND observation.source_id = mapping.source_id
      WHERE mapping.processing_run_id = $1::uuid AND mapping.library_id = $2::uuid AND mapping.dataset_id = $3::uuid AND mapping.point_id > $4::bigint
        AND EXISTS (SELECT 1 FROM jsonb_to_recordset($5::jsonb) AS slice(start timestamptz, "end" timestamptz)
          WHERE observation.recorded_at >= slice.start AND observation.recorded_at < slice."end")
      ORDER BY mapping.point_id LIMIT ${BATCH_SIZE}` : `
      SELECT vertex.sequence_number::text AS cursor,
        jsonb_build_array(to_char(vertex.recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
          ST_X(vertex.position)::text, ST_Y(vertex.position)::text, (selected.ordinality - 1)::text, segment.sequence_number::text, vertex.segment_sequence_number::text,
          observation.id::text, observation.fingerprint, observation.source_id::text,
          assessment.quality_status, assessment.anomaly_probability::text, array_to_string(assessment.reason_codes, '|'),
          variant.variant_kind, variant.processing_run_id::text, $7::text) AS cells
      FROM app.trajectory_variants AS variant
      JOIN app.trajectory_vertices AS vertex ON vertex.trajectory_variant_id = variant.id AND vertex.dataset_id = variant.dataset_id
      JOIN app.trajectory_segments AS segment ON segment.id = vertex.trajectory_segment_id AND segment.processing_run_id = variant.processing_run_id
      JOIN app.library_run_observations AS mapping ON mapping.processing_run_id = variant.processing_run_id AND mapping.point_id = vertex.source_point_id AND mapping.library_id = $2::uuid AND mapping.dataset_id = $3::uuid
      JOIN app.library_observations AS observation ON observation.id = mapping.observation_id AND observation.library_id = mapping.library_id AND observation.source_id = mapping.source_id
      JOIN app.point_assessments AS assessment ON assessment.processing_run_id = mapping.processing_run_id AND assessment.point_id = mapping.point_id
      JOIN LATERAL (SELECT slice.ordinality FROM jsonb_array_elements($5::jsonb) WITH ORDINALITY AS slice(value, ordinality)
        WHERE vertex.recorded_at >= (slice.value->>'start')::timestamptz AND vertex.recorded_at < (slice.value->>'end')::timestamptz) AS selected ON true
      WHERE variant.processing_run_id = $1::uuid AND variant.variant_kind = $6::text AND vertex.sequence_number > $4::bigint
        AND NOT vertex.is_interpolated AND assessment.quality_status <> 'excluded'
      ORDER BY vertex.sequence_number LIMIT ${BATCH_SIZE}`,
    query.kind === "observations" ? [snapshot.processing_run_id, owner.library.id, snapshot.dataset_id, last, ranges]
      : [snapshot.processing_run_id, owner.library.id, snapshot.dataset_id, last, ranges, query.kind, snapshot.algorithm_version],
    { fetchOptions: { signal: AbortSignal.any([signal, AbortSignal.timeout(30_000)]) } });
    if (rows.length) last = String(rows.at(-1)!.cursor);
    return rows as { cursor: string; cells: (string | null)[] }[];
  };
  // Validate the first query before committing a successful download response.
  const first = await page();
  const headers = headersFor(`routefrom-${query.kind}-${snapshot.processing_run_id.slice(0, 8)}.csv`);
  if (request.method === "HEAD") return new Response(null, { headers });
  const encoder = new TextEncoder();
  let batch = first;
  let initial = true;
  return new Response(new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        signal.throwIfAborted();
        if (initial) { initial = false; controller.enqueue(encoder.encode(csvRow(query.kind === "observations" ? OBSERVATION_COLUMNS : TRAJECTORY_COLUMNS))); return; }
        controller.enqueue(encoder.encode(batch.map(row => csvRow(row.cells)).join("")));
        if (batch.length < BATCH_SIZE) controller.close();
        else batch = await page();
      } catch (error) { cancellation.abort(); controller.error(error); }
    },
    cancel() { cancellation.abort(); },
  }, { highWaterMark: 0 }), { headers });
}
