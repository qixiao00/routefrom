import assert from "node:assert/strict";
import test from "node:test";

import {
  WorkspaceQueryValidationError,
  buildTrajectoryPaths,
  parseWorkspaceQuery,
  type StoredTrajectoryVertex,
} from "./workspace-query.ts";

const datasetId = "ab96de99-f2d3-402b-ad2b-c756e05d4d62";

test("arbitrary overlapping ranges are normalized without calendar buckets", () => {
  const query = parseWorkspaceQuery({
    datasetId,
    processingRunId: "active",
    timeSelection: {
      timezone: "Asia/Shanghai",
      ranges: [
        { start: "2026-07-15T10:00:30+08:00", end: "2026-07-15T10:00:50+08:00" },
        { start: "2026-07-15T10:00:00+08:00", end: "2026-07-15T10:00:20+08:00" },
        { start: "2026-07-15T10:00:15+08:00", end: "2026-07-15T10:00:30+08:00" },
        { start: "2027-02-03T17:12:00+08:00", end: "2027-02-03T17:13:00+08:00" },
      ],
    },
  });

  assert.deepEqual(query.timeSelection.ranges, [
    { start: "2026-07-15T02:00:00.000Z", end: "2026-07-15T02:00:50.000Z" },
    { start: "2027-02-03T09:12:00.000Z", end: "2027-02-03T09:13:00.000Z" },
  ]);
});

test("untrusted query fields, naive timestamps and excessive budgets are rejected", () => {
  assert.throws(
    () =>
      parseWorkspaceQuery({
        datasetId,
        processingRunId: "active",
        timeSelection: {
          timezone: "Asia/Shanghai",
          ranges: [{ start: "2026-07-01T00:00:00", end: "2026-07-01T00:01:00" }],
        },
        databaseUrl: "must-not-be-accepted",
      }),
    WorkspaceQueryValidationError,
  );
  assert.throws(
    () =>
      parseWorkspaceQuery({
        datasetId,
        processingRunId: "active",
        timeSelection: {
          timezone: "Asia/Shanghai",
          ranges: [{ start: "2026-07-01T00:00:00Z", end: "2026-07-01T00:01:00Z" }],
        },
        trajectoryDetail: { vertexBudget: 250_001 },
      }),
    /vertexBudget/,
  );
});

test("discontinuous selections are clipped into separate paths with boundary interpolation", () => {
  const base = Date.parse("2026-07-01T00:00:00.000Z");
  const rows: StoredTrajectoryVertex[] = Array.from({ length: 11 }, (_, index) => ({
    selectionRangeIndex: index <= 5 ? 0 : 1,
    segmentIndex: 0,
    sequenceNumber: index,
    recordedAt: new Date(base + index * 10_000).toISOString(),
    longitude: 121.49 + index * 0.0002,
    latitude: 31.2,
    sourcePointId: String(1000 + index),
    importanceMeters: index === 0 || index === 10 ? null : index,
    anchorReasons: index === 0 || index === 10 ? ["continuity_boundary"] : [],
  }));
  // Each selected range receives the full candidate chunk window from SQL.
  const candidateRows = [
    ...rows.slice(0, 6).map((row) => ({ ...row, selectionRangeIndex: 0 })),
    ...rows.slice(5).map((row) => ({ ...row, selectionRangeIndex: 1 })),
  ];
  const ranges = [
    { start: "2026-07-01T00:00:15.000Z", end: "2026-07-01T00:00:35.000Z" },
    { start: "2026-07-01T00:01:05.000Z", end: "2026-07-01T00:01:25.000Z" },
  ];

  const paths = buildTrajectoryPaths(candidateRows, ranges, "cleaned_gps", {
    pixelToleranceMeters: 0,
  });

  assert.equal(paths.length, 2);
  assert.equal(paths[0].vertices[0].recordedAt, ranges[0].start);
  assert.equal(paths[0].vertices.at(-1)?.recordedAt, ranges[0].end);
  assert.equal(paths[1].vertices[0].recordedAt, ranges[1].start);
  assert.equal(paths[1].vertices.at(-1)?.recordedAt, ranges[1].end);
  assert.equal(paths[0].vertices[0].isInterpolated, true);
  assert.equal(paths[1].vertices.at(-1)?.isInterpolated, true);
  assert.notEqual(paths[0].selectionRangeIndex, paths[1].selectionRangeIndex);
});

test("global vertex budget is shared across all returned paths", () => {
  const base = Date.parse("2026-07-01T00:00:00.000Z");
  const rows: StoredTrajectoryVertex[] = Array.from({ length: 20 }, (_, index) => ({
    selectionRangeIndex: 0,
    segmentIndex: 0,
    sequenceNumber: index,
    recordedAt: new Date(base + index * 10_000).toISOString(),
    longitude: 121.49 + index * 0.0001,
    latitude: 31.2,
    sourcePointId: String(index + 1),
    importanceMeters: index === 0 || index === 19 ? null : index,
    anchorReasons: index === 0 || index === 19 ? ["continuity_boundary"] : [],
  }));
  const paths = buildTrajectoryPaths(
    rows,
    [{ start: rows[0].recordedAt, end: new Date(base + 191_000).toISOString() }],
    "cleaned_gps",
    { pixelToleranceMeters: 0, vertexBudget: 6 },
  );

  assert.equal(paths[0].vertices.length, 6);
  assert.deepEqual(
    paths[0].vertices.slice(1, -1).map((vertex) => vertex.importanceMeters),
    [15, 16, 17, 18],
  );
});
