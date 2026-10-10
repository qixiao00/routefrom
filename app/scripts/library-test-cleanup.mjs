import assert from "node:assert/strict";

export async function cleanupTestAccounts(pool, identities) {
  if (!identities.length) return;
  const database = await pool.query("SELECT current_database() AS name");
  assert.match(database.rows[0].name, /^routefrom_integration/);
  for (const email of identities) {
    assert.match(email, /^[0-9a-f-]{36}@example\.invalid$/);
    const users = await pool.query(`SELECT app_user.id FROM app.users AS app_user JOIN public.routefrom_auth_user AS identity
      ON identity.id = app_user.auth_subject WHERE identity.email = $1 AND identity.name = 'Library HTTP test'`, [email]);
    for (const { id } of users.rows) {
      await pool.query("BEGIN");
      try {
        await pool.query("UPDATE app.footprint_libraries SET active_snapshot_id = NULL WHERE user_id = $1", [id]);
        await pool.query("DELETE FROM app.library_snapshots WHERE user_id = $1", [id]);
        await pool.query("DELETE FROM app.library_jobs WHERE user_id = $1", [id]);
        await pool.query("DELETE FROM app.library_files WHERE user_id = $1", [id]);
        await pool.query("DELETE FROM app.library_place_notes WHERE user_id = $1", [id]);
        await pool.query("DELETE FROM app.library_processing_inputs WHERE user_id = $1", [id]);
        await pool.query("DELETE FROM app.library_observations WHERE library_id IN (SELECT id FROM app.footprint_libraries WHERE user_id = $1)", [id]);
        await pool.query("DELETE FROM app.footprint_libraries WHERE user_id = $1", [id]);
        await pool.query("UPDATE app.processing_runs SET supersedes_run_id = NULL WHERE dataset_id IN (SELECT id FROM app.datasets WHERE user_id = $1)", [id]);
        await pool.query("DELETE FROM app.processing_runs WHERE dataset_id IN (SELECT id FROM app.datasets WHERE user_id = $1)", [id]);
        await pool.query("DELETE FROM app.users WHERE id = $1", [id]);
        await pool.query("COMMIT");
      } catch (error) { await pool.query("ROLLBACK"); throw error; }
    }
    await pool.query("DELETE FROM public.routefrom_auth_user WHERE email = $1 AND name = 'Library HTTP test'", [email]);
  }
}
