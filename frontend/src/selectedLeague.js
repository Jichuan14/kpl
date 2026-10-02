import { fetchFactualSeasons, fetchSiteDefault } from "./api.js";
import { createPublicSeasonState } from "./publicSeasonState.js";

export const publicSeasonState = createPublicSeasonState(fetchFactualSeasons, fetchSiteDefault);
export const selectedLeagueId = publicSeasonState.selectedLeagueId;
export const selectPublicLeague = publicSeasonState.selectLeague;
