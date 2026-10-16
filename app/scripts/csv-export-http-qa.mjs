import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";

function parseCsv(text) {
  // Use the standard CSV parser to check quotes/newlines, not split(',').
  const result = spawnSync(path.resolve("../.venv/Scripts/python.exe"), ["-X", "utf8", "-c", "import csv,io,json,sys; print(json.dumps(list(csv.reader(io.StringIO(sys.stdin.read()))),ensure_ascii=True))"],
    { input: text, encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr); return JSON.parse(result.stdout);
}

// Fail before database fixtures if a Windows shell/code page changes test IO.
export function verifyCsvParser() { assert.deepEqual(parseCsv('"中文,""WiFi"""\r\n'), [['中文,"WiFi"']]); }

export async function verifyCsvExports({ request, a, b, subjectA, subjectB, map, richEntries, csv, library, pool, base, upload, work }) {
  const url = (subject, query) => "/api/library/export?" + new URLSearchParams({ account: subject, query: JSON.stringify(query) });
  const ids = { datasetId: map.dataset.id, processingRunId: map.processing.runId };
  const range = (start, end) => ({ start: new Date(start).toISOString(), end: new Date(end).toISOString() });
  const full = { ...ids, kind: "observations", timeSelection: { timezone: "Asia/Shanghai", ranges: [range(base, base + 9000000)] } };
  const download = async (query, cookie = b, subject = subjectB) => {
    const response = await request(url(subject, query), cookie);
    assert.equal(response.status, 200); assert.match(response.headers.get("cache-control"), /private.*no-store/);
    assert.match(response.headers.get("content-type"), /text\/csv/); assert.equal(response.headers.get("x-content-type-options"), "nosniff");
    return parseCsv(await response.text());
  };
  const bFile = (await library(b)).files.find(file => file.name === "algorithm-map-fixture.csv");
  const fileUrl = "/api/library/export?" + new URLSearchParams({ account: subjectB, file: bFile.id });
  assert.equal((await request(fileUrl)).status, 401);
  assert.equal((await request(fileUrl, a)).status, 409);
  assert.equal((await request("/api/library/export?" + new URLSearchParams({ account: subjectA, file: bFile.id }), a)).status, 404);
  assert.equal((await request(url(subjectA, full), a)).status, 404);
  assert.equal((await request(url(subjectB, full), a)).status, 409);
  const original = await request(fileUrl, b);
  const originalBytes = Buffer.from(await original.arrayBuffer());
  assert.deepEqual(originalBytes, Buffer.from(csv(richEntries)));
  assert.equal(original.headers.get("content-length"), String(originalBytes.length));
  assert.equal(original.headers.get("x-routefrom-source-sha256"), createHash("sha256").update(originalBytes).digest("hex"));
  const head = await request(fileUrl, b, { method: "HEAD" });
  assert.equal(head.status, 200); assert.equal((await head.arrayBuffer()).byteLength, 0);
  const all = await download(full);
  assert.equal(all.length, richEntries.length + 1);
  assert.deepEqual(all[0], csv(richEntries).split("\n")[0].split(","));
  assert.deepEqual(all.slice(1).map(row => Number(row[0])), richEntries.map(row => row[0]));
  const slices = [range(base, base + 60000), range(base + 30000, base + 90000), range(base + 8000000, base + 8030000)];
  const subset = await download({ ...full, timeSelection: { ...full.timeSelection, ranges: slices } });
  const times = richEntries.map(row => row[0]).filter(time => slices.some(slice => time >= Date.parse(slice.start) && time < Date.parse(slice.end)));
  assert.deepEqual(subset.slice(1).map(row => Number(row[0])), times);
  const baseline = await download({ ...full, kind: "cleaned_gps" });
  const [quality] = (await pool.query(`SELECT assessment.point_id, assessment.quality_status, assessment.exclusion_supported
    FROM app.point_assessments assessment JOIN app.trajectory_variants variant ON variant.processing_run_id=assessment.processing_run_id AND variant.variant_kind='cleaned_gps'
    JOIN app.trajectory_vertices vertex ON vertex.trajectory_variant_id=variant.id AND vertex.source_point_id=assessment.point_id
    WHERE assessment.processing_run_id=$1 AND assessment.quality_status <> 'excluded' ORDER BY vertex.sequence_number LIMIT 1`, [map.processing.runId])).rows;
  assert.ok(quality);
  try {
    // Controlled evidence fixture, confined to the disposable account/test DB.
    await pool.query("UPDATE app.point_assessments SET quality_status='excluded', exclusion_supported=true WHERE processing_run_id=$1 AND point_id=$2", [map.processing.runId, quality.point_id]);
    assert.equal((await download({ ...full, kind: "cleaned_gps" })).length, baseline.length - 1);
    assert.equal((await download(full)).length, all.length, "source export never silently drops an assessed anomaly");
  } finally {
    await pool.query("UPDATE app.point_assessments SET quality_status=$3, exclusion_supported=$4 WHERE processing_run_id=$1 AND point_id=$2", [map.processing.runId, quality.point_id, quality.quality_status, quality.exclusion_supported]);
  }
  for (const kind of ["cleaned_gps", "smoothed_gps"]) {
    const processed = await download({ ...full, kind, timeSelection: { ...full.timeSelection, ranges: slices } });
    assert.ok(processed.length > 1); const col = name => processed[0].indexOf(name);
    const expected = await pool.query(`SELECT vertex.recorded_at, ST_X(vertex.position)::text AS longitude, ST_Y(vertex.position)::text AS latitude,
        segment.sequence_number, vertex.segment_sequence_number, mapping.observation_id, assessment.quality_status
      FROM app.trajectory_variants variant JOIN app.trajectory_vertices vertex ON vertex.trajectory_variant_id=variant.id
      JOIN app.trajectory_segments segment ON segment.id=vertex.trajectory_segment_id
      JOIN app.library_run_observations mapping ON mapping.processing_run_id=variant.processing_run_id AND mapping.point_id=vertex.source_point_id
      JOIN app.point_assessments assessment ON assessment.processing_run_id=variant.processing_run_id AND assessment.point_id=vertex.source_point_id
      WHERE variant.processing_run_id=$1 AND variant.variant_kind=$2 AND NOT vertex.is_interpolated AND assessment.quality_status<>'excluded'
      ORDER BY vertex.sequence_number`, [map.processing.runId, kind]);
    const filtered = expected.rows.filter(row => slices.some(slice => row.recorded_at >= new Date(slice.start) && row.recorded_at < new Date(slice.end)));
    assert.equal(processed.length - 1, filtered.length);
    for (let i = 0; i < filtered.length; i++) {
      const row = processed[i + 1], actual = filtered[i];
      assert.equal(row[col("recordedAt")], actual.recorded_at.toISOString());
      assert.equal(row[col("longitudeWgs84")], actual.longitude); assert.equal(row[col("latitudeWgs84")], actual.latitude);
      assert.equal(row[col("sourceObservationId")], actual.observation_id);
      assert.equal(row[col("segmentIndex")], String(actual.sequence_number));
      assert.equal(row[col("qualityStatus")], actual.quality_status); assert.equal(row[col("variant")], kind);
      assert.equal(row[col("processingRunId")], map.processing.runId);
    }
    assert.ok(new Set(processed.slice(1).map(row => row[col("selectionRangeIndex")])).size > 1);
    const betweenPoints = await download({ ...full, kind, timeSelection: { ...full.timeSelection, ranges: [range(base + 1, base + 20000)] } });
    assert.equal(betweenPoints.length, 1, "CSV must not export map boundary interpolation or inferred connections");
  }
  for (const invalid of [{ ...full, processingRunId: "active" }, { ...full, kind: "map_matched" }, { ...full, bounds: [0, 0, 1, 1] },
    { ...full, timeSelection: { ...full.timeSelection, ranges: [] } }]) assert.equal((await request(url(subjectB, invalid), b)).status, 400);
  const missing = await request(url(subjectB, { ...full, processingRunId: "00000000-0000-4000-8000-000000000000" }), b, { method: "HEAD" });
  assert.equal(missing.status, 404); assert.equal(missing.headers.get("x-routefrom-export-error"), "not_found");

  // More than one page, full precision and embedded CSV text, through the real
  // upload/worker path. Fixture is owned by A; B's browser fixture stays intact.
  const largeBase = base + 20 * 86400000;
  const points = Array.from({ length: 1011 }, (_, index) => [largeBase + index * 30000, "118.12345678901235", 0]);
  const text = csv(points).replaceAll(",0,,1\n", ',0,"中文,\"\"WiFi\"\"",1\n');
  const accepted = await upload(a, text, "合成导出精度.csv"); assert.equal(accepted.status, 202);
  await work();
  const response = await request("/api/workspace/bootstrap", a); const largeMap = await response.json();
  const query = { ...full, datasetId: largeMap.dataset.id, processingRunId: largeMap.processing.runId,
    timeSelection: { ...full.timeSelection, ranges: [range(largeBase, largeBase + 1011 * 30000)] } };
  const paged = await download(query, a, subjectA);
  assert.equal(paged.length, 1012); assert.equal(new Set(paged.slice(1).map(row => row[0])).size, 1011);
  for (const row of paged.slice(1)) {
    assert.equal(row[2], "118.12345678901235", "source coordinate keeps every significant digit");
    assert.equal(row[12], '中文,"WiFi"', "CSV quoting must preserve external text");
    // Parsed source sensors are floats: -1 and -1.0 represent the same sentinel.
    // Only the original-file download promises byte-for-byte formatting.
    assert.equal(Number(row[6]), -1, "missing sensor sentinel is never changed to zero or blank");
  }
  const sourceFile = (await library(a)).files.find(file => file.name === "合成导出精度.csv");
  const sourceUrl = "/api/library/export?" + new URLSearchParams({ account: subjectA, file: sourceFile.id });
  assert.match((await request(sourceUrl, a, { method: "HEAD" })).headers.get("content-disposition"), /%E5%90%88/);
  assert.equal((await request(`/api/library/files/${sourceFile.id}`, a, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "withdraw" }) })).status, 202);
  assert.equal(await (await request(sourceUrl, a)).text(), text, "withdrawal never removes the original download");
  const immutable = await download(query, a, subjectA);
  assert.deepEqual(immutable, paged, "pending withdrawal cannot silently change the viewed version's export");
  const cancelled = await request(url(subjectA, query), a);
  const reader = cancelled.body.getReader(); await reader.read(); await reader.cancel();
  assert.equal((await download(query, a, subjectA)).length, paged.length, "cancelled download does not poison later requests");
  // A malformed stored path cannot escape private storage or expose another file.
  const [asset] = (await pool.query("SELECT source_object_key FROM app.datasets WHERE id=$1", [sourceFile.dataset_id])).rows;
  try {
    await pool.query("UPDATE app.datasets SET source_object_key='../outside.csv' WHERE id=$1", [sourceFile.dataset_id]);
    assert.equal((await request(sourceUrl, a)).status, 503);
  } finally { await pool.query("UPDATE app.datasets SET source_object_key=$2 WHERE id=$1", [sourceFile.dataset_id, asset.source_object_key]); }
  const sixtyFour = { ...query, timeSelection: { ...query.timeSelection, ranges: Array.from({ length: 64 }, (_, i) => range(largeBase + i * 60000, largeBase + i * 60000 + 10000)) } };
  assert.equal((await download(sixtyFour, a, subjectA)).length, 65, "all 64 arbitrary disjoint intervals can be downloaded");
}
