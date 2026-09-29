import { copyFile, mkdir, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const packageRoot = path.dirname(require.resolve("maplibre-gl/package.json"));
const { version } = JSON.parse(await readFile(path.join(packageRoot, "package.json"), "utf8"));
const appRoot = fileURLToPath(new URL("../", import.meta.url));
const destination = path.join(appRoot, "public", "maplibre", version);
await mkdir(destination, { recursive: true });

// The v6 worker imports its shared module by a relative URL. Preserve both
// filenames instead of letting Turbopack emit independent hashed assets.
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  await copyFile(path.join(packageRoot, "dist", file), path.join(destination, file));
}
await copyFile(path.join(packageRoot, "LICENSE.txt"), path.join(destination, "LICENSE.txt"));
console.log(`Prepared MapLibre ${version} worker and shared module.`);
