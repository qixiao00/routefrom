import {
  LocalPreviewInvalidError,
  LocalPreviewNotFoundError,
  readLocalWorkspacePreview,
} from "@/server/local-preview";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PRIVATE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "Content-Type": "application/json; charset=utf-8",
};

export async function GET(): Promise<Response> {
  if (process.env.ROUTEFROM_ENABLE_LOCAL_DATA_API !== "true") {
    return Response.json(
      { error: { code: "workspace_api_disabled", message: "本地足迹接口尚未启用。" } },
      { status: 503, headers: PRIVATE_HEADERS },
    );
  }
  try {
    return Response.json(await readLocalWorkspacePreview(), { headers: PRIVATE_HEADERS });
  } catch (error) {
    if (error instanceof LocalPreviewNotFoundError) {
      return Response.json(
        { error: { code: "preview_missing", message: "请先生成本地足迹预览。" } },
        { status: 404, headers: PRIVATE_HEADERS },
      );
    }
    if (error instanceof LocalPreviewInvalidError) {
      return Response.json(
        { error: { code: "preview_invalid", message: error.message } },
        { status: 409, headers: PRIVATE_HEADERS },
      );
    }
    console.error("local workspace bootstrap failed", error);
    return Response.json(
      { error: { code: "internal_error", message: "读取本地足迹失败。" } },
      { status: 500, headers: PRIVATE_HEADERS },
    );
  }
}
