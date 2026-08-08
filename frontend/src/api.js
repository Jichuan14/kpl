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
      const detail = JSON.parse(text)?.detail;
      if (detail && typeof detail === "object") {
        message = detail.message || `HTTP ${res.status}`;
        if (detail.request_id) message += ` · ${detail.request_id}`;
      } else {
        message = detail || text;
      }
    } catch {
      // Keep the plain response body.
    }
    const error = new Error(message || `HTTP ${res.status}`);
    error.status = res.status;
    const retryAfter = Number(res.headers.get("Retry-After"));
    if (Number.isFinite(retryAfter) && retryAfter > 0) {
      error.retryAfter = retryAfter;
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

async function staticData(path, { signal, cache = true } = {}) {
  if (cache && staticCache.has(path)) return staticCache.get(path);
  const load = (async () => {
  let res;
  try {
    res = await fetch(path, { signal });
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
    staticCache.set(path, load);
    load.catch(() => staticCache.delete(path));
  }
  return load;
}

export function fetchLeagues() {
  return request("/api/leagues");
}

export function fetchSeasonTeams(leagueId) {
  return request(`/api/leagues/${encodeURIComponent(leagueId)}/teams`);
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

export function fetchTeamSynergies({ leagueId, minSelections = 2 }) {
  void minSelections;
  return staticData(`/assets/data/${encodeURIComponent(leagueId)}/team-synergies.json`);
}

export function fetchDraftModel(leagueId) {
  const params = new URLSearchParams({ league_id: leagueId });
  return request(`/api/simulations/model?${params}`);
}

export function fetchLearnedFeatureSpace(leagueId) {
  const params = new URLSearchParams({ league_id: leagueId });
  return request(`/api/simulations/feature-space?${params}`);
}

export function simulateDraft(state) {
  return request("/api/simulations/draft", {
    method: "POST",
    body: JSON.stringify(state),
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

export function fetchCoachUsage() {
  return request("/api/coach/usage");
}

export function updateCoachLimits(limits) {
  return request("/api/coach/limits", {
    method: "PUT",
    body: JSON.stringify(limits),
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
