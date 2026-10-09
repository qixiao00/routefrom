import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, realpath, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { promisify } from "node:util";
import { createInterface } from "node:readline";
import test from "node:test";
import { Pool } from "pg";

const execute = promisify(execFile);
const header = "geoTime,latitude,longitude,wgsLatitude,wgsLongitude,altitude,course,horizontalAccuracy,verticalAccuracy,speed,dayTimeMills,networkType,networkName,locationType\n";
const csv = (entries) => header + entries.map(([time, longitude]) => `${time},30.9,${longitude},30.9,${longitude},50,-1,5,-1,-1,1682006400000,0,,1\n`).join("");

test("website library: private multipart imports, dedup, isolation, conflicts, withdrawal and restore", {
  skip: process.env.ROUTEFROM_RUN_DATABASE_TESTS !== "true",
  timeout: process.env.ROUTEFROM_BROWSER_QA === "true" ? 900_000 : 300_000,
}, async () => {
  const database = process.env.ROUTEFROM_TEST_DATABASE || "routefrom_integration";
  assert.match(database, /^routefrom_integration/);
  const url = new URL(process.env.DATABASE_URL_DIRECT);
  url.pathname = `/${database}`;
  const webUrl = new URL(process.env.DATABASE_URL);
  webUrl.pathname = `/${database}`;
  const pool = new Pool({ connectionString: webUrl.toString(), max: 1, connectionTimeoutMillis: 10_000 });
  const root = await mkdtemp(path.join(os.tmpdir(), "routefrom-http-test-"));
  const baseURL = "http://127.0.0.1:3100";
  const env = { ...process.env, DATABASE_URL: webUrl.toString(), DATABASE_URL_DIRECT: url.toString(),
    BETTER_AUTH_URL: baseURL, FOOTPRINT_DATA_DIR: root };
  const identities = [];
  const browserAccounts = [];
  let server;
  // Capture output only for failure diagnostics; never print environment variables or URLs containing credentials.
  let logs = "";
  const request = (endpoint, cookie, options = {}) => fetch(baseURL + endpoint, {
    ...options, headers: { origin: baseURL, ...(cookie ? { cookie } : {}), ...options.headers },
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
  const upload = async (cookie, contents, name = "test.csv") => {
    const form = new FormData();
    form.append("file", new File([contents], name, { type: "text/csv" }));
    return request("/api/library/imports", cookie, { method: "POST", body: form });
  };
  const work = async () => {
    const python = path.resolve("../.venv/Scripts/python.exe");
    await execute(python, ["-m", "routefrom_pipeline.library_worker", "--once", "--data-dir", root],
      { cwd: path.resolve("../processor"), env, windowsHide: true, timeout: 90_000 });
  };
  try {
    const version = await pool.query("SELECT max(filename) AS filename FROM routefrom_meta.schema_migrations");
    assert.equal(version.rows[0].filename, "0015_library_jobs_and_snapshots.sql", "apply current migrations to the test database first");
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
    assert.equal((await request("/api/library/imports", a, { method: "POST", headers: { origin: "https://untrusted.example" } })).status, 403);
    const base = 1682028286912;
    const firstCsv = csv([[base, "118.7"], [base + 10000, "118.7001"]]);
    let response = await upload(a, firstCsv);
    assert.equal(response.status, 202);
    const first = (await response.json()).job.id;
    response = await upload(a, firstCsv);
    assert.equal(response.status, 202);
    assert.equal((await response.json()).job.id, first, "pending duplicate reuses its job");
    await work();
    let state = await library(a);
    assert.equal(state.jobs[0].status, "succeeded", state.jobs[0].error_message);
    assert.equal(state.jobs[0].metrics.point_count, 2);
    assert.equal(state.files.length, 1);
    assert.equal(state.user.name, "Library HTTP test");
    assert.equal(Number(state.published.point_count), 2);
    assert.equal(state.files[0].dataset_id, state.jobs[0].dataset_id);
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
    if (server && server.exitCode === null) {
      server.kill();
      await new Promise(resolve => server.once("exit", resolve));
    }
    // Only synthetic identities created by this test are targeted; preserve unrelated test data.
    for (const email of identities) {
      const users = await pool.query(`SELECT app_user.id FROM app.users AS app_user JOIN public.routefrom_auth_user AS identity
        ON identity.id = app_user.auth_subject WHERE identity.email = $1`, [email]);
      for (const { id } of users.rows) {
        await pool.query("BEGIN");
        try {
          await pool.query("UPDATE app.footprint_libraries SET active_snapshot_id = NULL WHERE user_id = $1", [id]);
          await pool.query("DELETE FROM app.library_snapshots WHERE user_id = $1", [id]);
          await pool.query("DELETE FROM app.library_jobs WHERE user_id = $1", [id]);
          await pool.query("DELETE FROM app.library_files WHERE user_id = $1", [id]);
          await pool.query("DELETE FROM app.library_observations WHERE library_id IN (SELECT id FROM app.footprint_libraries WHERE user_id = $1)", [id]);
          await pool.query("DELETE FROM app.footprint_libraries WHERE user_id = $1", [id]);
          await pool.query("UPDATE app.processing_runs SET supersedes_run_id = NULL WHERE dataset_id IN (SELECT id FROM app.datasets WHERE user_id = $1)", [id]);
          await pool.query("DELETE FROM app.processing_runs WHERE dataset_id IN (SELECT id FROM app.datasets WHERE user_id = $1)", [id]);
          await pool.query("DELETE FROM app.users WHERE id = $1", [id]);
          await pool.query("COMMIT");
        } catch (error) { await pool.query("ROLLBACK"); throw error; }
      }
      await pool.query("DELETE FROM public.routefrom_auth_user WHERE email = $1", [email]);
    }
    await pool.end();
    const resolved = await realpath(root);
    const parent = await realpath(os.tmpdir());
    assert.equal(path.dirname(resolved).toLowerCase(), parent.toLowerCase());
    assert.match(path.basename(resolved), /^routefrom-http-test-/);
    await rm(resolved, { recursive: true, force: true });
  }
});
