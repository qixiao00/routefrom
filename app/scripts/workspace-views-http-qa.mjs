import assert from "node:assert/strict";

export async function verifyMapViewsHttp({ request: baseRequest, a, b, subjectA, subjectB, userIdA, libraryIdA, pool, base }) {
  const identities = new Map([[a, subjectA], [b, subjectB]]);
  const request = (endpoint, cookie, options = {}) => baseRequest(endpoint, cookie, {
    ...options, headers: { ...(cookie ? { "x-routefrom-account": identities.get(cookie) } : {}), ...options.headers },
  });
  const endpoint = "/api/workspace/views";
  const json = (method, body) => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const read = async cookie => {
    const response = await request(endpoint, cookie);
    assert.equal(response.status, 200); assert.match(response.headers.get("cache-control"), /private.*no-store/);
    return response.json();
  };
  assert.equal((await request(endpoint)).status, 401);
  assert.equal((await request(endpoint, null, json("PUT", {}))).status, 401);
  assert.equal((await request(endpoint, null, json("POST", {}))).status, 401);
  assert.equal((await request(endpoint, b, { headers: { "x-routefrom-account": subjectA } })).status, 409,
    "an old account's tab cannot read the newly signed-in account's views");
  assert.equal((await request(endpoint, b, { ...json("PUT", {}), headers: { "content-type": "application/json", "x-routefrom-account": subjectA } })).status, 409);
  assert.deepEqual(await read(a), { revision: 0, config: null, updatedAt: null, views: [] });
  const config = { schemaVersion: 1, map: { projection: "globe", style: "openfreemap-dark-v1", terrain: false, buildings: false,
    camera: { longitude: 118.7, latitude: 30.9, zoom: 12.5, pitch: 20, bearing: 35 } },
    visibleLayers: { track: true, sparse: true, highSpeed: false, stays: false, gaps: true, places: false },
    panels: { layers: true, inspector: false },
    ranges: [{ start: new Date(base + 1000).toISOString(), end: new Date(base + 3000).toISOString() },
      { start: new Date(base + 6000).toISOString(), end: new Date(base + 9000).toISOString() }] };
  assert.equal((await request(endpoint, a, json("PUT", { revision: -1, config }))).status, 400);
  assert.equal((await request(endpoint, a, json("PUT", { revision: 0, config: { ...config, schemaVersion: 2 } }))).status, 400);
  for (const title of ["   ", "x".repeat(81)]) {
    assert.equal((await request(endpoint, a, json("POST", { title, config }))).status, 400);
  }
  const foreign = json("PUT", { revision: 0, config }); foreign.headers.origin = "https://untrusted.example";
  assert.equal((await request(endpoint, a, foreign)).status, 403);
  assert.equal((await request(endpoint, a, { method: "PUT", headers: { "content-type": "text/plain" }, body: "{}" })).status, 415);
  assert.equal((await request(endpoint, a, json("PUT", { padding: "x".repeat(33000) }))).status, 413);
  let response = await request(endpoint, a, json("PUT", { revision: 0, config }));
  assert.equal(response.status, 200); assert.equal((await response.json()).revision, 1);
  assert.equal((await request(endpoint, a, json("PUT", { revision: 0, config }))).status, 409);
  // Both requests use the same observed revision. Exactly one can advance it.
  const competitors = [structuredClone(config), structuredClone(config)];
  competitors[0].map.camera.zoom = 13; competitors[1].map.camera.zoom = 14;
  const writes = await Promise.all(competitors.map(value => request(endpoint, a, json("PUT", { revision: 1, config: value }))));
  assert.deepEqual(writes.map(value => value.status).sort(), [200, 409]);
  const winningConfig = competitors[writes.findIndex(value => value.status === 200)];
  assert.equal((await read(a)).revision, 2); assert.deepEqual((await read(a)).config, winningConfig);
  response = await request(endpoint, a, json("POST", { title: "  跨设备 · 任意切片  ", config }));
  assert.equal(response.status, 201); const bookmark = await response.json();
  assert.equal(bookmark.title, "跨设备 · 任意切片"); assert.deepEqual(bookmark.config, config);
  assert.deepEqual(await read(b), { revision: 0, config: null, updatedAt: null, views: [] });
  assert.equal((await request(`${endpoint}/${bookmark.id}`, b, { method: "DELETE" })).status, 404);
  assert.equal((await request(`${endpoint}/${bookmark.id}`, b, { method: "DELETE", headers: { "x-routefrom-account": subjectA } })).status, 409);
  assert.equal((await request(`${endpoint}/${bookmark.id}`, null, { method: "DELETE" })).status, 401);
  assert.equal((await request(`${endpoint}/${bookmark.id}`, a, { method: "DELETE", headers: { origin: "https://untrusted.example" } })).status, 403);
  assert.equal((await request(`${endpoint}/not-an-id`, a, { method: "DELETE" })).status, 404);
  // An empty-library view saved before the first import must not mask its
  // suggested camera/time window when data later becomes available.
  const emptyConfig = structuredClone(config);
  emptyConfig.ranges = []; emptyConfig.visibleLayers.places = true; emptyConfig.panels.inspector = true;
  emptyConfig.map.camera = { longitude: 105, latitude: 28, zoom: 1.5, pitch: 0, bearing: 0 };
  assert.equal((await request(endpoint, b, json("PUT", { revision: 0, config: emptyConfig }))).status, 200);
  return async () => {
    // Called after a different generated dataset has been published.
    const afterProcessing = await read(a);
    assert.deepEqual(afterProcessing.config, winningConfig); assert.equal(afterProcessing.revision, 2);
    assert.deepEqual(afterProcessing.views.map(item => item.id), [bookmark.id]);
    assert.equal((await request(`${endpoint}/${bookmark.id}`, a, { method: "DELETE" })).status, 204);
    assert.equal((await request(`${endpoint}/${bookmark.id}`, a, { method: "DELETE" })).status, 404);
    assert.deepEqual((await read(a)).views, []);
    // Populate only this disposable account to test the real limit without a
    // hundred slow network writes. Cleanup still uses exact owner constraints.
    await pool.query(`INSERT INTO app.library_saved_views (library_id, user_id, title, config)
      SELECT $1, $2, 'limit fixture ' || number, $3::jsonb FROM generate_series(1, 100) AS number`,
      [libraryIdA, userIdA, JSON.stringify(config)]);
    assert.equal((await read(a)).views.length, 100);
    assert.equal((await request(endpoint, a, json("POST", { title: "over limit", config }))).status, 409);
    const deleted = await pool.query("DELETE FROM app.library_saved_views WHERE library_id = $1 AND user_id = $2 AND title LIKE 'limit fixture %'",
      [libraryIdA, userIdA]);
    assert.equal(deleted.rowCount, 100);
    assert.deepEqual((await read(a)).views, []);
  };
}
