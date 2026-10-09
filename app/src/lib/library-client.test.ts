import assert from "node:assert/strict";
import test from "node:test";
import { changeLibraryFile, fileCoverage, jobStageLabel, jobStatusLabel, LibraryApiError, loadLibrary,
  loginReturnPath, updateLibraryJob, uploadLibraryFile, validConflictDecisions, type LibraryFile } from "./library-client.ts";

test("library reads are private and authentication errors remain actionable", async () => {
  const original = globalThis.fetch;
  const calls: Array<RequestInit | undefined> = [];
  globalThis.fetch = async (_input, init) => { calls.push(init); return Response.json({ error: { code: "authentication_required", message: "请先登录。" } }, { status: 401 }); };
  try {
    await assert.rejects(loadLibrary(), (error: unknown) => error instanceof LibraryApiError && error.status === 401 && error.code === "authentication_required");
    assert.equal(calls[0]?.cache, "no-store");
  } finally { globalThis.fetch = original; }
});

test("CSV uploads use multipart without a fabricated content-type; duplicate feedback is retained", async () => {
  const original = globalThis.fetch;
  let request: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => { request = init; return Response.json({ reused: true, fileId: "file" }); };
  try {
    const result = await uploadLibraryFile(new File(["csv"], "footprint.CSV"));
    assert.equal(result.reused, true);
    assert.equal(request?.method, "POST");
    assert.equal(request?.headers, undefined);
    assert.ok(request?.body instanceof FormData);
    assert.equal((request.body.get("file") as File).name, "footprint.CSV");
    await assert.rejects(uploadLibraryFile(new File([], "empty.csv")), /非空/);
    await assert.rejects(uploadLibraryFile(new File(["x"], "file.json")), /CSV/);
    await assert.rejects(uploadLibraryFile({ name: "huge.csv", size: 80 * 1024 * 1024 + 1 } as File), /80 MB/);
  } finally { globalThis.fetch = original; }
});

test("membership and retry/decision actions preserve the API contract and error messages", async () => {
  const original = globalThis.fetch;
  const requests: Array<{ url: string; body: unknown }> = [];
  globalThis.fetch = async (input, init) => { requests.push({ url: String(input), body: JSON.parse(String(init?.body)) }); return Response.json({ job: { id: "job" } }, { status: 202 }); };
  try {
    await changeLibraryFile("file", "withdraw");
    await changeLibraryFile("file", "restore");
    await updateLibraryJob("job", "retry");
    await updateLibraryJob("job", "resolve", { "1": "choice" });
    assert.deepEqual(requests.map(request => request.body), [{ action: "withdraw" }, { action: "restore" }, { action: "retry" }, { action: "resolve", resolutions: { "1": "choice" } }]);
    assert.equal(requests[0].url, "/api/library/files/file");
    globalThis.fetch = async () => Response.json({ error: { code: "invalid_decisions", message: "候选已变化" } }, { status: 400 });
    await assert.rejects(updateLibraryJob("job", "resolve", {}), /候选已变化/);
  } finally { globalThis.fetch = original; }
});

test("polling preserves valid conflict choices but never re-submits resolved or stale candidates", () => {
  const conflicts = [{ epochMs: 1, candidates: [{ fingerprint: "a", sourceId: "s", longitude: 1, latitude: 1, accuracyMeters: null }] },
    { epochMs: 2, candidates: [{ fingerprint: "b", sourceId: "s", longitude: 1, latitude: 1, accuracyMeters: 5 }] }];
  assert.deepEqual(validConflictDecisions(conflicts, { "1": "a", "2": "wrong", "3": "gone" }), { "1": "a" });
  assert.deepEqual(validConflictDecisions(conflicts.slice(1), { "1": "a" }), {});
});

test("file coverage compares actual arbitrary ranges and retains withdrawn history", () => {
  const file = (id: string, start: string, end: string, status: LibraryFile["status"] = "active") => ({ id, recorded_from: start, recorded_to: end, status } as LibraryFile);
  const a = file("a", "2026-01-01", "2026-01-11");
  const b = file("b", "2026-01-06", "2026-01-11", "withdrawn");
  assert.deepEqual(fileCoverage([a, b], a), { left: 0, width: 100 });
  assert.deepEqual(fileCoverage([a, b], b), { left: 50, width: 50 });
  assert.equal(fileCoverage([a], { ...a, recorded_from: null }), null);
  const single = file("one", "2026-01-01", "2026-01-01");
  assert.deepEqual(fileCoverage([single], single), { left: 0, width: 100 });
});

test("labels reflect durable states; login return links cannot become external redirects", () => {
  assert.equal(jobStatusLabel("awaiting_resolution"), "需要选择");
  assert.equal(jobStageLabel("publish"), "发布新版本");
  assert.equal(jobStageLabel("future_stage"), "处理足迹");
  assert.equal(loginReturnPath("/library"), "/library");
  for (const destination of [null, "https://evil.example", "//evil.example", "/library?redirect=evil", "/\\evil"]) assert.equal(loginReturnPath(destination), "/");
});
