// Version every attempt (including unavailable seasons), so late responses can
// never restore observations after a selection change.
export function createFactualLoader({ start, load, value, missing, error, finish }) {
  let version = 0;
  let controller;
  async function run() {
    const currentVersion = ++version;
    controller?.abort();
    controller = new AbortController();
    const current = () => currentVersion === version;
    start();
    try {
      const result = await load(controller.signal);
      if (!current()) return;
      if (result == null) missing(); else value(result);
    } catch (err) {
      if (!current() || err.name === "AbortError") return;
      if (err.status === 404) missing(); else error(err);
    } finally {
      if (current()) finish();
    }
  }
  run.cancel = () => { version += 1; controller?.abort(); };
  return run;
}

export function neutralSeasonRankings(season) {
  return {
    schema_version: 3, evidence_scope: "season_only", initial_elo: 1500,
    league: season, history_league_ids: [season.league_id],
    team_rankings: (season.fixture_teams || []).map((team) => ({
      ...team, rank: null, elo: 1500, games: 0, target_season_games: 0,
      hybrid_score: null, decayed_win_rate: null, effective_games: 0,
      recent_10_wins: null, recent_10_games: 0,
    })), hero_rankings: [], position_rankings: [],
  };
}
export function isSeasonOnlyRanking(payload, leagueId) {
  return payload?.schema_version === 3 && payload.evidence_scope === "season_only" &&
    payload.league?.league_id === leagueId &&
    payload.history_league_ids?.length === 1 && payload.history_league_ids[0] === leagueId;
}
export function hasSeasonObservations(season) {
  return Number(season?.completed_match_count || 0) > 0 || Number(season?.completed_battle_count || 0) > 0;
}
