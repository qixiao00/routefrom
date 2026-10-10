import assert from "node:assert/strict";
import test from "node:test";
import { classifyPlaceNotes } from "./place-note-records.ts";
const record = (id: string, placeIds: string[]) => ({ id, label: id, placeIds, revision: 1, position: [118, 30] as [number, number] });

test("place note binding preserves unmatched and split evidence without nearest-point fallback", () => {
  assert.deepEqual(classifyPlaceNotes([record("old", []), record("split", ["a", "b"]), record("safe", ["c"])]).map(note => note.status), ["unmatched", "ambiguous", "bound"]);
});
test("multiple labels colliding on a merged place remain separate records", () => {
  const records = classifyPlaceNotes([record("home", ["a"]), record("shop", ["a"])]);
  assert.deepEqual(records.map(note => note.status), ["collision", "collision"]);
  assert.deepEqual(records.map(note => note.label), ["home", "shop"]);
});
test("a split note also prevents another note silently owning one of its candidate places", () => {
  assert.deepEqual(classifyPlaceNotes([record("split", ["a", "b"]), record("other", ["b"])]).map(note => note.status), ["ambiguous", "collision"]);
});
