import { WorkspaceQueryValidationError, parseWorkspaceQuery } from "@/lib/workspace-query";
import { DatabaseNotConfiguredError } from "@/server/database";
import {
  DatasetNotFoundError,
  WorkspaceApiDisabledError,
  requireDatasetAccess,
} from "@/server/workspace-access";
import {
  ProcessingRunNotFoundError,
  TrajectoryVariantNotFoundError,
  queryWorkspaceTrajectory,
} from "@/server/workspace-trajectory";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 64 * 1024;
const PRIVATE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "Content-Type": "application/json; charset=utf-8",
};

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status, headers: PRIVATE_HEADERS });
}

export async function POST(request: Request): Promise<Response> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim();
  if (contentType !== "application/json") {
    return errorResponse(415, "unsupported_media_type", "Content-Type must be application/json");
  }
  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return errorResponse(413, "payload_too_large", "request body exceeds 64 KiB");
  }
  try {
    const text = await request.text();
    if (new TextEncoder().encode(text).byteLength > MAX_BODY_BYTES) {
      return errorResponse(413, "payload_too_large", "request body exceeds 64 KiB");
    }
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      return errorResponse(400, "invalid_json", "request body is not valid JSON");
    }
    const query = parseWorkspaceQuery(body);
    requireDatasetAccess(query.datasetId);
    const result = await queryWorkspaceTrajectory(query);
    return Response.json(result, { headers: PRIVATE_HEADERS });
  } catch (error) {
    if (error instanceof WorkspaceQueryValidationError) {
      return errorResponse(400, "invalid_query", error.message);
    }
    if (error instanceof WorkspaceApiDisabledError || error instanceof DatabaseNotConfiguredError) {
      return errorResponse(503, "workspace_api_unavailable", "workspace data API is not configured");
    }
    if (error instanceof DatasetNotFoundError || error instanceof ProcessingRunNotFoundError) {
      return errorResponse(404, "not_found", "dataset or processing run was not found");
    }
    if (error instanceof TrajectoryVariantNotFoundError) {
      return errorResponse(409, "trajectory_not_ready", "processing run has no trajectory result");
    }
    console.error("workspace trajectory query failed", error);
    return errorResponse(500, "internal_error", "workspace query failed");
  }
}
