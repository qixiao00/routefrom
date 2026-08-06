import type { WorkspacePreview } from "./workspace-data";

interface ApiErrorBody {
  error?: { code?: string; message?: string };
}

export class WorkspaceBootstrapError extends Error {
  constructor(
    message: string,
    readonly code: string,
  ) {
    super(message);
  }
}

export async function loadWorkspacePreview(signal?: AbortSignal): Promise<WorkspacePreview> {
  const response = await fetch("/api/workspace/bootstrap", {
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new WorkspaceBootstrapError(
      body.error?.message ?? "无法读取足迹工作区。",
      body.error?.code ?? "unknown_error",
    );
  }
  return (await response.json()) as WorkspacePreview;
}
