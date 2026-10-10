import test from "node:test";
import assert from "node:assert/strict";
import { serializeMapView } from "./map-view-config.ts";
import { MapViewSaveQueue, type ViewSyncState } from "./map-view-save-queue.ts";
import { DEFAULT_LAYERS, DEFAULT_MAP_VIEW, DEFAULT_PANELS } from "./workspace-session.ts";

const config = (zoom = 1) => serializeMapView({ mapView: { ...DEFAULT_MAP_VIEW, zoom }, visibleLayers: DEFAULT_LAYERS, panels: DEFAULT_PANELS, ranges: [] });
const deferred = () => { let resolve!: (value: { revision: number }) => void; const promise = new Promise<{ revision: number }>(done => { resolve = done; }); return { promise, resolve }; };

test("save queue serializes writes and preserves edits made during an in-flight request", async () => {
  const first = deferred(); const calls: Array<{ revision: number; zoom: number }> = []; const states: ViewSyncState[] = [];
  const queue = new MapViewSaveQueue(4, config(1), async (value, revision) => {
    calls.push({ revision, zoom: value.map.camera.zoom });
    return calls.length === 1 ? first.promise : { revision: 6 };
  }, state => states.push(state));
  queue.update(config(2)); const saving = queue.flush();
  queue.update(config(3)); await queue.flush(); queue.update(config(4));
  assert.equal(calls.length, 1);
  first.resolve({ revision: 5 }); await saving;
  assert.deepEqual(calls, [{ revision: 4, zoom: 2 }, { revision: 5, zoom: 4 }]);
  assert.equal(queue.dirty, false); assert.equal(states.at(-1)?.status, "saved");
});

test("conflict/network failure freezes writes until an authoritative reread", async () => {
  for (const error of [Object.assign(new Error("conflict"), { code: "view_conflict" }), new Error("network")]) {
    let writes = 0; const states: ViewSyncState[] = [];
    const queue = new MapViewSaveQueue(1, config(1), async () => { writes++; throw error; }, state => states.push(state));
    queue.update(config(2)); await queue.flush(); queue.update(config(3)); await queue.flush();
    assert.equal(writes, 1); assert.equal(queue.dirty, true);
    assert.equal(states.at(-1)?.status, "code" in error ? "conflict" : "error");
  }
});

test("disposed account queue cannot publish a response or send its pending edits", async () => {
  const first = deferred(); let writes = 0; const states: ViewSyncState[] = [];
  const queue = new MapViewSaveQueue(0, null, async () => { writes++; return first.promise; }, state => states.push(state));
  queue.update(config(2)); const saving = queue.flush(); queue.update(config(3)); queue.dispose();
  const count = states.length; first.resolve({ revision: 1 }); await saving; await queue.flush();
  assert.equal(writes, 1); assert.equal(states.length, count);
});
