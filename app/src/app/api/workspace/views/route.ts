import { getUserLibrary, libraryErrorResponse, PRIVATE_HEADERS, readBoundedJson, requireSameOrigin } from "@/server/library-api";
import { createSavedView, readWorkspaceViews, requireViewAccount, saveWorkspaceView } from "@/server/workspace-views";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const owner = await getUserLibrary(request);
    requireViewAccount(owner, request);
    return Response.json(await readWorkspaceViews(owner), { headers: PRIVATE_HEADERS });
  }
  catch (error) { return libraryErrorResponse(error); }
}

export async function PUT(request: Request) {
  try {
    const owner = await getUserLibrary(request);
    requireSameOrigin(request);
    requireViewAccount(owner, request);
    return Response.json(await saveWorkspaceView(owner, await readBoundedJson(request, 32768)), { headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}

export async function POST(request: Request) {
  try {
    const owner = await getUserLibrary(request);
    requireSameOrigin(request);
    requireViewAccount(owner, request);
    return Response.json(await createSavedView(owner, await readBoundedJson(request, 32768)), { status: 201, headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
