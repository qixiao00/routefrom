import { WorkspaceQueryValidationError, parseWorkspaceQuery } from "@/lib/workspace-query";
import { getUserLibrary, libraryErrorResponse, LibraryRequestError, PRIVATE_HEADERS, readBoundedJson } from "@/server/library-api";
import { resolvePublishedWorkspace } from "@/server/workspace-access";
import { ProcessingRunNotFoundError, TrajectoryVariantNotFoundError, queryWorkspaceTrajectory } from "@/server/workspace-trajectory";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  try {
    const owner = await getUserLibrary(request);
    const query = parseWorkspaceQuery(await readBoundedJson(request));
    const snapshot = (await resolvePublishedWorkspace(owner, query))!;
    return Response.json(await queryWorkspaceTrajectory({ ...query, processingRunId: snapshot.processing_run_id }), { headers: PRIVATE_HEADERS });
  } catch (error) {
    if (error instanceof WorkspaceQueryValidationError) return Response.json({ error: { code: "invalid_query", message: error.message } },
      { status: 400, headers: PRIVATE_HEADERS });
    if (error instanceof ProcessingRunNotFoundError || error instanceof TrajectoryVariantNotFoundError) {
      return libraryErrorResponse(new LibraryRequestError(404, "not_found", "足迹版本不存在。"));
    }
    return libraryErrorResponse(error);
  }
}
