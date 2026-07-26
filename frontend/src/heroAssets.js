/** Return the bundled hero image path; never make a remote image request. */
export function heroAsset(heroId) {
  const id = Number(heroId);
  return Number.isInteger(id) && id > 0 ? `/assets/heroes/${id}.jpg` : "";
}
