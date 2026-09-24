import type { WorkspaceEvents, WorkspacePreview } from "./workspace-data";
import type { TimeRange } from "./workspace-query";
import type { ViewportBounds, ViewportRequest, ViewportResponse } from "./workspace-viewport";

interface ApiErrorBody {
  error?: { code?: string; message?: string };
}

export class WorkspaceBootstrapError extends Error {
  readonly code: string;

  constructor(
    message: string,
    code: string,
  ) {
    super(message);
    this.code = code;
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

const viewportCache: Array<{ key: string; response: ViewportResponse }> = [];
const MAX_CACHED_VIEWPORTS = 8;
const eventCache = new Map<string, WorkspaceEvents>();

function containsBounds(outer: ViewportBounds, inner: ViewportBounds): boolean {
  return outer[0] <= inner[0] && outer[1] <= inner[1] &&
    outer[2] >= inner[2] && outer[3] >= inner[3];
}

function expandBounds(bounds: ViewportBounds): ViewportBounds {
  const longitudePadding = (bounds[2] - bounds[0]) * 0.5;
  const latitudePadding = (bounds[3] - bounds[1]) * 0.5;
  return [
    Math.max(-180, bounds[0] - longitudePadding),
    Math.max(-90, bounds[1] - latitudePadding),
    Math.min(180, bounds[2] + longitudePadding),
    Math.min(90, bounds[3] + latitudePadding),
  ];
}

export async function loadViewportPaths(
  datasetId: string,
  processingRunId: string,
  ranges: readonly TimeRange[],
  visibleBounds: ViewportBounds,
  detailZoom: number,
  signal?: AbortSignal,
): Promise<ViewportResponse> {
  const key = `${datasetId}:${processingRunId}:${JSON.stringify(ranges)}:${detailZoom}`;
  const cached = viewportCache.find(
    (entry) => entry.key === key && containsBounds(entry.response.coverageBounds, visibleBounds),
  );
  if (cached) return cached.response;

  const query: ViewportRequest = {
    datasetId,
    processingRunId,
    ranges: [...ranges],
    bounds: expandBounds(visibleBounds),
    detailZoom,
  };
  const response = await fetch("/api/workspace/viewport", {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(query),
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new WorkspaceBootstrapError(
      body.error?.message ?? "无法读取当前地图区域。",
      body.error?.code ?? "unknown_error",
    );
  }
  const result = (await response.json()) as ViewportResponse;
  viewportCache.unshift({ key, response: result });
  if (viewportCache.length > MAX_CACHED_VIEWPORTS) viewportCache.pop();
  return result;
}

export async function loadWorkspaceEvents(
  datasetId: string,
  processingRunId: string,
  ranges: readonly TimeRange[],
  signal?: AbortSignal,
): Promise<WorkspaceEvents> {
  const key = `${datasetId}:${processingRunId}:${JSON.stringify(ranges)}`;
  const cached = eventCache.get(key);
  if (cached) return cached;
  const response = await fetch("/api/workspace/events", {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ datasetId, processingRunId, ranges }),
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new WorkspaceBootstrapError(
      body.error?.message ?? "无法读取所选时间的事件。",
      body.error?.code ?? "unknown_error",
    );
  }
  const result = (await response.json()) as WorkspaceEvents;
  eventCache.set(key, result);
  if (eventCache.size > 8) eventCache.delete(eventCache.keys().next().value!);
  return result;
}
