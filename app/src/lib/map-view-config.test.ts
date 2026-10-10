import test from "node:test";
import assert from "node:assert/strict";
import { canonicalMapCamera, mapCamerasMatch, parseMapViewConfig, serializeMapView } from "./map-view-config.ts";
import { DEFAULT_LAYERS, DEFAULT_MAP_VIEW, DEFAULT_PANELS } from "./workspace-session.ts";

const config = () => serializeMapView({ mapView: { ...DEFAULT_MAP_VIEW }, visibleLayers: { ...DEFAULT_LAYERS }, panels: { ...DEFAULT_PANELS }, ranges: [
  { start: "2026-02-03T12:34:56+08:00", end: "2026-02-03T12:37:08+08:00" },
  { start: "2025-12-15T00:00:00Z", end: "2025-12-19T09:00:00Z" },
] });

test("complete map config round-trips arbitrary discontinuous ranges and globe camera/layers", () => {
  const value = config();
  assert.deepEqual(parseMapViewConfig(JSON.parse(JSON.stringify(value))), value);
  assert.equal(value.ranges.length, 2);
  assert.equal(value.ranges[1].start, "2026-02-03T04:34:56.000Z");
  assert.equal(value.visibleLayers.stays, false);
  assert.deepEqual(value.map, { projection: "globe", style: "openfreemap-dark-v1", terrain: false, buildings: false, camera: DEFAULT_MAP_VIEW });
  const reordered = { ...value, visibleLayers: Object.fromEntries(Object.entries(value.visibleLayers).reverse()) };
  assert.equal(JSON.stringify(parseMapViewConfig(reordered)), JSON.stringify(value), "JSONB key order cannot cause phantom dirty state");
});

test("camera comparison respects wrapped globe longitude/rotation, with no restore feedback loop", () => {
  assert.deepEqual(canonicalMapCamera({ ...DEFAULT_MAP_VIEW, longitude: 181, bearing: 720 }), { ...DEFAULT_MAP_VIEW, longitude: -179, bearing: 0 });
  assert.equal(mapCamerasMatch({ ...DEFAULT_MAP_VIEW, longitude: 180 }, { ...DEFAULT_MAP_VIEW, longitude: -180 }), true);
  assert.equal(mapCamerasMatch(DEFAULT_MAP_VIEW, { ...DEFAULT_MAP_VIEW, longitude: 105.00000001 }), true);
  assert.equal(mapCamerasMatch(DEFAULT_MAP_VIEW, { ...DEFAULT_MAP_VIEW, zoom: 14 }), false);
  assert.equal(mapCamerasMatch(DEFAULT_MAP_VIEW, { ...DEFAULT_MAP_VIEW, pitch: 20 }), false);
});

test("config rejects unsupported versions, injected fields, invalid coordinates, dates and layers", () => {
  for (const mutate of [
    value => { value.schemaVersion = 2; },
    value => { value.ownerId = "someone-else"; },
    value => { value.map.projection = "mercator"; },
    value => { value.map.camera.zoom = NaN; },
    value => { value.map.camera.latitude = 100; },
    value => { value.map.camera.bearing = 1e30; },
    value => { value.ranges[0].end = value.ranges[0].start; },
    value => { value.ranges[0].start = "not-a-date"; },
    value => { value.ranges[0].start = "2025-12-15T00:00:00"; },
    value => { value.ranges = Array(65).fill(value.ranges[0]); },
    value => { value.visibleLayers.places = "true"; },
    value => { delete value.visibleLayers.gaps; },
    value => { value.panels.inspector = "false"; },
  ] as Array<(value: any) => void>) {
    const value = config(); mutate(value); assert.throws(() => parseMapViewConfig(value));
  }
});
