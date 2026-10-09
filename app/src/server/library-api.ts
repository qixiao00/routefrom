import "server-only";

import { AuthenticationRequiredError, requireAuthenticatedUser } from "./auth";
import { getDatabase } from "./database";

export class LibraryRequestError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export const PRIVATE_HEADERS = { "Cache-Control": "private, no-store" };

export async function getUserLibrary(request: Request) {
  const user = await requireAuthenticatedUser(request);
  const rows = await getDatabase()`
    INSERT INTO app.footprint_libraries (user_id) VALUES (${user.id}::uuid)
    ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
    RETURNING id, name, revision, active_snapshot_id
  `;
  return { user, library: rows[0] };
}

export function requireSameOrigin(request: Request) {
  const expected = process.env.BETTER_AUTH_URL;
  if (!expected || request.headers.get("origin") !== new URL(expected).origin) {
    throw new LibraryRequestError(403, "invalid_origin", "请从本站页面执行操作。");
  }
}

export function libraryErrorResponse(error: unknown): Response {
  if (error instanceof AuthenticationRequiredError) {
    return Response.json({ error: { code: "authentication_required", message: "请先登录。" } },
      { status: 401, headers: PRIVATE_HEADERS });
  }
  if (error instanceof LibraryRequestError) {
    return Response.json({ error: { code: error.code, message: error.message } },
      { status: error.status, headers: PRIVATE_HEADERS });
  }
  console.error("library operation failed", error instanceof Error ? error.name : "unknown_error");
  return Response.json({ error: { code: "library_unavailable", message: "暂时无法处理足迹，请稍后再试。" } },
    { status: 503, headers: PRIVATE_HEADERS });
}

export async function readBoundedMultipart(request: Request, maxBytes: number) {
  if (!request.headers.get("content-type")?.startsWith("multipart/form-data;")) {
    throw new LibraryRequestError(415, "invalid_upload", "请选择 CSV 文件上传。");
  }
  if (Number(request.headers.get("content-length")) > maxBytes) {
    throw new LibraryRequestError(413, "upload_too_large", "单个 CSV 文件不能超过 80 MB。");
  }
  const reader = request.body?.getReader();
  if (!reader) throw new LibraryRequestError(400, "empty_upload", "请选择 CSV 文件。");
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    total += next.value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new LibraryRequestError(413, "upload_too_large", "单个 CSV 文件不能超过 80 MB。");
    }
    chunks.push(next.value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try {
    return await new Response(bytes, { headers: { "content-type": request.headers.get("content-type")! } }).formData();
  } catch {
    throw new LibraryRequestError(400, "invalid_upload", "上传内容不完整，请重新选择文件。");
  }
}

export async function readBoundedJson(request: Request, maxBytes = 65536): Promise<Record<string, unknown>> {
  const reader = request.body?.getReader();
  if (!reader) throw new LibraryRequestError(400, "invalid_request", "请求内容为空。");
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const item = await reader.read();
    if (item.done) break;
    size += item.value.length;
    if (size > maxBytes) { await reader.cancel(); throw new LibraryRequestError(413, "body_too_large", "请求内容过大。"); }
    chunks.push(item.value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try {
    const value = JSON.parse(new TextDecoder().decode(bytes));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not an object");
    return value;
  } catch { throw new LibraryRequestError(400, "invalid_request", "请求内容不完整。"); }
}
