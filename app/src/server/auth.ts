import "server-only";

import { betterAuth } from "better-auth";
import { createAuthOptions } from "./auth-config";
import { getDatabase } from "./database";

let instance: ReturnType<typeof buildAuth> | undefined;

export class AuthenticationRequiredError extends Error {}

function buildAuth() {
  const databaseUrl = process.env.DATABASE_URL;
  const secret = process.env.BETTER_AUTH_SECRET;
  const baseURL = process.env.BETTER_AUTH_URL;
  if (!databaseUrl || !secret || !baseURL) throw new Error("website authentication is not configured");
  return betterAuth(createAuthOptions({ databaseUrl, secret, baseURL }).options);
}

export function getAuth() {
  return instance ??= buildAuth();
}

export async function requireAuthenticatedUser(request: Request) {
  const session = await getAuth().api.getSession({ headers: request.headers });
  if (!session) throw new AuthenticationRequiredError("请先登录。");
  const sql = getDatabase();
  const rows = await sql`
    INSERT INTO app.users (auth_subject, display_name)
    VALUES (${session.user.id}, ${session.user.name})
    ON CONFLICT (auth_subject) DO UPDATE
      SET display_name = EXCLUDED.display_name, updated_at = now()
    RETURNING id, display_name, timezone
  ` as { id: string; display_name: string; timezone: string }[];
  return {
    id: String(rows[0].id),
    name: String(rows[0].display_name),
    timezone: String(rows[0].timezone),
  };
}
