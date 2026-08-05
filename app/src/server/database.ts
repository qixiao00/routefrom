import "server-only";

import { neon } from "@neondatabase/serverless";

export class DatabaseNotConfiguredError extends Error {}

let queryClient: ReturnType<typeof neon> | undefined;

export function getDatabase() {
  if (queryClient) return queryClient;
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new DatabaseNotConfiguredError("DATABASE_URL is not configured");
  }
  queryClient = neon(connectionString);
  return queryClient;
}
