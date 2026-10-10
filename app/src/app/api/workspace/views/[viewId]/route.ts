import { getUserLibrary, libraryErrorResponse, PRIVATE_HEADERS, requireSameOrigin } from "@/server/library-api";
import { deleteSavedView, requireViewAccount } from "@/server/workspace-views";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(request: Request, context: { params: Promise<{ viewId: string }> }) {
  try {
    const owner = await getUserLibrary(request);
    requireSameOrigin(request);
    requireViewAccount(owner, request);
    await deleteSavedView(owner, (await context.params).viewId);
    return new Response(null, { status: 204, headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
