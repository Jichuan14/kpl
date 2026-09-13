async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error(
      `Cannot reach API (${err.message}). Is the backend running on :8000?`
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let message = text;
    let errorCode = "";
    try {
      const detail = JSON.parse(text)?.detail;
      if (detail && typeof detail === "object") {
        message = detail.message || `HTTP ${res.status}`;
        errorCode = detail.code || "";
        if (detail.request_id) message += ` · ${detail.request_id}`;
      } else {
        message = detail || text;
      }
    } catch {
      // Keep the plain response body.
    }
    const error = new Error(message || `HTTP ${res.status}`);
    error.status = res.status;
    error.code = errorCode;
    const retryAfter = Number(res.headers.get("Retry-After"));
    if (Number.isFinite(retryAfter) && retryAfter > 0) {
      error.retryAfter = retryAfter;
    } else if (res.status === 429) {
      error.retryAfter = 20;
    }
    throw error;
  }
  const body = await res.json();
  if (body && body.success === false) {
    throw new Error(body.message || "Request failed");
  }
  return body.data;
}

const staticCache = new Map();
const STATIC_CACHE_TTL = 5 * 60_000;

async function staticData(path, { signal, cache = true } = {}) {
  // Never share a caller-owned AbortSignal: aborting one view must not abort a
  // concurrent consumer of the same published artifact.
  const cached = staticCache.get(path);
  if (cache && cached && cached.expiresAt > Date.now()) return cached.promise;
  const load = (async () => {
  let res;
  try {
    res = await fetch(path, { signal: cache ? undefined : signal });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error(`Cannot load published analysis (${err.message}).`);
  }
  if (!res.ok) {
    throw new Error("Published analysis is not available yet. Run the analysis pipeline.");
  }
  return res.json();
  })();
  if (cache) {
    staticCache.set(path, { promise: load, expiresAt: Date.now() + STATIC_CACHE_TTL });
    load.catch(() => { if (staticCache.get(path)?.promise === load) staticCache.delete(path); });
  }
  return load;
}

export function invalidatePublishedData(leagueId) {
  const prefix = leagueId ? `/assets/data/${encodeURIComponent(leagueId)}/` : "/assets/data/";
  for (const path of staticCache.keys()) if (path.startsWith(prefix)) staticCache.delete(path);
}

// Kept intentionally small and explicit for deterministic browser-client tests.
export function resetPublishedDataCacheForTests() { staticCache.clear(); }

export function fetchLeagues() {
  return request("/api/leagues");
}

export function fetchSeasonTeams(leagueId) {
  return request(`/api/leagues/${encodeURIComponent(leagueId)}/teams`);
}

export function fetchUpcomingMatch(leagueId, { nextOnly = false } = {}) {
  const query = nextOnly ? "?next_only=true" : "";
  return request(`/api/leagues/${encodeURIComponent(leagueId)}/upcoming-match${query}`);
}

export function fetchDailyMatches({ date } = {}) {
  const query = date ? `?match_date=${encodeURIComponent(date)}` : "";
  return request(`/api/leagues/daily-matches${query}`);
}

export function fetchLiveMatch({ leagueId, teamAId, teamBId, matchId }) {
  const params = new URLSearchParams({ team_a_id: teamAId, team_b_id: teamBId, match_id: matchId });
  return request(`/api/leagues/${encodeURIComponent(leagueId)}/live-match?${params}`);
}

export function refreshLiveMatch({ leagueId, teamAId, teamBId, matchId }) {
  const params = new URLSearchParams({ team_a_id: teamAId, team_b_id: teamBId, match_id: matchId });
  return request(`/api/leagues/${encodeURIComponent(leagueId)}/live-match/refresh?${params}`, {
    method: "POST",
  });
}

export function fetchLiveWinnerPredictions({ leagueId, matchId, gameNumber }) {
  const params = new URLSearchParams({ match_id: matchId, game_number: String(gameNumber) });
  return request(`/api/leagues/${encodeURIComponent(leagueId)}/live-match/predictions?${params}`);
}

export function saveLiveWinnerPrediction({
  leagueId,
  visitorId,
  matchId,
  gameNumber,
  teamAId,
  teamBId,
  winnerTeamId,
  bestOf = null,
  teamAScore = null,
  teamBScore = null,
}) {
  return request(`/api/leagues/${encodeURIComponent(leagueId)}/live-match/predictions`, {
    method: "POST",
    body: JSON.stringify({
      visitor_id: visitorId,
      match_id: matchId,
      game_number: gameNumber,
      team_a_id: teamAId,
      team_b_id: teamBId,
      winner_team_id: winnerTeamId,
      best_of: bestOf,
      team_a_score: teamAScore,
      team_b_score: teamBScore,
    }),
  });
}

export function fetchVisualizationSeasons() {
  return staticData("/assets/data/seasons.json");
}

export function fetchMetaHistory(options) {
  return staticData("/assets/data/meta-history.json", options);
}

export function fetchVisualizationPatterns({
  leagueId,
  minSelections = 2,
  relation,
  context,
  signal,
}) {
  void minSelections;
  if (!relation || !context) {
    throw new Error("A pattern relation and context are required.");
  }
  return staticData(
    `/assets/data/${encodeURIComponent(leagueId)}/patterns/${encodeURIComponent(relation)}/${encodeURIComponent(context)}.json`,
    { signal }
  );
}

export function fetchPatternManifest(leagueId, options) {
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/overview.json`, options);
}

export function fetchHeroResponses(leagueId, options) {
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/hero-responses.json`, options);
}

export function fetchBattleLineups(leagueId, options) {
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/battle-lineups.json`, options);
}

export function fetchTeamSynergies({ leagueId, minSelections = 2, signal } = {}) {
  void minSelections;
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/team-synergies.json`, { signal });
}

export function fetchPowerRankings(leagueId, options) {
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/rankings.json`, options);
}

export function fetchDraftModel(leagueId) {
  const params = new URLSearchParams({ league_id: leagueId });
  return request(`/api/simulations/model?${params}`);
}

export function fetchLearnedFeatureSpace(leagueId) {
  const params = new URLSearchParams({ league_id: leagueId });
  return request(`/api/simulations/feature-space?${params}`);
}

export function fetchHeroMatchupRecommendations(payload) {
  return request("/api/simulations/hero-matchup", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchUltimateLineups(leagueId) {
  const params = new URLSearchParams({ league_id: leagueId });
  return request(`/api/simulations/ultimate-lineups?${params}`);
}

export function fetchUltimateCounterLineup({ leagueId, targetHeroIds }) {
  return request("/api/simulations/ultimate-lineups/counter", {
    method: "POST",
    body: JSON.stringify({
      league_id: leagueId,
      target_hero_ids: targetHeroIds,
    }),
  });
}

export function simulateDraft(state) {
  return request("/api/simulations/draft", {
    method: "POST",
    body: JSON.stringify(state),
  });
}

export function simulateDraftScenario(state, { signal } = {}) {
  return request("/api/simulations/draft-scenario", {
    method: "POST",
    signal,
    body: JSON.stringify(state),
  });
}

export function recommendLineup(state) {
  return request("/api/simulations/recommend-lineup", {
    method: "POST",
    body: JSON.stringify(state),
  });
}

export function scoreLineup(payload) {
  return request("/api/simulations/score-lineup", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function scoreNeutralLineup(payload) {
  return request("/api/simulations/score-neutral-lineup", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchSelectionCommentary(state) {
  return request("/api/simulations/commentary", {
    method: "POST",
    body: JSON.stringify(state),
  });
}

export function askDraftCoach(payload) {
  return request("/api/coach", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function askDraftCoachStream(payload, { signal, onEvent } = {}) {
  const { parseCoachStreamChunk } = await import("./coachStream.js");
  let res;
  try {
    res = await fetch("/api/coach/stream", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error(
      `Cannot reach API (${err.message}). Is the backend running on :8000?`
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail = {};
    try {
      detail = JSON.parse(text)?.detail || {};
    } catch {
      detail = { message: text };
    }
    const error = new Error(detail.message || `HTTP ${res.status}`);
    error.status = res.status;
    error.code = detail.code;
    const retryAfter = Number(res.headers.get("Retry-After"));
    if (Number.isFinite(retryAfter) && retryAfter > 0) error.retryAfter = retryAfter;
    throw error;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseCoachStreamChunk(buffer, decoder.decode(value, { stream: true }));
    buffer = parsed.buffer;
    for (const event of parsed.events) {
      onEvent?.(event);
      if (event.type === "result") result = event.data;
      if (event.type === "error") {
        const error = new Error(event.message || "stream error");
        error.code = event.code;
        throw error;
      }
    }
  }
  if (buffer.trim()) {
    const parsed = parseCoachStreamChunk(buffer, "\n");
    for (const event of parsed.events) {
      onEvent?.(event);
      if (event.type === "result") result = event.data;
      if (event.type === "error") {
        const error = new Error(event.message || "stream error");
        error.code = event.code;
        throw error;
      }
    }
  }
  return result;
}

export function clearCoachConversation() {
  return request("/api/coach/conversation/clear", { method: "POST" });
}

export function prepareScoutReport(payload) {
  return request("/api/coach/scout-report", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchCoachUsage() {
  return request("/api/coach/usage");
}

export function updateCoachLimits(limits) {
  return request("/api/coach/limits", {
    method: "PUT",
    body: JSON.stringify(limits),
  });
}

export function trackVisitor({ visitorId, pagePath }) {
  return request("/api/analytics/visits", {
    method: "POST",
    body: JSON.stringify({ visitor_id: visitorId, page_path: pagePath }),
  });
}

export function fetchVisitorStats() {
  return request("/api/analytics/summary");
}

export function syncLeagues() {
  return request("/api/sync/leagues", { method: "POST" });
}

export function fetchDataStatus(leagueId) {
  const params = new URLSearchParams({ league_id: leagueId });
  return request(`/api/data/status?${params}`);
}

export function runAnalysisStep({ leagueId, step }) {
  return request("/api/pipeline/run", {
    method: "POST",
    body: JSON.stringify({ league_id: leagueId, step }),
  });
}

export function publishFrontendAssets(leagueId) {
  return request("/api/pipeline/publish", {
    method: "POST",
    body: JSON.stringify({ league_id: leagueId }),
  }).then((result) => { invalidatePublishedData(leagueId); return result; });
}

export function fetchHeroBp({ leagueId, sort = "presence", limit = 40 } = {}) {
  const params = new URLSearchParams({ sort, limit: String(limit) });
  if (leagueId) params.set("league_id", leagueId);
  return request(`/api/bp/heroes?${params}`);
}

export function syncLeagueBp({ leagueId, matchLimit = null, runAnalysis = false } = {}) {
  return request("/api/sync/league-bp", {
    method: "POST",
    body: JSON.stringify({
      league_id: leagueId || null,
      match_limit: matchLimit,
      recompute_stats: true,
      run_analysis: runAnalysis,
      incremental: true,
    }),
  });
}
