import type { PlaceNoteCollection } from "./place-note-records.ts";
import { WorkspaceBootstrapError } from "./workspace-client.ts";

export async function requestPlaceNotes<T>(accountSubject: string, endpoint: string, method: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(endpoint, {
    method, cache: "no-store", signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
    headers: { Accept: "application/json", "X-RouteFrom-Account": accountSubject,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new WorkspaceBootstrapError(result.error?.message ?? "暂时无法保存地点备注。", result.error?.code ?? "unknown_error");
  }
  return response.json();
}

export const loadPlaceNotes = (subject: string, runId: string | null, signal?: AbortSignal) =>
  requestPlaceNotes<PlaceNoteCollection>(subject, `/api/workspace/place-notes${runId ? `?runId=${encodeURIComponent(runId)}` : ""}`, "GET", undefined, signal);
export const writePlaceNote = (subject: string, body: { id: string | null; revision: number; label: string; placeId: string | null; runId: string | null }, signal?: AbortSignal) =>
  requestPlaceNotes(subject, "/api/workspace/place-notes", "PUT", body, signal);
