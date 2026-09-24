import assert from "node:assert/strict";
import test from "node:test";

import { loadViewportPaths, loadWorkspaceEvents } from "./workspace-client.ts";

test("nearby map moves reuse the cached coverage until the user leaves it or changes zoom", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ bounds: [number, number, number, number]; detailZoom: number }> = [];
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body));
    requests.push(body);
    return Response.json({
      paths: [],
      coverageBounds: body.bounds,
      detailZoom: body.detailZoom,
      selectedBounds: null,
      selectedDistanceMeters: 0,
      visibleVertexCount: 0,
    });
  };
  try {
    const ranges = [{ start: "2026-07-01T00:00:00Z", end: "2026-07-02T00:00:00Z" }];
    await loadViewportPaths("cache-dataset", "cache-run", ranges, [118, 24, 119, 25], 10);
    await loadViewportPaths("cache-dataset", "cache-run", ranges, [118.1, 24.1, 119.1, 25.1], 10);
    assert.equal(requests.length, 1);
    await loadViewportPaths("cache-dataset", "cache-run", ranges, [118.1, 24.1, 119.1, 25.1], 11);
    assert.equal(requests.length, 2);
    await loadViewportPaths("cache-dataset", "cache-run", ranges, [121, 30, 122, 31], 11);
    assert.equal(requests.length, 3);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("time events are fetched once per selection", async () => {
  const originalFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => {
    requests += 1;
    return Response.json({ gaps: [], stays: [], trips: [], modeLegs: [] });
  };
  try {
    const ranges = [{ start: "2026-07-03T00:00:00Z", end: "2026-07-04T00:00:00Z" }];
    await loadWorkspaceEvents("event-dataset", "event-run", ranges);
    await loadWorkspaceEvents("event-dataset", "event-run", ranges);
    assert.equal(requests, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
