import assert from "node:assert/strict";
import test from "node:test";

import {
  selectPaths,
  selectedDistanceMeters,
  simplifySelectedPath,
  type PreviewPath,
} from "./workspace-data.ts";

const base = Date.parse("2026-07-01T00:00:00.000Z");
const paths: PreviewPath[] = [
  {
    segmentIndex: 4,
    vertices: Array.from({ length: 7 }, (_, index) => [
      new Date(base + index * 10_000).toISOString(),
      121.49 + index * 0.001,
      31.2,
    ]),
  },
];

test("multiple discontinuous ranges remain separate and interpolate exact boundaries", () => {
  const selected = selectPaths(paths, [
    { start: "2026-07-01T00:00:05.000Z", end: "2026-07-01T00:00:15.000Z" },
    { start: "2026-07-01T00:00:35.000Z", end: "2026-07-01T00:00:55.000Z" },
  ]);

  assert.equal(selected.length, 2);
  assert.equal(selected[0].rangeIndex, 0);
  assert.equal(selected[1].rangeIndex, 1);
  assert.equal(selected[0].vertices[0][0], "2026-07-01T00:00:05.000Z");
  assert.equal(selected[0].vertices.at(-1)?.[0], "2026-07-01T00:00:15.000Z");
  assert.equal(selected[1].vertices[0][0], "2026-07-01T00:00:35.000Z");
  assert.equal(selected[1].vertices.at(-1)?.[0], "2026-07-01T00:00:55.000Z");
});

test("selected distance sums only confirmed vertices inside selected ranges", () => {
  const selected = selectPaths(paths, [
    { start: "2026-07-01T00:00:10.000Z", end: "2026-07-01T00:00:30.000Z" },
  ]);

  const distance = selectedDistanceMeters(selected);
  assert.ok(distance > 180);
  assert.ok(distance < 200);
});

test("map precision reveals intermediate geometry without changing confirmed distance", () => {
  const path = {
    segmentIndex: 9,
    rangeIndex: 0,
    vertices: [
      ["2026-07-01T00:00:00.000Z", 121.49, 31.2, null],
      ["2026-07-01T00:00:10.000Z", 121.491, 31.201, 3],
      ["2026-07-01T00:00:20.000Z", 121.492, 31.2, 30],
      ["2026-07-01T00:00:30.000Z", 121.493, 31.201, null],
    ],
  } satisfies import("./workspace-data.ts").SelectedPath;

  const distance = selectedDistanceMeters([path]);
  assert.equal(simplifySelectedPath(path, 50).length, 2);
  assert.equal(simplifySelectedPath(path, 10).length, 3);
  assert.equal(simplifySelectedPath(path, 1).length, 4);
  assert.equal(selectedDistanceMeters([path]), distance);
});

test("time slicing preserves importance on existing vertices and anchors new boundaries", () => {
  const source: PreviewPath = {
    segmentIndex: 10,
    vertices: [
      ["2026-07-01T00:00:00.000Z", 121.49, 31.2, null],
      ["2026-07-01T00:00:10.000Z", 121.491, 31.201, 3],
      ["2026-07-01T00:00:20.000Z", 121.492, 31.2, 30],
      ["2026-07-01T00:00:30.000Z", 121.493, 31.201, null],
    ],
  };
  const selected = selectPaths([source], [
    { start: "2026-07-01T00:00:05.000Z", end: "2026-07-01T00:00:25.000Z" },
  ]);

  assert.equal(selected.length, 1);
  assert.deepEqual(selected[0].vertices.map((vertex) => vertex[3]), [null, 3, 30, null]);
  assert.equal(simplifySelectedPath(selected[0], 10).length, 3);
});
