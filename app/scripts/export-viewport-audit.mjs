import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { buildViewportSelection, queryViewportSelection } from "../src/lib/workspace-viewport.ts";

const previewPath = path.resolve("..", "data", "generated", "workspace-preview.json");
const outputArgument = process.argv.indexOf("--output");
const outputPath = outputArgument >= 0
  ? path.resolve(process.argv[outputArgument + 1])
  : path.resolve("..", "data", "generated", "view-z9-audit.json");
const preview = JSON.parse(await readFile(previewPath, "utf8"));
const last = preview.paths.at(-1)?.vertices.at(-1);
if (!last) throw new Error("preview has no trajectory vertices");

const selection = buildViewportSelection(preview.paths, [{
  start: preview.dataset.startedAt,
  end: preview.dataset.endedAt,
}], preview.modeLegs);
const boundsArgument = process.argv.indexOf("--bounds");
const bounds = boundsArgument >= 0
  ? process.argv[boundsArgument + 1].split(",").map(Number)
  : [last[1] - 1, last[2] - 0.65, last[1] + 1, last[2] + 0.65];
if (bounds.length !== 4 || bounds.some((value) => !Number.isFinite(value))) {
  throw new Error("--bounds must contain west,south,east,north");
}
const zoomArgument = process.argv.indexOf("--zoom");
const zoom = zoomArgument >= 0 ? Number(process.argv[zoomArgument + 1]) : 9;
if (!Number.isFinite(zoom) || zoom < 0 || zoom > 22) throw new Error("invalid --zoom");
const response = queryViewportSelection(selection, bounds, zoom);
await writeFile(outputPath, JSON.stringify(response));
console.log(JSON.stringify({ paths: response.paths.length, vertices: response.visibleVertexCount }));
