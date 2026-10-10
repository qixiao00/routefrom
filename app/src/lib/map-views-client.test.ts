import test from "node:test";
import assert from "node:assert/strict";
import { createMapView, loadMapViews, removeMapView, saveActiveMapView } from "./map-views-client.ts";
import { serializeMapView } from "./map-view-config.ts";
import { DEFAULT_LAYERS, DEFAULT_MAP_VIEW, DEFAULT_PANELS } from "./workspace-session.ts";
import { WorkspaceBootstrapError } from "./workspace-client.ts";

test("cloud view requests carry the page's expected identity, bounded signals and private cache policy", async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return options.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({ revision: 8 });
  };
  const config = serializeMapView({ mapView: DEFAULT_MAP_VIEW, visibleLayers: DEFAULT_LAYERS, panels: DEFAULT_PANELS, ranges: [] });
  await loadMapViews("expected-account");
  await saveActiveMapView("expected-account", config, 7);
  await createMapView("expected-account", "通勤", config);
  await removeMapView("expected-account", "view-id");
  assert.deepEqual(calls.map(call => call.options.method), ["GET", "PUT", "POST", "DELETE"]);
  for (const { options } of calls) {
    assert.equal(options.cache, "no-store");
    assert.equal(new Headers(options.headers).get("x-routefrom-account"), "expected-account");
    assert.ok(options.signal instanceof AbortSignal);
  }
  assert.deepEqual(JSON.parse(String(calls[1].options.body)), { config, revision: 7 });
  assert.equal(calls[3].url, "/api/workspace/views/view-id");
});

test("account switches and conflicts remain actionable; cancellation is not converted into success", async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  for (const code of ["authentication_required", "account_changed", "view_conflict"]) {
    globalThis.fetch = async () => Response.json({ error: { code, message: "需要确认" } }, { status: 409 });
    await assert.rejects(() => loadMapViews("expected-account"), error => error instanceof WorkspaceBootstrapError && error.code === code);
  }
  globalThis.fetch = async () => { throw new DOMException("cancelled", "AbortError"); };
  await assert.rejects(() => loadMapViews("expected-account"), error => error instanceof DOMException && error.name === "AbortError");
});
