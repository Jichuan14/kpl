import { ref, watch } from "vue";
import { availableLeagueId, inferLeagueSelectionMode } from "./leagueSelection.js";

export const DEFAULT_LEAGUE_ID = "20260003"; // Legacy preference migration only.
const STORAGE_KEY = "kpl-lab:selected-league-id";
const MODE_KEY = "kpl-lab:league-selection-mode";
const PREFERRED_KEY = "kpl-lab:preferred-league-id";

function read(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function write(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* Storage is optional. */ }
}

const storedId = read(STORAGE_KEY);
let selectionMode = inferLeagueSelectionMode(storedId, read(MODE_KEY), DEFAULT_LEAGUE_ID);
let preferredId = read(PREFERRED_KEY) || (selectionMode === "explicit" ? storedId : null);
let applyingCatalog = false;
export const selectedLeagueId = ref(storedId || DEFAULT_LEAGUE_ID);

watch(selectedLeagueId, (leagueId) => {
  write(STORAGE_KEY, leagueId);
  if (!applyingCatalog) {
    selectionMode = "explicit";
    preferredId = leagueId;
    write(MODE_KEY, selectionMode);
    write(PREFERRED_KEY, preferredId);
  }
}, { flush: "sync" });

export function selectAvailableLeague(publishedLeagues) {
  const nextId = availableLeagueId(publishedLeagues, selectionMode, preferredId);
  applyingCatalog = true;
  try { selectedLeagueId.value = nextId; } finally { applyingCatalog = false; }
  write(MODE_KEY, selectionMode);
}
