// A job can finish after the user switches seasons. Only the selected season
// may replace the status currently shown on the management page.
export async function loadSelectedStatus(fetchStatus, requestedLeagueId, selectedLeagueId, applyStatus) {
  const status = await fetchStatus(requestedLeagueId);
  if (requestedLeagueId === selectedLeagueId()) applyStatus(status);
}
