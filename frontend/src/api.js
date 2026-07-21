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
    throw new Error(text || `HTTP ${res.status}`);
  }
  const body = await res.json();
  if (body && body.success === false) {
    throw new Error(body.message || "Request failed");
  }
  return body.data;
}

export function fetchLeagues() {
  return request("/api/leagues");
}

export function fetchHeroBp({ leagueId, sort = "presence", limit = 40 } = {}) {
  const params = new URLSearchParams({ sort, limit: String(limit) });
  if (leagueId) params.set("league_id", leagueId);
  return request(`/api/bp/heroes?${params}`);
}

export function syncLeagueBp({ leagueId, matchLimit = null } = {}) {
  return request("/api/sync/league-bp", {
    method: "POST",
    body: JSON.stringify({
      league_id: leagueId || null,
      match_limit: matchLimit,
      recompute_stats: true,
    }),
  });
}
