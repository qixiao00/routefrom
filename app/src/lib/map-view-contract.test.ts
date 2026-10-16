import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parseMapViewConfig } from "./map-view-config.ts";

test("website and Python backup share the same v1 map-state wire fixtures", () => {
  const fixtures = JSON.parse(readFileSync(new URL("../../../contracts/map-view-v1-fixtures.json", import.meta.url), "utf8"));
  assert.doesNotThrow(() => parseMapViewConfig(fixtures.base));
  for (const item of fixtures.cases) {
    const config = structuredClone(fixtures.base);
    let target = config;
    for (const key of item.path.slice(0, -1)) target = target[key];
    target[item.path.at(-1)] = item.value;
    if (item.valid) assert.doesNotThrow(() => parseMapViewConfig(config));
    else assert.throws(() => parseMapViewConfig(config), JSON.stringify(item.path));
  }
});
