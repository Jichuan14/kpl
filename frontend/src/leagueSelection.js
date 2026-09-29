export function inferLeagueSelectionMode(storedId, storedMode, legacyDefaultId) {
  if (storedMode === "explicit" || storedMode === "auto") return storedMode;
  // Older versions stored the hard-coded default without recording whether
  // the visitor chose it. Preserve non-default IDs as intentional choices.
  return storedId && storedId !== legacyDefaultId ? "explicit" : "auto";
}

export function newestPublishedLeague(leagues) {
  return [...leagues].sort((a, b) =>
    Number(b.year || 0) - Number(a.year || 0) ||
    String(b.start_time || "").localeCompare(String(a.start_time || "")) ||
    Number(b.season || 0) - Number(a.season || 0) ||
    String(b.league_id).localeCompare(String(a.league_id))
  )[0] || null;
}

export function availableLeagueId(leagues, mode, preferredId) {
  if (mode === "explicit" && leagues.some((league) => league.league_id === preferredId)) {
    return preferredId;
  }
  return newestPublishedLeague(leagues)?.league_id || "";
}
