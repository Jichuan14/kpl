async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch (err) {
    throw new Error(
      `Cannot reach API (${err.message}). Is the backend running on :8000?`
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let message = text;
    try {
      message = JSON.parse(text)?.detail || text;
    } catch {
      // Keep the plain response body.
    }
    throw new Error(message || `HTTP ${res.status}`);
  }
  const body = await res.json();
  if (body && body.success === false) {
    throw new Error(body.message || "Request failed");
  }
  return body.data;
}

async function staticData(path) {
  let res;
  try {
    res = await fetch(path);
  } catch (err) {
    throw new Error(`Cannot load published analysis (${err.message}).`);
  }
  if (!res.ok) {
    throw new Error("Published analysis is not available yet. Run the analysis pipeline.");
  }
  return res.json();
}

export function fetchLeagues() {
  return request("/api/leagues");
}

export function fetchVisualizationSeasons() {
  return staticData("/assets/data/seasons.json");
}

export function fetchVisualizationPatterns({
  leagueId,
  minSelections = 2,
}) {
  void minSelections;
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/patterns.json`);
}

export function fetchTeamSynergies({ leagueId, minSelections = 2 }) {
  void minSelections;
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/team-synergies.json`);
}

export function fetchDraftModel(leagueId) {
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/draft-model.json`);
}

export function simulateDraft(state) {
  return request("/api/simulations/draft", {
    method: "POST",
    body: JSON.stringify(state),
  });
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
  });
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
