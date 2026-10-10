import { normalizeTimeRanges, type TimeRange } from "./workspace-query.ts";

export const DEFAULT_LAYERS = { track: true, sparse: true, highSpeed: false, stays: false, gaps: true, places: true };
export const DEFAULT_MAP_VIEW = { longitude: 105, latitude: 28, zoom: 1.5, pitch: 0, bearing: 0 };

export function sameWorkspaceOwner(saved: string | null, incoming: string | null): boolean {
  return !!incoming && saved === incoming;
}

// Local view state is an optional convenience, never an account/data source.
// Restore a whitelist, not arbitrary persisted keys such as `data` or actions.
export function normalizeSavedWorkspaceView(value: unknown) {
  const saved = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const ownerId = typeof saved.ownerId === "string" ? saved.ownerId : null;
  const defaults = { ownerId, viewRunId: null as string | null, ranges: [] as TimeRange[],
    visibleLayers: { ...DEFAULT_LAYERS }, placeNotes: {} as Record<string, string>, mapView: { ...DEFAULT_MAP_VIEW } };
  if (!ownerId) return defaults; // Legacy shared-browser state cannot be assigned to a new account.
  defaults.viewRunId = typeof saved.viewRunId === "string" ? saved.viewRunId : null;
  if (Array.isArray(saved.ranges) && saved.ranges.length <= 64) {
    const valid = saved.ranges.filter((range): range is TimeRange => range && typeof range.start === "string" &&
      typeof range.end === "string" && Number.isFinite(Date.parse(range.start)) && Number.isFinite(Date.parse(range.end)) &&
      Date.parse(range.start) < Date.parse(range.end));
    defaults.ranges = normalizeTimeRanges(valid.map(range => ({ start: new Date(range.start).toISOString(), end: new Date(range.end).toISOString() })));
  }
  if (saved.visibleLayers && typeof saved.visibleLayers === "object") {
    for (const key of Object.keys(DEFAULT_LAYERS) as Array<keyof typeof DEFAULT_LAYERS>) {
      const value = (saved.visibleLayers as Record<string, unknown>)[key];
      if (typeof value === "boolean") defaults.visibleLayers[key] = value;
    }
  }
  if (saved.placeNotes && typeof saved.placeNotes === "object" && !Array.isArray(saved.placeNotes)) {
    for (const [key, note] of Object.entries(saved.placeNotes).slice(0, 3000)) {
      if (typeof note === "string" && key.length <= 200 && note.trim()) defaults.placeNotes[key] = note.trim().slice(0, 80);
    }
  }
  const map = saved.mapView as Record<string, unknown> | undefined;
  if (map && Object.keys(DEFAULT_MAP_VIEW).every(key => typeof map[key] === "number" && Number.isFinite(map[key])) &&
    Math.abs(Number(map.longitude)) <= 180 && Math.abs(Number(map.latitude)) <= 90 && Number(map.zoom) >= 0 && Number(map.zoom) <= 24 &&
    Number(map.pitch) >= 0 && Number(map.pitch) <= 85) {
    defaults.mapView = { longitude: Number(map.longitude), latitude: Number(map.latitude), zoom: Number(map.zoom),
      pitch: Number(map.pitch), bearing: Number(map.bearing) };
  }
  return defaults;
}
