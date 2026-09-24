import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { buildViewportSelection, queryViewportSelection } from "../src/lib/workspace-viewport.ts";

const previewPath = path.resolve("..", "data", "generated", "workspace-preview.json");
const outputPath = path.resolve("..", "data", "generated", "view-z9-audit.json");
const preview = JSON.parse(await readFile(previewPath, "utf8"));
const last = preview.paths.at(-1)?.vertices.at(-1);
if (!last) throw new Error("preview has no trajectory vertices");

const selection = buildViewportSelection(preview.paths, [{
  start: preview.dataset.startedAt,
  end: preview.dataset.endedAt,
}], preview.modeLegs);
const bounds = [last[1] - 1, last[2] - 0.65, last[1] + 1, last[2] + 0.65];
const response = queryViewportSelection(selection, bounds, 9);
await writeFile(outputPath, JSON.stringify(response));
console.log(JSON.stringify({ paths: response.paths.length, vertices: response.visibleVertexCount }));
