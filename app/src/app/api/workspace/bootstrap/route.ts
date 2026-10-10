import { getUserLibrary, libraryErrorResponse, PRIVATE_HEADERS } from "@/server/library-api";
import { readPersonalWorkspace } from "@/server/workspace-bootstrap";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  try {
    return Response.json(await readPersonalWorkspace(await getUserLibrary(request)), { headers: PRIVATE_HEADERS });
  } catch (error) { return libraryErrorResponse(error); }
}
