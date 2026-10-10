import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_LAYERS, DEFAULT_MAP_VIEW, normalizeSavedWorkspaceView, sameWorkspaceOwner } from "./workspace-session.ts";

test("workspace view restoration requires the same authenticated owner, never a missing identity", () => {
  assert.equal(sameWorkspaceOwner("a", "a"), true);
  assert.equal(sameWorkspaceOwner("a", "b"), false);
  assert.equal(sameWorkspaceOwner(null, null), false);
  assert.equal(sameWorkspaceOwner("a", null), false);
});

test("unowned legacy view data cannot become a new account's places, camera or time selection", () => {
  const state = normalizeSavedWorkspaceView({ placeNotes: { old: "家" }, ranges: [{ start: "2025-01-01Z", end: "2025-02-01Z" }],
    mapView: { longitude: 118.7, latitude: 30.9, zoom: 10, pitch: 0, bearing: 0 }, visibleLayers: { stays: true } });
  assert.equal(state.ownerId, null);
  assert.deepEqual(state.placeNotes, {});
  assert.deepEqual(state.ranges, []);
  assert.deepEqual(state.mapView, DEFAULT_MAP_VIEW);
  assert.deepEqual(state.visibleLayers, DEFAULT_LAYERS);
});

test("owned local preferences restore only validated view fields, not raw data, status or actions", () => {
  const saved = normalizeSavedWorkspaceView({ ownerId: "a", data: { private: true }, status: "ready", setData: "invalid",
    ranges: [{ start: "2025-04-21T01:03:12+08:00", end: "2025-04-21T02:04:15+08:00" }, { start: "bad", end: "bad" }],
    mapView: { longitude: Infinity, latitude: 0, zoom: 12, pitch: 0, bearing: 0 },
    visibleLayers: { stays: true, track: "false" }, placeNotes: { home: " 家 ", invalid: 12 } });
  assert.equal(saved.ownerId, "a");
  assert.equal("data" in saved, false);
  assert.equal("setData" in saved, false);
  assert.equal("status" in saved, false);
  assert.deepEqual(saved.ranges, [{ start: "2025-04-20T17:03:12.000Z", end: "2025-04-20T18:04:15.000Z" }]);
  assert.equal(saved.visibleLayers.stays, true);
  assert.equal(saved.visibleLayers.track, true);
  assert.deepEqual(saved.placeNotes, { home: "家" });
  assert.deepEqual(saved.mapView, DEFAULT_MAP_VIEW);
});
