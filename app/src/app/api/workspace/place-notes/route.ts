import { getUserLibrary, libraryErrorResponse, PRIVATE_HEADERS, readBoundedJson, requireSameOrigin } from "@/server/library-api";
import { requireViewAccount } from "@/server/workspace-views";
import { readPlaceNotes, savePlaceNote } from "@/server/place-notes";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const owner = await getUserLibrary(request);
    requireViewAccount(owner, request);
    const runId = new URL(request.url).searchParams.get("runId") ?? undefined;
    return Response.json(await readPlaceNotes(owner, runId), { headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}

export async function PUT(request: Request) {
  try {
    const owner = await getUserLibrary(request);
    requireSameOrigin(request);
    requireViewAccount(owner, request);
    const body = await readBoundedJson(request, 4096);
    return Response.json(await savePlaceNote(owner, body), { headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
