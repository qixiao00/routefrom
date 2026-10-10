import test from "node:test";
import assert from "node:assert/strict";
import { serializeMapView } from "./map-view-config.ts";
import { DEFAULT_LAYERS, DEFAULT_MAP_VIEW, DEFAULT_PANELS } from "./workspace-session.ts";
import type { WorkspacePreview } from "./workspace-data.ts";

const preview = (subject: string): WorkspacePreview => ({
  schemaVersion: 1, account: { id: `user-${subject}`, subject, name: "测试", timezone: "Asia/Shanghai" },
  library: { id: `library-${subject}`, state: "ready", publishedAt: "2026-01-02T00:00:00Z" },
  dataset: { id: `dataset-${subject}`, name: "测试", sourceName: "合成数据", pointCount: 2, startedAt: "2026-01-01T00:00:00Z", endedAt: "2026-01-02T00:00:00Z" },
  processing: { runId: `run-${subject}`, algorithmVersion: "test", trajectoryVariant: "cleaned_gps", smoothingConfidence: 0 },
  summary: { gapCount: 0, stayCount: 0, tripCount: 0, placeCount: 0, modeLegCount: 0 },
  suggestedRanges: [{ start: "2026-01-01T00:00:00Z", end: "2026-01-02T00:00:00Z" }],
  suggestedMapView: { ...DEFAULT_MAP_VIEW, longitude: 118.7, latitude: 30.9, zoom: 10 },
  paths: [], gaps: [], stays: [], trips: [], modeLegs: [], places: [],
});

test("workspace store isolates cloud/panel state and restores real upload defaults or complete named views", async t => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  } });
  t.after(() => { if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor); else Reflect.deleteProperty(globalThis, "localStorage"); });
  const { useWorkspaceStore: store } = await import("./workspace-store.ts");
  store.getState().setLoading("a"); store.getState().setData(preview("a"));
  store.getState().setPanels({ layers: false, inspector: false });
  store.getState().setPlaceNote("a-note", "家");
  store.getState().setData(preview("b")); // Defense even if caller skips setLoading.
  assert.deepEqual(store.getState().panels, DEFAULT_PANELS);
  assert.equal(store.getState().cloudViewPending, false);
  assert.deepEqual(store.getState().placeNotes, {});

  const empty = serializeMapView({ mapView: DEFAULT_MAP_VIEW, visibleLayers: DEFAULT_LAYERS, panels: DEFAULT_PANELS, ranges: [] });
  store.getState().applyMapViewConfig(empty);
  assert.deepEqual(store.getState().mapView, preview("b").suggestedMapView, "empty-library config cannot reset uploaded data to world zoom");
  assert.deepEqual(store.getState().ranges, preview("b").suggestedRanges);
  const named = serializeMapView({ mapView: { ...DEFAULT_MAP_VIEW, longitude: 181, bearing: 720, zoom: 14, pitch: 20 },
    visibleLayers: { ...DEFAULT_LAYERS, track: false }, panels: { layers: true, inspector: false }, ranges: [
      { start: "2025-07-03T01:02:03Z", end: "2025-07-03T02:04:06Z" },
      { start: "2026-09-07T08:01:02Z", end: "2026-09-10T03:05:07Z" },
    ] });
  store.getState().setSelection({ kind: "stay", id: "old-event" });
  store.getState().applyMapViewConfig(named);
  assert.deepEqual(serializeMapView(store.getState()), named);
  assert.equal(store.getState().selection, null);
  assert.equal(store.getState().cloudViewPending, true);
  store.getState().setLoading("c");
  assert.deepEqual(store.getState().panels, DEFAULT_PANELS);
  assert.equal(store.getState().cloudViewPending, false);
});
