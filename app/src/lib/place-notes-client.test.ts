import assert from "node:assert/strict";
import test from "node:test";
import { loadPlaceNotes, writePlaceNote } from "./place-notes-client.ts";
import { WorkspaceBootstrapError } from "./workspace-client.ts";

test("place notes carry expected identity and version with private requests", async t => {
  const previous = globalThis.fetch; t.after(() => { globalThis.fetch = previous; });
  const calls: { url: string; options: RequestInit }[] = [];
  globalThis.fetch = async (url, options = {}) => { calls.push({ url: String(url), options }); return Response.json({}); };
  await loadPlaceNotes("owner", "run-id");
  const body = { id: "note-id", revision: 4, label: "家", placeId: "place-id", runId: "run-id" };
  await writePlaceNote("owner", body);
  assert.equal(calls[0].url, "/api/workspace/place-notes?runId=run-id");
  assert.deepEqual(JSON.parse(String(calls[1].options.body)), body);
  for (const call of calls) { assert.equal(call.options.cache, "no-store"); assert.equal(new Headers(call.options.headers).get("x-routefrom-account"), "owner"); assert.ok(call.options.signal instanceof AbortSignal); }
});
test("note conflicts, changed identities and ambiguous writes never become successful saves", async t => {
  const previous = globalThis.fetch; t.after(() => { globalThis.fetch = previous; });
  for (const code of ["note_conflict", "account_changed", "snapshot_changed", "place_note_collision"]) {
    globalThis.fetch = async () => Response.json({ error: { code, message: "请确认" } }, { status: 409 });
    await assert.rejects(() => loadPlaceNotes("owner", null), error => error instanceof WorkspaceBootstrapError && error.code === code);
  }
  globalThis.fetch = async () => { throw new DOMException("aborted", "AbortError"); };
  await assert.rejects(() => loadPlaceNotes("owner", null), error => error instanceof DOMException && error.name === "AbortError");
});
