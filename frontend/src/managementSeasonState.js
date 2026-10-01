import { ref, shallowRef } from "vue";
import { newestCatalogLeague } from "./publicSeasonState.js";

export function createManagementSeasonState(fetchCatalog, fetchDefault, saveDefault, onSavedDefault = () => {}) {
  const leagueId = ref("");
  const selectedYear = ref("");
  const leagues = shallowRef([]);
  const savingSiteDefault = ref(false);
  const defaultError = ref("");
  let catalogVersion = 0;
  function assignTarget(id) {
    leagueId.value = id;
    selectedYear.value = String(leagues.value.find((item) => item.league_id === id)?.year || "");
  }
  async function loadLeagues() {
    const version = ++catalogVersion;
    const [catalog, preference] = await Promise.all([fetchCatalog(), leagueId.value ? null : fetchDefault()]);
    if (version !== catalogVersion) return;
    leagues.value = catalog || [];
    if (!leagueId.value) assignTarget(preference?.default_league_id || newestCatalogLeague(leagues.value));
    else assignTarget(leagueId.value);
  }
  async function chooseLeague(id) {
    if (savingSiteDefault.value || !id || id === leagueId.value) return false;
    if (!leagues.value.some((item) => item.league_id === id)) return false;
    const previous = leagueId.value;
    assignTarget(id);
    savingSiteDefault.value = true;
    defaultError.value = "";
    try {
      await saveDefault(id);
    } catch (error) {
      assignTarget(previous);
      defaultError.value = error.message || "Could not save the site default season.";
      return false;
    } finally { savingSiteDefault.value = false; }
    onSavedDefault(id);
    return true;
  }
  async function chooseYear(year) {
    const candidates = leagues.value.filter((item) => String(item.year) === String(year));
    const id = newestCatalogLeague(candidates);
    return id ? chooseLeague(id) : false;
  }
  return { leagueId, selectedYear, leagues, savingSiteDefault, defaultError, loadLeagues, chooseLeague, chooseYear };
}
