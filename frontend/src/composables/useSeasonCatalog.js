import { publicSeasonState } from "../selectedLeague.js";
// The full local catalog includes new seasons with zero model artifacts.
export function useSeasonCatalog() {
  return { seasons: publicSeasonState.seasons, loadSeasons: publicSeasonState.loadSeasons };
}
