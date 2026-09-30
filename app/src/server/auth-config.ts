import type { BetterAuthOptions } from "better-auth";
import { Pool } from "pg";

export function createAuthOptions(input: {
  databaseUrl: string;
  secret: string;
  baseURL: string;
}) {
  if (input.secret.length < 32) throw new Error("BETTER_AUTH_SECRET must have at least 32 characters");
  const pool = new Pool({
    connectionString: input.databaseUrl,
    max: 5,
    connectionTimeoutMillis: 10_000,
    idleTimeoutMillis: 30_000,
  });
  const options = {
    appName: "RouteFrom",
    database: pool,
    secret: input.secret,
    baseURL: input.baseURL,
    trustedOrigins: [input.baseURL],
    emailAndPassword: { enabled: true, minPasswordLength: 10, maxPasswordLength: 128 },
    user: { modelName: "routefrom_auth_user" },
    account: { modelName: "routefrom_auth_account" },
    session: { modelName: "routefrom_auth_session", expiresIn: 60 * 60 * 24 * 7 },
    verification: { modelName: "routefrom_auth_verification" },
    rateLimit: { enabled: true, storage: "database", modelName: "routefrom_auth_rate_limit" },
  } satisfies BetterAuthOptions;
  return { options, pool };
}
