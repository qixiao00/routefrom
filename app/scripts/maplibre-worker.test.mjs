import assert from "node:assert/strict";
import { once } from "node:events";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { Worker } from "node:worker_threads";
import test from "node:test";
import { buildTrackGeoJSON } from "../src/lib/map-track-data.ts";

const require = createRequire(import.meta.url);
const packageRoot = path.dirname(require.resolve("maplibre-gl/package.json"));
const { version } = JSON.parse(await readFile(path.join(packageRoot, "package.json"), "utf8"));
const publicRoot = new URL(`../public/maplibre/${version}/`, import.meta.url);

test("published worker and sibling match the installed MapLibre version", async () => {
  for (const name of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
    assert.deepEqual(
      await readFile(new URL(name, publicRoot)),
      await readFile(path.join(packageRoot, "dist", name)),
    );
  }
});

test("published worker indexes map GeoJSON without joining disjoint fragments", { timeout: 10000 }, async () => {
  // Exercise the shipped ESM files in an isolated worker. Only the browser
  // worker scope is shimmed; the actual MapLibre worker and GeoJSON index run.
  const code = `
    import { parentPort, workerData } from 'node:worker_threads';
    globalThis.WorkerGlobalScope = class extends EventTarget {};
    globalThis.self = new WorkerGlobalScope();
    globalThis.location = { origin: 'https://routefrom.test' };
    self.postMessage = () => {};
    await import(workerData.url);
    if (!self.worker) throw new Error('Worker entry did not install its message handler');
    await self.worker.actor.messageHandlers.LD('smoke', {
      type: 'geojson', source: 'track',
      geojsonVtOptions: { extent: 8192, maxZoom: 18, buffer: 128, tolerance: 0, cluster: false },
      data: workerData.geojson
    });
    const tile = self.worker.workerSources.smoke.geojson.track._geoJSONIndex.getTile(0,0,0);
    parentPort.postMessage({
      indexedFeatures: tile?.features.length ?? 0,
      lineVertexCounts: tile?.features.map(feature => feature.geometry.map(line => line.length))
    });
  `;
  const geojson = buildTrackGeoJSON([0, 1, 2].map((index) => ({
    segmentIndex: index,
    rangeIndex: index === 2 ? 1 : 0,
    movementClass: "ordinary",
    vertices: [["2026-01-01T00:00:00Z", index * 3, 0], ["2026-01-01T01:00:00Z", index * 3 + 1, 0]],
  })), { track: true, sparse: true, highSpeed: true });
  const worker = new Worker(new URL(`data:text/javascript,${encodeURIComponent(code)}`), {
    workerData: { url: new URL("maplibre-gl-worker.mjs", publicRoot).href, geojson },
  });
  try {
    const [result] = await once(worker, "message", { signal: AbortSignal.timeout(8000) });
    assert.equal(result.indexedFeatures, 2);
    assert.deepEqual(result.lineVertexCounts, [[2, 2], [2]]);
  } finally {
    await worker.terminate();
  }
});
