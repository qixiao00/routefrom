import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { setDefaultResultOrder } from "node:dns";
import { lookup } from "node:dns/promises";
import { mkdtemp, realpath, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { promisify } from "node:util";
import { createInterface } from "node:readline";
import test from "node:test";
import { Pool } from "pg";
import { cleanupTestAccounts } from "./library-test-cleanup.mjs";
import { verifyMapViewsHttp } from "./workspace-views-http-qa.mjs";
import { verifyPlaceNotesHttp } from "./place-notes-http-qa.mjs";
import { verifyCsvExports, verifyCsvParser } from "./csv-export-http-qa.mjs";

const execute = promisify(execFile);
const header = "geoTime,latitude,longitude,wgsLatitude,wgsLongitude,altitude,course,horizontalAccuracy,verticalAccuracy,speed,dayTimeMills,networkType,networkName,locationType\n";
const csv = (entries) => header + entries.map(([time, longitude, speed = -1]) => `${time},30.9,${longitude},30.9,${longitude},50,-1,5,-1,${speed},1682006400000,0,,1\n`).join("");

const exportValidationOnly = process.env.ROUTEFROM_EXPORT_VALIDATION_ONLY === "true";
test(exportValidationOnly ? "CSV export validation: authenticated malformed options and stale page identity (no imports)"
  : process.env.ROUTEFROM_BROWSER_SMOKE_ONLY === "true"
  ? "website map: published algorithm data and isolated browser account switch (short fixture)"
  : "website library: private multipart imports, dedup, isolation, conflicts, withdrawal and restore", {
  skip: process.env.ROUTEFROM_RUN_DATABASE_TESTS !== "true",
  timeout: process.env.ROUTEFROM_BROWSER_QA === "true" ? 900_000 : 600_000,
}, async () => {
  verifyCsvParser();
  const database = process.env.ROUTEFROM_TEST_DATABASE || "routefrom_integration";
  assert.match(database, /^routefrom_integration/);
  const preferIpv4 = process.env.ROUTEFROM_TEST_IPV4 === "true";
  if (preferIpv4) setDefaultResultOrder("ipv4first");
  const url = new URL(process.env.DATABASE_URL_DIRECT);
  url.pathname = `/${database}`;
  const webUrl = new URL(process.env.DATABASE_URL);
  webUrl.pathname = `/${database}`;
  const pool = new Pool({ connectionString: webUrl.toString(), max: 1, connectionTimeoutMillis: 10_000, query_timeout: 30_000 });
  const root = await mkdtemp(path.join(os.tmpdir(), "routefrom-http-test-"));
  const baseURL = "http://127.0.0.1:3100";
  const env = { ...process.env, DATABASE_URL: webUrl.toString(), DATABASE_URL_DIRECT: url.toString(),
    BETTER_AUTH_URL: baseURL, FOOTPRINT_DATA_DIR: root };
  if (preferIpv4) {
    env.NODE_OPTIONS = `${env.NODE_OPTIONS || ""} --dns-result-order=ipv4first`.trim();
    // Diagnostic option for a local IPv6 path with intermittent TLS stalls.
    // libpq keeps the original hostname for SNI/certificate verification.
    env.PGHOSTADDR = (await lookup(url.hostname, { family: 4 })).address;
  }
  const identities = [];
  const browserAccounts = [];
  let targetLibraryId;
  let server;
  // Capture output only for failure diagnostics; never print environment variables or URLs containing credentials.
  let logs = "";
  const checkpoint = label => { if (process.env.ROUTEFROM_HTTP_VERBOSE === "true") console.log(`HTTP checkpoint: ${label}`); };
  const request = (endpoint, cookie, options = {}) => fetch(baseURL + endpoint, {
    signal: AbortSignal.timeout(30_000), ...options, headers: { origin: baseURL, ...(cookie ? { cookie } : {}), ...options.headers },
  });
  const action = (endpoint, cookie, body) => request(endpoint, cookie, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const library = async (cookie) => {
    const response = await request("/api/library", cookie);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("cache-control"), /no-store/);
    return response.json();
  };
  const bootstrap = async cookie => {
    const response = await request("/api/workspace/bootstrap", cookie);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("cache-control"), /private.*no-store/);
    return response.json();
  };
  const upload = async (cookie, contents, name = "test.csv") => {
    const form = new FormData();
    form.append("file", new File([contents], name, { type: "text/csv" }));
    return request("/api/library/imports", cookie, { method: "POST", body: form });
  };
  const work = async (libraryId = targetLibraryId) => {
    assert.ok(libraryId, "worker fixture must be scoped to its own library");
    const python = path.resolve("../.venv/Scripts/python.exe");
    const started = Date.now();
    await execute(python, ["-m", "routefrom_pipeline.library_worker", "--once", "--data-dir", root, "--library-id", libraryId],
      { cwd: path.resolve("../processor"), env, windowsHide: true, timeout: 90_000 });
    checkpoint(`scoped worker completed in ${((Date.now() - started) / 1000).toFixed(1)} seconds`);
  };
  try {
    const version = await pool.query("SELECT max(filename) AS filename FROM routefrom_meta.schema_migrations");
    assert.equal(version.rows[0].filename, "0019_library_place_notes.sql", "apply current migrations to the test database first");
    checkpoint("database ready");
    server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "--port", "3100"],
      { env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    server.stdout.on("data", data => { logs = (logs + data).slice(-6000); });
    server.stderr.on("data", data => { logs = (logs + data).slice(-6000); });
    for (let attempt = 0; attempt < 40; attempt++) {
      try { if ((await request("/login")).ok) break; } catch { /* server booting */ }
      assert.equal(server.exitCode, null, "test server exited early");
      await delay(250);
    }
    assert.equal((await request("/api/library")).status, 401);
    assert.equal((await request("/", null, { redirect: "manual" })).status, 307);
    assert.equal((await request("/library", null, { redirect: "manual" })).status, 307);
    assert.equal((await request("/api/workspace/bootstrap")).status, 401);
    for (const endpoint of ["viewport", "events", "query"]) {
      assert.equal((await action(`/api/workspace/${endpoint}`, null, {})).status, 401);
    }
    const signup = async () => {
      const email = `${randomUUID()}@example.invalid`;
      const password = randomUUID();
      identities.push(email);
      const response = await action("/api/auth/sign-up/email", null, { email, name: "Library HTTP test", password });
      assert.equal(response.status, 200);
      browserAccounts.push({ email, password });
      return response.headers.getSetCookie().map(value => value.split(";", 1)[0]).join("; ");
    };
    const a = await signup();
    const b = await signup();
    checkpoint("two accounts ready");
    const empty = await bootstrap(a);
    targetLibraryId = empty.library.id;
    const secondBootstrap = await bootstrap(b);
    const secondLibraryId = secondBootstrap.library.id;
    assert.equal(empty.library.state, "empty");
    assert.equal(empty.dataset.pointCount, 0);
    assert.deepEqual(empty.paths, []);
    assert.deepEqual(empty.places, []);
    assert.deepEqual(empty.suggestedRanges, []);
    if (exportValidationOnly) {
      const query = { datasetId: "11111111-1111-4111-8111-111111111111", processingRunId: "22222222-2222-4222-8222-222222222222",
        kind: "observations", timeSelection: { timezone: "Asia/Shanghai", ranges: [{ start: "2026-10-01T00:00:00Z", end: "2026-10-02T00:00:00Z" }] } };
      const url = (body, subject = empty.account.subject) => "/api/library/export?" + new URLSearchParams({ account: subject, query: JSON.stringify(body) });
      for (const kind of [["observations"], null, {}, true, "map_matched"]) {
        for (const method of ["GET", "HEAD"]) {
          const response = await request(url({ ...query, kind }), a, { method });
          assert.equal(response.status, 400); assert.match(response.headers.get("cache-control"), /private.*no-store/);
          if (method === "HEAD") assert.equal(response.headers.get("x-routefrom-export-error"), "invalid_export");
          else assert.equal((await response.json()).error.code, "invalid_export");
        }
      }
      assert.equal((await request(url(query), a)).status, 404);
      assert.equal((await request(url(query, secondBootstrap.account.subject), a)).status, 409);
      checkpoint("strict export kinds and stale page identity validated without importing data");
      return;
    }
    assert.equal((await request("/api/library/imports", a, { method: "POST", headers: { origin: "https://untrusted.example" } })).status, 403);
    const base = 1682028286912;
    const verifyViewsAfterPublication = await verifyMapViewsHttp({ request, a, b, subjectA: empty.account.subject,
      subjectB: secondBootstrap.account.subject, userIdA: empty.account.id, libraryIdA: empty.library.id, pool, base });
    checkpoint("cloud views: validation, atomic revisions and isolation passed");
    if (process.env.ROUTEFROM_BROWSER_SMOKE_ONLY !== "true") {
    const firstCsv = csv([[base, "118.7"], [base + 10000, "118.7001"]]);
    let response = await upload(a, firstCsv);
    assert.equal(response.status, 202);
    const first = (await response.json()).job.id;
    response = await upload(a, firstCsv);
    assert.equal(response.status, 202);
    assert.equal((await response.json()).job.id, first, "pending duplicate reuses its job");
    await work();
    checkpoint("first snapshot published");
    let state = await library(a);
    assert.equal(state.jobs[0].status, "succeeded", state.jobs[0].error_message);
    assert.equal(state.jobs[0].metrics.point_count, 2);
    assert.equal(state.files.length, 1);
    assert.equal(state.user.name, "Library HTTP test");
    assert.equal(Number(state.published.point_count), 2);
    assert.equal(state.files[0].dataset_id, state.jobs[0].dataset_id);
    const map = await bootstrap(a);
    assert.equal(map.library.state, "ready");
    assert.equal(map.dataset.pointCount, 2);
    assert.equal(map.dataset.id === state.files[0].dataset_id, false, "map reads merged published data, not an unprocessed raw upload");
    assert.deepEqual(map.paths, [], "bootstrap cannot overfetch all trajectory vertices");
    const ids = { datasetId: map.dataset.id, processingRunId: map.processing.runId };
    const storedChunks = await pool.query(`SELECT count(*) AS count, bool_and(bounds IS NOT NULL AND ST_IsValid(bounds)) AS valid
      FROM app.trajectory_chunks AS chunk JOIN app.trajectory_variants AS variant ON variant.id = chunk.trajectory_variant_id
      WHERE variant.processing_run_id = $1`, [ids.processingRunId]);
    assert.ok(Number(storedChunks.rows[0].count) > 0);
    assert.equal(storedChunks.rows[0].valid, true, "degenerate horizontal chunks still have indexed polygon envelopes");
    const ranges = [{ start: new Date(base + 1000).toISOString(), end: new Date(base + 3000).toISOString() },
      { start: new Date(base + 6000).toISOString(), end: new Date(base + 9000).toISOString() }];
    const viewportBody = { ...ids, ranges, bounds: [118.69, 30.89, 118.71, 30.91], detailZoom: 15 };
    let viewportResponse = await action("/api/workspace/viewport", a, viewportBody);
    assert.equal(viewportResponse.status, 200);
    const viewport = await viewportResponse.json();
    assert.equal(viewport.paths.length, 2, "discontinuous slices remain separate paths");
    assert.equal(viewport.visibleVertexCount, 4);
    assert.ok(viewport.selectedDistanceMeters > 0);
    for (const path of viewport.paths) {
      assert.equal(path.vertices[0][0], ranges[path.rangeIndex].start);
      assert.equal(path.vertices.at(-1)[0], ranges[path.rangeIndex].end);
    }
    const offscreen = await action("/api/workspace/viewport", a, { ...viewportBody, bounds: [0, 0, 1, 1] });
    assert.equal(offscreen.status, 200);
    const offscreenBody = await offscreen.json();
    assert.deepEqual(offscreenBody.paths, []);
    assert.deepEqual(offscreenBody.selectedBounds, viewport.selectedBounds);
    assert.equal(offscreenBody.selectedDistanceMeters, viewport.selectedDistanceMeters, "selected distance is not viewport distance");
    assert.equal((await action("/api/workspace/viewport", a, { ...viewportBody, padding: "x".repeat(17000) })).status, 413);
    assert.equal((await action("/api/workspace/viewport", a, { ...viewportBody, bounds: [1, 0, 0, 1] })).status, 400);
    const queryBody = { ...ids, timeSelection: { timezone: "Asia/Shanghai", ranges },
      trajectoryDetail: { pixelToleranceMeters: 0, vertexBudget: 1000 }, entityKinds: ["point", "gap"] };
    const trajectory = await action("/api/workspace/query", a, queryBody);
    assert.equal(trajectory.status, 200);
    assert.equal((await trajectory.json()).trajectoryPaths.length, 2);
    assert.equal((await action("/api/workspace/events", a, { ...ids, ranges })).status, 200);
    assert.equal((await action("/api/workspace/viewport", a, { ...viewportBody, datasetId: state.files[0].dataset_id })).status, 404);
    assert.equal((await action("/api/workspace/viewport", b, viewportBody)).status, 404);
    assert.equal((await action("/api/workspace/events", b, { ...ids, ranges })).status, 404);
    assert.equal((await action("/api/workspace/query", b, queryBody)).status, 404);
    assert.equal((await bootstrap(b)).library.state, "empty");
    checkpoint("viewport, slices and isolation passed");
    response = await upload(a, firstCsv);
    assert.equal(response.status, 200);
    assert.equal((await response.json()).reused, true);
    const ownFile = state.files[0].id;
    const foreign = await library(b);
    assert.equal(foreign.files.length, 0);
    assert.equal(foreign.jobs.length, 0);
    assert.equal((await action(`/api/library/files/${ownFile}`, b, { action: "withdraw" })).status, 404);
    assert.equal((await action(`/api/library/jobs/${first}`, b, { action: "retry" })).status, 404);
    assert.equal((await action(`/api/library/files/${ownFile}`, a, null)).status, 400);
    assert.equal((await action(`/api/library/files/${ownFile}`, a, { action: "x", padding: "a".repeat(1024) })).status, 413);
    response = await upload(a, csv([[base + 10000, "118.7001"], [base + 20000, "118.7002"]]));
    assert.equal(response.status, 202);
    await work();
    state = await library(a);
    assert.equal(state.jobs[0].status, "succeeded", state.jobs[0].error_message);
    assert.equal(state.jobs[0].metrics.point_count, 3);
    assert.equal(state.jobs[0].metrics.duplicate_rows, 1);
    response = await upload(a, csv([[base, "118.70001"]]));
    assert.equal(response.status, 202);
    const conflictJob = (await response.json()).job.id;
    const previousMap = state.library.active_snapshot_id;
    await work();
    state = await library(a);
    assert.equal(state.jobs[0].status, "awaiting_resolution", state.jobs[0].error_message);
    assert.equal(state.library.active_snapshot_id, previousMap);
    const pendingMap = await bootstrap(a);
    assert.equal(pendingMap.dataset.pointCount, 3, "unresolved conflict cannot replace the published map");
    assert.equal((await action("/api/workspace/viewport", a, viewportBody)).status, 200, "owned superseded snapshots remain queryable during publication changes");
    assert.equal((await action(`/api/library/jobs/${conflictJob}`, a,
      { action: "resolve", resolutions: { [base]: "not-a-candidate" } })).status, 400);
    const candidate = state.jobs[0].metrics.conflicts[0].candidates[0].fingerprint;
    assert.equal((await action(`/api/library/jobs/${conflictJob}`, a,
      { action: "resolve", resolutions: { [base]: candidate } })).status, 202);
    await work();
    state = await library(a);
    assert.equal(state.jobs[0].status, "succeeded", state.jobs[0].error_message);
    assert.equal((await action(`/api/library/files/${ownFile}`, a, { action: "withdraw" })).status, 202);
    await work();
    state = await library(a);
    assert.equal(state.jobs[0].status, "succeeded", state.jobs[0].error_message);
    assert.equal(state.files.find(file => file.id === ownFile).status, "withdrawn");
    assert.equal(state.jobs[0].metrics.point_count, 3, "shared and conflicting-source points survive withdrawal");
    assert.equal((await action(`/api/library/files/${ownFile}`, a, { action: "restore" })).status, 202);
    await work();
    state = await library(a);
    assert.equal(state.jobs[0].status, "succeeded", state.jobs[0].error_message);
    assert.equal(state.files.find(file => file.id === ownFile).status, "active");
    response = await upload(a, "invalid,csv\n");
    assert.equal(response.status, 202);
    const failedJob = (await response.json()).job.id;
    const lastMap = state.library.active_snapshot_id;
    await work();
    state = await library(a);
    assert.equal(state.jobs[0].status, "failed");
    assert.equal(state.library.active_snapshot_id, lastMap);
    assert.equal((await action(`/api/library/jobs/${failedJob}`, a, { action: "retry" })).status, 202);
    await work();
    assert.equal((await library(a)).jobs[0].status, "failed");
    checkpoint("import, conflicts, withdrawal, restore and failure passed");
    } else {
      assert.equal((await upload(a, csv([[base, "118.7"], [base + 10000, "118.7001"], [base + 20000, "118.7002"]]))).status, 202);
      await work();
      checkpoint("short browser fixture published; full import lifecycle skipped");
    }
    // A real algorithm-derived fixture exercises nonempty stops, gaps, places
    // and modes, rather than merely validating SQL against two empty points.
    const richEntries = [];
    for (const offset of [0, 8000]) {
      for (let i = 0; i < 4; i++) richEntries.push([base + (offset + i * 30) * 1000, 118.7 + i * 0.0006, 2]);
      for (let i = 0; i <= 10; i++) richEntries.push([base + (offset + 120 + i * 60) * 1000, 118.7024 + (i % 3 - 1) * 0.00001, 0.1]);
      for (let i = 1; i <= 4; i++) richEntries.push([base + (offset + 720 + i * 30) * 1000, 118.7024 + i * 0.0006, 2]);
    }
    assert.equal((await upload(b, csv(richEntries), "algorithm-map-fixture.csv")).status, 202);
    await work(secondLibraryId);
    const richMap = await bootstrap(b);
    assert.equal(richMap.dataset.pointCount, richEntries.length);
    assert.ok(richMap.places.length > 0, "published place metadata loads without a full local preview");
    assert.ok(richMap.places.every(place => Number.isFinite(Date.parse(place.firstVisitedAt))));
    const richIds = { datasetId: richMap.dataset.id, processingRunId: richMap.processing.runId };
    const richRanges = [{ start: richMap.dataset.startedAt, end: richMap.dataset.endedAt }];
    const richEventsResponse = await action("/api/workspace/events", b, { ...richIds, ranges: richRanges });
    assert.equal(richEventsResponse.status, 200);
    const richEvents = await richEventsResponse.json();
    for (const kind of ["gaps", "stays", "trips", "modeLegs"]) assert.ok(richEvents[kind].length > 0, kind);
    const richViewportResponse = await action("/api/workspace/viewport", b,
      { ...richIds, ranges: richRanges, bounds: [118.69, 30.89, 118.72, 30.91], detailZoom: 15 });
    assert.equal(richViewportResponse.status, 200);
    const richViewport = await richViewportResponse.json();
    assert.ok(richViewport.paths.length > 1);
    assert.ok(richViewport.aggregatedRoutes.features.length > 0);
    for (const path of richViewport.paths) {
      assert.ok(path.vertices.every(vertex => Number.isFinite(Date.parse(vertex[0]))));
      for (const gap of richEvents.gaps) assert.ok(!(path.vertices[0][0] < gap.start && path.vertices.at(-1)[0] > gap.end),
        "a real observation gap cannot be bridged by viewport loading");
    }
    const gap = richEvents.gaps[0];
    const partialGap = [{ start: gap.start, end: new Date((Date.parse(gap.start) + Date.parse(gap.end)) / 2).toISOString() }];
    const partialEvents = await (await action("/api/workspace/events", b, { ...richIds, ranges: partialGap })).json();
    assert.equal(partialEvents.inferredConnections.length, 0, "partial slices do not draw a whole inferred connection");
    assert.equal((await action("/api/workspace/viewport", a,
      { ...richIds, ranges: richRanges, bounds: [118.69, 30.89, 118.72, 30.91], detailZoom: 15 })).status, 404,
      "reverse account isolation also holds after the second account publishes");
    checkpoint("nonempty algorithm events, places and geometry passed");
    await verifyViewsAfterPublication();
    checkpoint("cloud views preserved across dataset publication");
    if (process.env.ROUTEFROM_RUN_BROWSER_TESTS === "true") {
      const { verifyWorkspaceBrowser } = await import("./workspace-browser-qa.mjs");
      await verifyWorkspaceBrowser({ baseURL, accounts: browserAccounts, richEntries: richEntries.length });
      checkpoint("browser map and account switch passed");
    }
    await verifyCsvExports({ request, a, b, subjectA: empty.account.subject, subjectB: secondBootstrap.account.subject,
      map: richMap, richEntries, csv, library, pool, base, upload, work });
    checkpoint("CSV exports: immutable originals, slices, exact precision, multiple pages and isolation passed");
    await verifyPlaceNotesHttp({ request, a, b, map: richMap, subjectA: empty.account.subject,
      subjectB: secondBootstrap.account.subject, pool, work, upload, csv, base });
    checkpoint("place notes: exact rebinding, versions, legacy upgrade, withdrawal/restore and collisions passed");
    if (process.env.ROUTEFROM_BROWSER_QA === "true") {
      // Optional browser review uses only this test's disposable account/data.
      // Type `work` after a UI action, or `finish` to close and clean the fixture.
      assert.equal((await upload(a, csv([[base + 10000, "118.7003"]]),
        "灵敢足迹-重复时刻待选择-用于检查很长原始文件名在小屏幕上的显示.csv")).status, 202);
      await work();
      console.log("Disposable browser QA:", JSON.stringify({ url: `${baseURL}/login?next=/library`, ...browserAccounts[0] }));
      const input = createInterface({ input: process.stdin, output: process.stdout });
      try {
        for await (const line of input) {
          if (line.trim() === "finish") break;
          if (line.trim() === "work") { await work(); console.log("QA worker completed"); }
        }
      } finally { input.close(); }
    }
  } catch (error) {
    console.error(logs);
    console.error("HTTP integration assertion:", error);
    throw error;
  } finally {
    checkpoint("cleanup started");
    if (server && server.exitCode === null) {
      server.kill();
      await new Promise(resolve => server.once("exit", resolve));
    }
    // Only synthetic identities created by this test are targeted; preserve unrelated test data.
    await cleanupTestAccounts(pool, identities);
    await pool.end();
    const resolved = await realpath(root);
    const parent = await realpath(os.tmpdir());
    assert.equal(path.dirname(resolved).toLowerCase(), parent.toLowerCase());
    assert.match(path.basename(resolved), /^routefrom-http-test-/);
    await rm(resolved, { recursive: true, force: true });
    checkpoint("cleanup complete");
  }
});
