import assert from "node:assert/strict";
import test from "node:test";
import { buildTrackGeoJSON } from "./map-track-data.ts";
import type { ViewportPath } from "./workspace-viewport.ts";

const paths: ViewportPath[] = [
  { segmentIndex: 0, rangeIndex: 0, movementClass: "ordinary", vertices: [
    ["2026-01-01T00:00:00Z", 118, 24], ["2026-01-01T00:01:00Z", 118.001, 24],
  ] },
  { segmentIndex: 1, rangeIndex: 0, movementClass: "ordinary", vertices: [
    ["2026-01-01T01:00:00Z", 118.1, 24], ["2026-01-01T01:01:00Z", 118.101, 24],
  ] },
  { segmentIndex: 1, rangeIndex: 1, movementClass: "ordinary", vertices: [
    ["2026-01-02T01:00:00Z", 118.2, 24], ["2026-01-02T01:01:00Z", 118.201, 24],
  ] },
];
const all = { track: true, sparse: true, highSpeed: true };

test("map GeoJSON preserves exactly the input edges across gaps and time slices", () => {
  const data = buildTrackGeoJSON(paths, all);
  const edges = data.features.flatMap((feature) => feature.geometry.coordinates.flatMap((line) =>
    line.slice(1).map((end, index) => [line[index], end]),
  ));
  const expected = paths.flatMap((path) => path.vertices.slice(1).map((end, index) => [
    path.vertices[index].slice(1, 3), end.slice(1, 3),
  ]));
  assert.deepEqual(edges, expected);
  assert.equal(data.features.length, 2);
  assert.equal(data.features[0].geometry.coordinates.length, 2);
});

test("hidden classes and isolated points cannot become connector lines", () => {
  const mixed: ViewportPath[] = [
    paths[0], { ...paths[1], movementClass: "sparse" },
    { ...paths[2], movementClass: "high_speed" },
    { ...paths[0], vertices: [paths[0].vertices[0]] },
  ];
  const data = buildTrackGeoJSON(mixed, { track: true, sparse: false, highSpeed: false });
  assert.equal(data.features.length, 1);
  assert.deepEqual(data.features[0].geometry.coordinates, [[[118, 24], [118.001, 24]]]);
});
