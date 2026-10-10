import type { MapViewCollection, MapViewConfig, SavedMapView } from "./map-view-config";
import { WorkspaceBootstrapError } from "./workspace-client.ts";

async function request<T>(accountSubject: string, method: string, body?: unknown, id?: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/workspace/views${id ? `/${encodeURIComponent(id)}` : ""}`, {
    method, cache: "no-store", signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
    headers: { Accept: "application/json", "X-RouteFrom-Account": accountSubject, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new WorkspaceBootstrapError(result.error?.message ?? "暂时无法保存地图视图。", result.error?.code ?? "unknown_error");
  }
  return response.status === 204 ? undefined as T : response.json();
}

export const loadMapViews = (accountSubject: string, signal?: AbortSignal) => request<MapViewCollection>(accountSubject, "GET", undefined, undefined, signal);
export const saveActiveMapView = (accountSubject: string, config: MapViewConfig, revision: number, signal?: AbortSignal) =>
  request<{ revision: number; config: MapViewConfig; updatedAt: string }>(accountSubject, "PUT", { config, revision }, undefined, signal);
export const createMapView = (accountSubject: string, title: string, config: MapViewConfig) => request<SavedMapView>(accountSubject, "POST", { title, config });
export const removeMapView = (accountSubject: string, id: string) => request<void>(accountSubject, "DELETE", undefined, id);
