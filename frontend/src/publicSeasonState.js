import { ref, shallowRef } from "vue";

export function newestCatalogLeague(seasons) {
  return [...seasons].sort((a, b) =>
    Number(b.year || 0) - Number(a.year || 0) ||
    Number(b.season || 0) - Number(a.season || 0) ||
    String(b.start_time || "").localeCompare(String(a.start_time || "")) ||
    String(b.league_id).localeCompare(String(a.league_id))
  )[0]?.league_id || "";
}

// One instance per page load. Deliberately never reads browser storage.
export function createPublicSeasonState(fetchCatalog, fetchDefault) {
  const selectedLeagueId = ref("");
  const seasons = shallowRef([]);
  const savedDefaultLeagueId = ref("");
  let initialized = false;
  let manualChoice = false;
  let version = 0;
  function selectLeague(id) {
    selectedLeagueId.value = id;
    manualChoice = true;
  }
  function applySavedDefault(id) {
    if (!id) return;
    savedDefaultLeagueId.value = id;
    selectedLeagueId.value = id;
    manualChoice = false;
    // A deliberate save in this tab supersedes any initial preference read
    // already in flight. Its catalog can still load without restoring the old ID.
    initialized = true;
  }
  async function loadSeasons() {
    const requestVersion = ++version;
    const [catalog, preference] = await Promise.all([
      fetchCatalog(), initialized ? null : fetchDefault(),
    ]);
    if (requestVersion !== version) return seasons.value;
    seasons.value = catalog || [];
    if (!initialized) {
      const defaultId = preference?.default_league_id;
      savedDefaultLeagueId.value = defaultId || "";
      if (!manualChoice) {
        selectedLeagueId.value = defaultId || newestCatalogLeague(seasons.value);
      }
      initialized = true;
    }
    return seasons.value;
  }
  return { selectedLeagueId, seasons, savedDefaultLeagueId, selectLeague, applySavedDefault, loadSeasons };
}
