import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { betterAuth } from "better-auth";
import { getMigrations } from "better-auth/db/migration";
import { createAuthOptions } from "../src/server/auth-config.ts";

test("real Postgres authentication: schema, signup, session, password rejection and logout", {
  skip: process.env.ROUTEFROM_RUN_DATABASE_TESTS !== "true",
}, async () => {
  const url = new URL(process.env.DATABASE_URL_DIRECT);
  const database = process.env.ROUTEFROM_TEST_DATABASE || "routefrom_integration";
  assert.match(database, /^routefrom_integration/);
  url.pathname = `/${database}`;
  const baseURL = "http://127.0.0.1:3000";
  const { options, pool } = createAuthOptions({
    databaseUrl: url.toString(),
    secret: process.env.BETTER_AUTH_SECRET,
    baseURL,
  });
  const email = `${randomUUID()}@example.invalid`;
  const password = randomUUID();
  const auth = betterAuth(options);
  const request = (path, body, cookie) => new Request(`${baseURL}/api/auth/${path}`, {
    method: body ? "POST" : "GET",
    headers: {
      origin: baseURL,
      "content-type": "application/json",
      "x-forwarded-for": "192.0.2.21",
      ...(cookie ? { cookie } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  try {
    const migrations = await getMigrations(options);
    assert.equal(migrations.toBeCreated.length, 0);
    assert.equal(migrations.toBeAdded.length, 0);
    const signup = await auth.handler(request("sign-up/email", {
      email, password, name: "Integration test",
    }));
    assert.equal(signup.status, 200);
    const identity = await signup.json();
    assert.ok(identity.user.id);
    const cookie = signup.headers.getSetCookie().map(value => value.split(";", 1)[0]).join("; ");
    assert.ok(cookie);
    const session = await auth.handler(request("get-session", null, cookie));
    assert.equal((await session.json()).user.id, identity.user.id);
    const rejected = await auth.handler(request("sign-in/email", {
      email, password: "wrong-password",
    }));
    assert.equal(rejected.status, 401);
    const logout = await auth.handler(request("sign-out", {}, cookie));
    assert.equal(logout.status, 200);
    const ended = await auth.handler(request("get-session", null, cookie));
    assert.equal(await ended.json(), null);
  } finally {
    await pool.query("DELETE FROM public.routefrom_auth_user WHERE email = $1", [email]);
    await pool.end();
  }
});
