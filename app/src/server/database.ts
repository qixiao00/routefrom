import "server-only";

import { neon, type NeonQueryFunction } from "@neondatabase/serverless";
import { Pool, type PoolClient } from "pg";

export class DatabaseNotConfiguredError extends Error {}

let queryClient: NeonQueryFunction<false, false> | undefined;
let transactionPool: Pool | undefined;

export async function withDatabaseTransaction<T>(operation: (client: PoolClient) => Promise<T>) {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new DatabaseNotConfiguredError("DATABASE_URL is not configured");
  transactionPool ??= new Pool({ connectionString, max: 5, connectionTimeoutMillis: 10_000, idleTimeoutMillis: 30_000 });
  const client = await transactionPool.connect();
  try {
    await client.query("BEGIN");
    const result = await operation(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally { client.release(); }
}

export function getDatabase() {
  if (queryClient) return queryClient;
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new DatabaseNotConfiguredError("DATABASE_URL is not configured");
  }
  queryClient = neon(connectionString, { arrayMode: false, fullResults: false });
  return queryClient;
}
