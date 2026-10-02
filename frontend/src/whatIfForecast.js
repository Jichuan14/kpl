export function buildWhatIfForecastPayload(context, scenario) {
  const { leagueId, modelVersion, blueTeam, redTeam, modelType } = context;
  return {
    league_id: leagueId,
    model_version: modelVersion,
    model_type: modelType,
    blue_team_id: String(blueTeam.team_id),
    blue_team_name: blueTeam.team_name,
    red_team_id: String(redTeam.team_id),
    red_team_name: redTeam.team_name,
    bp_order: scenario.bpOrder,
    blue_picks: [...scenario.board.blue_picks],
    red_picks: [...scenario.board.red_picks],
    blue_bans: [...scenario.board.blue_bans],
    red_bans: [...scenario.board.red_bans],
    blue_used_previous_battles: [...scenario.blueUsed],
    red_used_previous_battles: [...scenario.redUsed],
  };
}
