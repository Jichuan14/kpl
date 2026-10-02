import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

import { heroAsset } from "./heroAssets.js";

test("cache-busts a late-added hero portrait that may have a cached 404", () => {
  assert.equal(heroAsset(547), "/assets/heroes/547.webp?v=72a0a7e");
});

test("Wangwei's versioned portrait is bundled as a WebP image", () => {
  const portrait = readFileSync(new URL("../public/assets/heroes/138.webp", import.meta.url));
  assert.equal(portrait.toString("ascii", 0, 4), "RIFF");
  assert.equal(portrait.toString("ascii", 8, 12), "WEBP");
  const revision = createHash("sha256").update(portrait).digest("hex").slice(0, 8);
  assert.equal(heroAsset(138), `/assets/heroes/138.webp?v=${revision}`);
});

test("keeps established hero portraits on their stable asset URLs", () => {
  assert.equal(heroAsset(545), "/assets/heroes/545.webp");
});

test("rejects invalid hero identifiers", () => {
  assert.equal(heroAsset("unknown"), "");
  assert.equal(heroAsset(0), "");
});
