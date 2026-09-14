import { shallowRef } from "vue";
import { fetchVisualizationSeasons } from "../api";
import { selectAvailableLeague } from "../selectedLeague";
export function useSeasonCatalog(ready = () => true) {
  const seasons = shallowRef([]);
  async function loadSeasons() {
    seasons.value = ((await fetchVisualizationSeasons()) || []).filter(ready);
    selectAvailableLeague(seasons.value); return seasons.value;
  }
  return { seasons, loadSeasons };
}
