import assert from "node:assert/strict";

export async function verifyPlaceNotesHttp({ request, a, b, map, subjectA, subjectB, pool, work, upload, csv, base }) {
  const get = async (cookie = b, subject = subjectB, runId) => {
    const response = await request(`/api/workspace/place-notes${runId ? `?runId=${runId}` : ""}`, cookie, { headers: { "x-routefrom-account": subject } });
    assert.equal(response.status, 200); assert.match(response.headers.get("cache-control"), /private.*no-store/);
    return response.json();
  };
  const put = (body, cookie = b, subject = subjectB, origin) => request("/api/workspace/place-notes", cookie, {
    method: "PUT", headers: { "x-routefrom-account": subject, "content-type": "application/json", ...(origin ? { origin } : {}) }, body: JSON.stringify(body),
  });
  const refresh = async () => (await request("/api/workspace/bootstrap", b)).json();
  const process = () => work(map.library.id);
  assert.equal((await request("/api/workspace/place-notes")).status, 401);
  assert.equal((await request("/api/workspace/place-notes", b)).status, 409);
  assert.equal((await request("/api/workspace/place-notes", b, { headers: { "x-routefrom-account": subjectA } })).status, 409);
  assert.deepEqual((await get(a, subjectA)).notes, []);
  assert.equal((await get()).lineageReady, true);
  const body = { id: null, revision: 0, runId: map.processing.runId, placeId: map.places[0].id, label: "合成地点 · 家" };
  assert.equal((await put(body, b, subjectB, "https://untrusted.example")).status, 403);
  assert.equal((await put({ ...body, label: "a".repeat(81) })).status, 400);
  assert.equal((await put({ ...body, label: "a".repeat(5000) })).status, 413);
  assert.equal((await request("/api/workspace/place-notes", b, { method: "PUT", headers: { "x-routefrom-account": subjectB, "content-type": "text/plain" }, body: "{}" })).status, 415);
  assert.equal((await put({ ...body, revision: -1 })).status, 400);
  assert.equal((await put(body)).status, 200);
  assert.equal((await put(body)).status, 409, "no duplicate note for the same evidence");
  let note = (await get()).notes[0];
  assert.equal(note.status, "bound"); assert.deepEqual(note.placeIds, [map.places[0].id]);
  const anchorCount = await pool.query("SELECT count(*) FROM app.library_place_note_anchors WHERE note_id = $1 AND library_id = $2", [note.id, map.library.id]);
  assert.ok(Number(anchorCount.rows[0].count) > 0 && Number(anchorCount.rows[0].count) <= 12);
  assert.equal((await put({ ...body, id: note.id, revision: note.revision, placeId: null }, a, subjectA)).status, 404);
  const concurrent = await Promise.all(["名字一", "名字二"].map(label => put({ ...body, id: note.id, revision: note.revision, placeId: null, label })));
  assert.deepEqual(concurrent.map(response => response.status).sort(), [200, 409]);
  note = (await get()).notes[0];
  const label = note.label;

  // New source content changes the generated dataset and place logical UUIDs.
  assert.equal((await upload(b, csv([[base - 10000, "118.7", 2]]), "older-observation.csv")).status, 202);
  await process();
  const recomputed = await refresh();
  assert.notEqual(recomputed.dataset.id, map.dataset.id);
  note = (await get()).notes[0];
  assert.equal(note.status, "bound"); assert.equal(note.label, label);
  assert.notEqual(note.placeIds[0], body.placeId);
  assert.equal((await put({ ...body, id: note.id, revision: note.revision })).status, 409, "stale map cannot silently rebind the note");

  // Model a pre-lineage published run without changing any original observations.
  await pool.query("DELETE FROM app.library_processing_inputs WHERE processing_run_id = $1 AND library_id = $2 AND user_id = $3", [recomputed.processing.runId, map.library.id, map.account.id]);
  assert.equal((await get()).lineageReady, false);
  assert.equal((await put({ ...body, id: note.id, revision: note.revision, runId: recomputed.processing.runId, placeId: note.placeIds[0] })).status, 409);
  const reprocess = () => request("/api/library/reprocess", b, { method: "POST", headers: { "x-routefrom-account": subjectB } });
  const queued = await (await reprocess()).json();
  assert.equal((await (await reprocess()).json()).job.id, queued.job.id);
  await process();
  assert.equal((await get()).lineageReady, true); assert.equal((await get()).notes[0].status, "bound");

  // Withdraw just the original place evidence, keeping the older isolated point.
  const files = (await (await request("/api/library", b)).json()).files;
  const richFile = files.find(file => file.name === "algorithm-map-fixture.csv");
  const membership = action => request(`/api/library/files/${richFile.id}`, b, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action }) });
  assert.equal((await membership("withdraw")).status, 202); await process();
  note = (await get()).notes[0]; assert.equal(note.status, "unmatched"); assert.equal(note.label, label);
  assert.equal((await put({ id: note.id, revision: note.revision, label: "来源撤销仍保留", placeId: null })).status, 200);
  assert.equal((await membership("restore")).status, 202); await process();
  note = (await get()).notes[0]; assert.equal(note.status, "bound"); assert.equal(note.label, "来源撤销仍保留");

  // Controlled collision fixture: two human records must survive a merged place.
  const copied = await pool.query(`INSERT INTO app.library_place_notes (library_id, user_id, label, position)
    SELECT library_id, user_id, '合并待确认', position FROM app.library_place_notes
    WHERE id = $1 AND library_id = $2 AND user_id = $3 RETURNING id`, [note.id, map.library.id, map.account.id]);
  const collisionId = copied.rows[0].id;
  await pool.query(`INSERT INTO app.library_place_note_anchors (note_id, library_id, source_id, observation_id)
    SELECT $1, library_id, source_id, observation_id FROM app.library_place_note_anchors WHERE note_id = $2 AND library_id = $3`, [collisionId, note.id, map.library.id]);
  assert.deepEqual((await get()).notes.map(item => item.status), ["collision", "collision"]);
  assert.equal((await put({ id: note.id, revision: note.revision, label: note.label, placeId: note.placeIds[0], runId: (await refresh()).processing.runId })).status, 409);
  assert.equal((await put({ id: collisionId, revision: 1, label: "", placeId: null })).status, 200);
  assert.equal((await get()).notes[0].status, "bound");
  assert.equal((await put({ id: note.id, revision: note.revision - 1, label: "", placeId: null })).status, 409);
  assert.equal((await put({ id: note.id, revision: note.revision, label: "", placeId: null })).status, 200);
  assert.deepEqual((await get()).notes, []);
  assert.deepEqual((await get(a, subjectA)).notes, []);
  const capped = await pool.query(`INSERT INTO app.library_place_notes (library_id, user_id, label, position)
    SELECT $1, $2, 'cap-fixture-' || sequence, ST_SetSRID(ST_MakePoint(118.7, 30.9), 4326)
    FROM generate_series(1,1000) AS sequence RETURNING id`, [map.library.id, map.account.id]);
  const currentMap = await refresh();
  const limit = await put({ ...body, placeId: currentMap.places[0].id, runId: currentMap.processing.runId });
  assert.equal(limit.status, 409); assert.equal((await limit.json()).error.code, "note_limit");
  await pool.query("DELETE FROM app.library_place_notes WHERE library_id = $1 AND user_id = $2 AND id = ANY($3::uuid[])", [map.library.id, map.account.id, capped.rows.map(row => row.id)]);
}
