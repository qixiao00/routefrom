import {
  parseViewportRequest,
  queryViewportSelection,
  buildViewportSelection,
  ViewportQueryError,
  type ViewportSelection,
} from "@/lib/workspace-viewport";
import {
  LocalPreviewInvalidError,
  LocalPreviewNotFoundError,
  readLocalWorkspacePreview,
} from "@/server/local-preview";
import type { WorkspacePreview } from "@/lib/workspace-data";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 16 * 1024;
const PRIVATE_HEADERS = { "Cache-Control": "private, no-store, max-age=0" };

let cachedSelection: {
  preview: WorkspacePreview;
  rangeKey: string;
  selection: ViewportSelection;
} | null = null;

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status, headers: PRIVATE_HEADERS });
}

export async function POST(request: Request): Promise<Response> {
  if (process.env.ROUTEFROM_ENABLE_LOCAL_DATA_API !== "true") {
    return errorResponse(503, "workspace_api_disabled", "本地足迹接口尚未启用。");
  }
  if (!request.headers.get("content-type")?.startsWith("application/json")) {
    return errorResponse(415, "unsupported_media_type", "Content-Type must be application/json");
  }
  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return errorResponse(413, "payload_too_large", "request body exceeds 16 KiB");
  }
  try {
    const bodyText = await request.text();
    if (new TextEncoder().encode(bodyText).byteLength > MAX_BODY_BYTES) {
      return errorResponse(413, "payload_too_large", "request body exceeds 16 KiB");
    }
    let body: unknown;
    try {
      body = JSON.parse(bodyText);
    } catch {
      return errorResponse(400, "invalid_json", "request body is not valid JSON");
    }
    const query = parseViewportRequest(body);
    const preview = await readLocalWorkspacePreview();
    if (query.datasetId !== preview.dataset.id || query.processingRunId !== preview.processing.runId) {
      return errorResponse(404, "not_found", "dataset or processing run was not found");
    }
    const rangeKey = JSON.stringify(query.ranges);
    if (cachedSelection?.preview !== preview || cachedSelection.rangeKey !== rangeKey) {
      cachedSelection = {
        preview,
        rangeKey,
        selection: buildViewportSelection(preview.paths, query.ranges),
      };
    }
    return Response.json(
      queryViewportSelection(cachedSelection.selection, query.bounds, query.detailZoom),
      { headers: PRIVATE_HEADERS },
    );
  } catch (error) {
    if (error instanceof ViewportQueryError) {
      return errorResponse(400, "invalid_query", error.message);
    }
    if (error instanceof LocalPreviewNotFoundError) {
      return errorResponse(404, "preview_missing", "请先生成本地足迹预览。");
    }
    if (error instanceof LocalPreviewInvalidError) {
      return errorResponse(409, "preview_invalid", error.message);
    }
    console.error("local viewport query failed", error);
    return errorResponse(500, "internal_error", "查询当前地图区域失败");
  }
}
