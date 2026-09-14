import test from "node:test";
import assert from "node:assert/strict";

import { heroAsset } from "./heroAssets.js";

test("cache-busts a late-added hero portrait that may have a cached 404", () => {
  assert.equal(heroAsset(547), "/assets/heroes/547.webp?v=72a0a7e");
});

test("keeps established hero portraits on their stable asset URLs", () => {
  assert.equal(heroAsset(545), "/assets/heroes/545.webp");
});

test("rejects invalid hero identifiers", () => {
  assert.equal(heroAsset("unknown"), "");
  assert.equal(heroAsset(0), "");
});
