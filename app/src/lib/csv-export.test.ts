import test from "node:test";
import assert from "node:assert/strict";
import { csvDownloadName, csvRow, parseCsvExportQuery, OBSERVATION_COLUMNS, OBSERVATION_FIELDS } from "./csv-export.ts";
import { csvExportUrl, prepareCsvDownload } from "./csv-export-client.ts";

const ids = { datasetId: "11111111-1111-4111-8111-111111111111", processingRunId: "22222222-2222-4222-8222-222222222222" };
const request = { ...ids, kind: "observations", timeSelection: { timezone: "Asia/Shanghai", ranges: [
  { start: "2026-10-02T00:00:00+08:00", end: "2026-10-03T00:00:00+08:00" },
  { start: "2026-10-01T00:00:00+08:00", end: "2026-10-02T01:00:00+08:00" },
  { start: "2026-10-05T00:00:00+08:00", end: "2026-10-05T00:00:01+08:00" },
] } };

test("CSV exports normalize arbitrary/discontinuous ranges without accepting LOD or inferred variants", () => {
  const parsed = parseCsvExportQuery(request);
  assert.equal(parsed.timeSelection.ranges.length, 2);
  assert.equal(parsed.timeSelection.ranges[0].start, "2026-09-30T16:00:00.000Z");
  for (const bad of [{ ...request, kind: "inferred" }, { ...request, kind: ["observations"] }, { ...request, kind: "map_matched" }, { ...request, processingRunId: "active" },
    { ...request, bounds: [0, 0, 1, 1] }, { ...request, trajectoryDetail: {} }, { ...request, timeSelection: { ...request.timeSelection, ranges: [] } }]) {
    assert.throws(() => parseCsvExportQuery(bad));
  }
  assert.equal(parseCsvExportQuery({ ...request, kind: "cleaned_gps" }).kind, "cleaned_gps");
  assert.equal(OBSERVATION_COLUMNS.length, 14); assert.equal(OBSERVATION_FIELDS.length, 14);
});

test("CSV preserves sentinels, precision and quoted external text rather than silently changing source fields", () => {
  assert.equal(csvRow(["1682006400000", "118.12345678901235", -1, null, '中文,\"WiFi\"\n', "=untrusted"]),
    '1682006400000,118.12345678901235,-1,,"中文,""WiFi""\n",=untrusted\r\n');
});

test("download names use UTF-8 and cannot inject response headers or file paths", () => {
  const name = csvDownloadName('足迹/"\r\nInjected: evil.csv');
  assert.ok(name.includes("filename*=UTF-8''")); assert.ok(!/[\r\n]/.test(name));
  assert.ok(name.includes("%E8%B6%B3"));
  assert.doesNotThrow(() => csvDownloadName("a" + "🌍".repeat(200)));
});

test("native download preparation carries the page account and reports a stale login without reading a CSV blob", async () => {
  const previous = globalThis.fetch;
  let input = "";
  globalThis.fetch = async (url, init) => {
    input = String(url); assert.equal(init?.method, "HEAD"); assert.equal(init?.cache, "no-store");
    return new Response(null, { status: 409, headers: { "x-routefrom-export-error": "account_changed" } });
  };
  try {
    await assert.rejects(prepareCsvDownload("old-account", { fileId: ids.datasetId }), /账号已切换/);
    assert.equal(new URL(input, "http://localhost").searchParams.get("account"), "old-account");
    const parsed = parseCsvExportQuery(request);
    assert.deepEqual(JSON.parse(new URL(csvExportUrl("owner", parsed), "http://localhost").searchParams.get("query")!), parsed);
  } finally { globalThis.fetch = previous; }
});
