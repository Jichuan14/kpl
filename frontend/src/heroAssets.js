// Late-added files may replace a previously cached 404. Give only those files
// a revisioned URL so existing hero portraits keep their long-lived cache.
const HERO_ASSET_REVISIONS = new Map([[547, "72a0a7e"]]);

/** Return the bundled hero image path; never make a remote image request. */
export function heroAsset(heroId) {
  const id = Number(heroId);
  if (!Number.isInteger(id) || id <= 0) return "";
  const revision = HERO_ASSET_REVISIONS.get(id);
  return `/assets/heroes/${id}.webp${revision ? `?v=${revision}` : ""}`;
}
