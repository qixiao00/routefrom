import assert from "node:assert/strict";
import test from "node:test";
import { aggregateOrdinaryRoutes } from "./map-route-aggregation.ts";
import { buildPlaceGeoJSON } from "./map-place-data.ts";
import type { ViewportPath } from "./workspace-viewport.ts";
import type { PreviewPlace } from "./workspace-data.ts";

function path(id: number, points: number[][], rangeIndex = 0): ViewportPath {
  return { segmentIndex: id, rangeIndex, movementClass: "ordinary", vertices: points.map(([x, y], i) => [`2026-01-01T00:00:${String(i).padStart(2, "0")}Z`, x / 111320, y / 111320]) };
}
function lengthByCount(paths: ViewportPath[], zoom = 14) {
  const result = new Map<number, number>();
  for (const f of aggregateOrdinaryRoutes(paths, zoom).features) {
    const count = f.properties!.traversalCount;
    const length = f.geometry.coordinates.reduce((n, line) => n + line.slice(1).reduce((m, b, i) => m + Math.hypot(line[i][0] - b[0], line[i][1] - b[1]) * 111320, 0), 0);
    result.set(count, (result.get(count) ?? 0) + length);
  }
  return result;
}
test("duplicate/reverse routes across time slices become one wider route", () => {
  const counts = lengthByCount([path(0, [[0, 0], [100, 0]]), path(1, [[100, 0], [0, 0]], 1)]);
  assert.ok(Math.abs(counts.get(2)! - 100) < 0.01);
  assert.equal(counts.size, 1);
});
test("jitter and different sample density merge without counting each sample as a pass", () => {
  const counts = lengthByCount([path(0, [[0, 0], [100, 0]]), path(1, [[0, 3], [20, 3], [50, 3], [100, 3]])]);
  assert.ok(Math.abs(counts.get(2)! - 100) < 0.01);
  assert.equal(counts.size, 1);
});
test("only overlapping section thickens; branches remain separate", () => {
  const counts = lengthByCount([path(0, [[0, 0], [100, 0]]), path(1, [[50, 0], [150, 0]])]);
  assert.ok(Math.abs(counts.get(2)! - 50) < 0.01);
  assert.ok(Math.abs(counts.get(1)! - 100) < 0.01);
});
test("crossings, parallel roads beyond tolerance, gaps and uncertain classes stay separate", () => {
  const counts = lengthByCount([
    path(0, [[0, 0], [100, 0]]), path(1, [[0, 12], [100, 12]]),
    path(2, [[50, -50], [50, 50]]), path(3, [[200, 0], [250, 0]]),
    { ...path(4, [[0, 0], [100, 0]]), movementClass: "sparse" },
  ]);
  assert.equal(counts.size, 1);
  assert.ok(Math.abs(counts.get(1)! - 350) < 0.01);
});
test("zoom separates nearby parallel observations and duplicate input cannot inflate counts", () => {
  const first = path(0, [[0, 0], [100, 0]]);
  assert.equal(lengthByCount([first, first]).size, 1);
  assert.ok(Math.abs(lengthByCount([first, first]).get(1)! - 100) < 0.01);
  assert.ok(Math.abs(lengthByCount([first, path(1, [[0, 3], [100, 3]])], 19).get(1)! - 200) < 0.01);
});

function place(id: string, x: number, visits = 1): PreviewPlace {
  return { id, name: id, position: [x / 111320, 0], visitCount: visits, dwellSeconds: visits * 100,
    confidence: 0.9, frequentProbability: 0.8, firstVisitedAt: "2026-01-01", lastVisitedAt: "2026-01-02" };
}
test("overlapping places combine visits, membership and selection without mutating originals", () => {
  const places = [place("a", 0, 3), place("b", 10, 2), place("c", 400)];
  const before = JSON.stringify(places);
  const merged = buildPlaceGeoJSON(places, 15, "b");
  assert.equal(merged.features.length, 2);
  assert.equal(merged.features[0].properties!.visitCount, 5);
  assert.equal(merged.features[0].properties!.selected, true);
  assert.deepEqual(JSON.parse(merged.features[0].properties!.memberIds), ["a", "b"]);
  assert.equal(JSON.stringify(places), before);
  assert.equal(buildPlaceGeoJSON(places, 20).features.length, 3);
});
test("place groups have bounded diameter and do not chain across a neighborhood", () => {
  const result = buildPlaceGeoJSON([place("a", 0), place("b", 100), place("c", 200)], 10);
  assert.equal(result.features.length, 2);
  assert.equal(result.features[0].properties!.placeCount, 2);
  assert.equal(buildPlaceGeoJSON([place("a", 0), place("b", 0)], 22).features.length, 1);
});
