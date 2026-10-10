import assert from "node:assert/strict";
import test from "node:test";
import { placeDisplayName, placeNoteKey } from "./place-notes.ts";
import type { PreviewPlace } from "./workspace-data.ts";

const place: PreviewPlace = {
  id: "place-0", name: "常去地点 1", position: [118.12345, 24.12345],
  visitCount: 8, dwellSeconds: 3600, confidence: 0.8, frequentProbability: 0.9,
  firstVisitedAt: "2026-01-01T00:00:00Z", lastVisitedAt: "2026-02-01T00:00:00Z",
};

test("a local note replaces only the matching inferred place label", () => {
  const notes = { [placeNoteKey("dataset-a", place)]: "家" };
  assert.equal(placeDisplayName("dataset-a", place, notes), "家");
  assert.equal(placeDisplayName("dataset-b", place, notes), place.name);
  assert.equal(placeDisplayName("dataset-a", { ...place, firstVisitedAt: "2026-03-01T00:00:00Z" }, notes), place.name);
});

test("cloud note deletion cannot resurrect a browser-only legacy name", () => {
  const notes = { [placeNoteKey("dataset-a", place)]: "旧名字" };
  assert.equal(placeDisplayName("dataset-a", { ...place, note: null }, notes), place.name);
  assert.equal(placeDisplayName("dataset-a", { ...place, note: { id: "note", revision: 1, label: "账号名字" } }, notes), "账号名字");
});
