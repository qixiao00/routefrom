import "server-only";

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export function privateDataRoot() {
  // Runtime-mounted private storage must never be bundled into a web deployment.
  return path.resolve(/* turbopackIgnore: true */ process.env.FOOTPRINT_DATA_DIR || path.join(process.cwd(), "..", "data"));
}

export function privateFilePath(key: string) {
  if (!/^(raw|generated)\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.csv$/.test(key)) {
    throw new Error("invalid private asset key");
  }
  return path.join(/* turbopackIgnore: true */ privateDataRoot(), ...key.split("/"));
}

export async function storePrivateCsv(key: string, bytes: Uint8Array) {
  const target = privateFilePath(key);
  await mkdir(path.dirname(target), { recursive: true, mode: 0o700 });
  await writeFile(target, bytes, { flag: "wx", mode: 0o600 });
}
