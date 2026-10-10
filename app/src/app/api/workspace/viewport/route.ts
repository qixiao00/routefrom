import { parseViewportRequest, ViewportQueryError } from "@/lib/workspace-viewport";
import { getUserLibrary, libraryErrorResponse, PRIVATE_HEADERS, readBoundedJson } from "@/server/library-api";
import { resolvePublishedWorkspace } from "@/server/workspace-access";
import { readPersonalViewport } from "@/server/workspace-viewport";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    const owner = await getUserLibrary(request);
    const query = parseViewportRequest(await readBoundedJson(request, 16 * 1024));
    const snapshot = (await resolvePublishedWorkspace(owner, query))!;
    return Response.json(await readPersonalViewport(snapshot, query), { headers: PRIVATE_HEADERS });
  } catch (error) {
    if (error instanceof ViewportQueryError) return Response.json({ error: { code: "invalid_query", message: error.message } },
      { status: 400, headers: PRIVATE_HEADERS });
    return libraryErrorResponse(error);
  }
}
