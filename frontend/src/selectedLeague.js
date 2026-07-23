import { ref, watch } from "vue";

export const DEFAULT_LEAGUE_ID = "20260003";
const STORAGE_KEY = "kpl-lab:selected-league-id";

function storedLeagueId() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || DEFAULT_LEAGUE_ID;
  } catch {
    return DEFAULT_LEAGUE_ID;
  }
}

export const selectedLeagueId = ref(storedLeagueId());

watch(selectedLeagueId, (leagueId) => {
  try {
    window.localStorage.setItem(STORAGE_KEY, leagueId);
  } catch {
    // Continue without persistence when browser storage is unavailable.
  }
});

export function selectAvailableLeague(leagues) {
  if (!leagues.length) {
    selectedLeagueId.value = "";
    return;
  }

  if (leagues.some((league) => league.league_id === selectedLeagueId.value)) {
    return;
  }

  const defaultLeague = leagues.find(
    (league) => league.league_id === DEFAULT_LEAGUE_ID
  );
  selectedLeagueId.value = (defaultLeague || leagues[0]).league_id;
}
