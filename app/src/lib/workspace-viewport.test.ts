import assert from "node:assert/strict";
import test from "node:test";

import type { PreviewPath } from "./workspace-data.ts";
import {
  buildViewportSelection,
  parseViewportRequest,
  queryViewportSelection,
  type ViewportBounds,
} from "./workspace-viewport.ts";

const start = Date.parse("2026-07-01T00:00:00Z");
const instant = (seconds: number) => new Date(start + seconds * 1000).toISOString();
const range = { start: instant(0), end: instant(300) };

test("viewport query validates bounds and normalizes independent time slices", () => {
  const query = parseViewportRequest({
    datasetId: "dataset",
    processingRunId: "run",
    ranges: [
      { start: instant(100), end: instant(150) },
      { start: instant(0), end: instant(50) },
    ],
    bounds: [118, 24, 119, 25],
    detailZoom: 11,
  });
  assert.equal(query.ranges.length, 2);
  assert.equal(query.ranges[0].start, instant(0));
  assert.throws(() => parseViewportRequest({ ...query, bounds: [119, 24, 118, 25] }));
  assert.throws(() => parseViewportRequest({
    ...query,
    ranges: [{ start: "2026-07-01T00:00:00", end: instant(50) }],
  }));
});

test("spatial clipping keeps crossing edges and never joins disconnected pieces", () => {
  const path: PreviewPath = {
    segmentIndex: 7,
    vertices: [
      [instant(0), 117.8, 24.5, null],
      [instant(30), 118.2, 24.5, 100],
      [instant(60), 117.8, 24.5, 100],
      [instant(90), 117.7, 24.5, 100],
      [instant(120), 118.2, 24.5, 100],
      [instant(150), 117.8, 24.5, null],
    ],
  };
  const selection = buildViewportSelection([path], [range]);
  const bounds: ViewportBounds = [118.0, 24.4, 118.1, 24.6];
  const result = queryViewportSelection(selection, bounds, 13);

  assert.equal(result.paths.length, 2);
  assert.deepEqual(result.paths.map((piece) => piece.vertices.length), [3, 3]);
  assert.equal(result.selectedDistanceMeters, selection.distanceMeters);
  assert.deepEqual(result.selectedBounds, [117.7, 24.5, 118.2, 24.5]);
});

test("zoom-dependent budget caps optional vertices without dropping path endpoints", () => {
  const path: PreviewPath = {
    segmentIndex: 8,
    vertices: Array.from({ length: 20 }, (_, index) => [
      instant(index * 10),
      118 + index * 0.001,
      24.5 + (index % 2) * 0.001,
      index === 0 || index === 19 ? null : 100,
    ]),
  };
  const selection = buildViewportSelection([path], [range]);
  const result = queryViewportSelection(selection, [117.9, 24.4, 118.1, 24.6], 16, 6);

  assert.equal(result.visibleVertexCount, 6);
  assert.equal(result.paths[0].vertices[0][0], instant(0));
  assert.equal(result.paths[0].vertices.at(-1)?.[0], instant(190));
  assert.equal(selection.paths[0].path.vertices.length, 20);
});

test("normal viewport loading reveals additional geometry as the map zooms in", () => {
  const path: PreviewPath = {
    segmentIndex: 10,
    vertices: Array.from({ length: 20 }, (_, index) => [
      instant(index * 10),
      118 + index * 0.001,
      24.5 + (index % 2) * 0.001,
      index === 0 || index === 19 ? null : 100,
    ]),
  };
  const selection = buildViewportSelection([path], [range]);
  const bounds: ViewportBounds = [117.9, 24.4, 118.1, 24.6];
  const overview = queryViewportSelection(selection, bounds, 4);
  const detail = queryViewportSelection(selection, bounds, 16);

  assert.equal(overview.visibleVertexCount, 2);
  assert.equal(detail.visibleVertexCount, 20);
  assert.deepEqual(overview.paths[0].vertices.map((vertex) => vertex[0]), [instant(0), instant(190)]);
});

test("high-speed edges are isolated without losing or bridging confirmed edges", () => {
  const path: PreviewPath = {
    segmentIndex: 11,
    vertices: [
      [instant(0), 118, 24.5, null],
      [instant(60), 118.001, 24.5, 100],
      [instant(120), 118.101, 24.5, 100],
      [instant(180), 118.102, 24.5, null],
    ],
  };
  const selection = buildViewportSelection([path], [range]);
  const result = queryViewportSelection(selection, [117.9, 24.4, 118.2, 24.6], 16);

  assert.deepEqual(result.paths.map((piece) => piece.movementClass), [
    "ordinary", "high_speed", "ordinary",
  ]);
  assert.deepEqual(result.paths.map((piece) => piece.vertices.map((vertex) => vertex[0])), [
    [instant(0), instant(60)],
    [instant(60), instant(120)],
    [instant(120), instant(180)],
  ]);
  assert.equal(result.selectedDistanceMeters, selection.distanceMeters);
});

test("a long low-speed observation interval is not presented as an exact route", () => {
  const path: PreviewPath = {
    segmentIndex: 13,
    vertices: [
      [instant(0), 118, 24.5, null],
      [instant(60), 118.001, 24.5, 100],
      [instant(180), 118.041, 24.5, 100],
      [instant(240), 118.042, 24.5, null],
    ],
  };
  const selection = buildViewportSelection([path], [range]);
  const result = queryViewportSelection(selection, [117.9, 24.4, 118.2, 24.6], 16);

  assert.deepEqual(result.paths.map((piece) => piece.movementClass), [
    "ordinary", "sparse", "ordinary",
  ]);
  assert.equal(result.paths[0].vertices.at(-1)?.[0], result.paths[1].vertices[0][0]);
  assert.equal(result.paths[1].vertices.at(-1)?.[0], result.paths[2].vertices[0][0]);
  assert.equal(result.selectedDistanceMeters, selection.distanceMeters);
});

test("air mode is displayed separately even during slow takeoff and landing samples", () => {
  const path: PreviewPath = {
    segmentIndex: 12,
    vertices: [
      [instant(0), 118, 24.5, null],
      [instant(60), 118.001, 24.5, 100],
      [instant(120), 118.002, 24.5, null],
    ],
  };
  const selection = buildViewportSelection([path], [range], [{
    id: "air-leg",
    start: instant(0),
    end: instant(180),
    mode: "air",
    level: "specific",
    confidence: 0.7,
  }]);
  const result = queryViewportSelection(selection, [117.9, 24.4, 118.1, 24.6], 16);

  assert.deepEqual(result.paths.map((piece) => piece.movementClass), ["high_speed"]);
  assert.equal(result.paths[0].vertices[0][0], instant(0));
  assert.equal(result.paths[0].vertices.at(-1)?.[0], instant(120));
});

test("discontinuous time ranges never become a single map path", () => {
  const path: PreviewPath = {
    segmentIndex: 9,
    vertices: Array.from({ length: 10 }, (_, index) => [
      instant(index * 30),
      118 + index * 0.001,
      24.5,
      index === 0 || index === 9 ? null : 50,
    ]),
  };
  const selection = buildViewportSelection([path], [
    { start: instant(15), end: instant(75) },
    { start: instant(165), end: instant(225) },
  ]);
  const result = queryViewportSelection(selection, [117.9, 24.4, 118.1, 24.6], 14);

  assert.deepEqual(result.paths.map((piece) => piece.rangeIndex), [0, 1]);
});
