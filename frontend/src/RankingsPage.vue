<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { fetchPowerRankings, fetchVisualizationSeasons } from "./api";
import { heroAsset } from "./heroAssets";
import { language } from "./i18n";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { finishStartupLoading } from "./startupLoader";

const seasons = ref([]);
const leagueId = selectedLeagueId;
const payload = ref(null);
const loading = ref(false);
const error = ref("");
const board = ref("teams");
const selectedHeroId = ref(0);
const selectedPositionId = ref(0);
const heroSearch = ref("");
const playerSearch = ref("");
const minimumGames = ref(5);
const heroDirectoryOpen = ref(false);

const teams = computed(() => payload.value?.team_rankings || []);
const heroes = computed(() => payload.value?.hero_rankings || []);
const positions = computed(() => payload.value?.position_rankings || []);
function heroUsage(hero) {
  return (hero?.players || []).reduce(
    (total, player) => total + Number(player.target_season_games || 0),
    0
  );
}
const filteredHeroes = computed(() => {
  const needle = heroSearch.value.trim().toLocaleLowerCase();
  return heroes.value
    .filter(
      (hero) => !needle || hero.hero_name.toLocaleLowerCase().includes(needle)
    )
    .sort(
      (a, b) =>
        heroUsage(b) - heroUsage(a) ||
        b.player_count - a.player_count ||
        a.hero_name.localeCompare(b.hero_name)
    );
});
const selectedHero = computed(() =>
  heroes.value.find((hero) => hero.hero_id === selectedHeroId.value)
);
const shownPlayers = computed(() => {
  const needle = playerSearch.value.trim().toLocaleLowerCase();
  return (selectedHero.value?.players || []).filter(
    (player) =>
      player.target_season_games >= Number(minimumGames.value || 1) &&
      (!needle ||
        player.player_name.toLocaleLowerCase().includes(needle) ||
        player.current_team_name.toLocaleLowerCase().includes(needle))
  );
});
const selectedPosition = computed(() =>
  positions.value.find((position) => position.position === selectedPositionId.value)
);
const shownPositionPlayers = computed(() => {
  const needle = playerSearch.value.trim().toLocaleLowerCase();
  return (selectedPosition.value?.players || []).filter(
    (player) =>
      player.target_season_games >= Number(minimumGames.value || 1) &&
      (!needle ||
        player.player_name.toLocaleLowerCase().includes(needle) ||
        player.current_team_name.toLocaleLowerCase().includes(needle))
  );
});
const topTeams = computed(() => teams.value.slice(0, 3));
const maxTeamScore = computed(() => Math.max(...teams.value.map((row) => row.hybrid_score), 1));
const maxHeroPlayerScore = computed(() =>
  Math.max(...shownPlayers.value.map((row) => row.hybrid_score), 1)
);
const maxPositionPlayerScore = computed(() =>
  Math.max(...shownPositionPlayers.value.map((row) => row.hybrid_score), 1)
);

const positionLabels = {
  2: { en: "Mid", "zh-CN": "中路" },
  4: { en: "Roam", "zh-CN": "游走" },
  5: { en: "Jungle", "zh-CN": "打野" },
  6: { en: "Clash lane", "zh-CN": "对抗路" },
  7: { en: "Farm lane", "zh-CN": "发育路" },
};

function positionLabel(position) {
  return positionLabels[position.position]?.[language.value] || position.position_name;
}

function number(value, digits = 0) {
  return Number(value || 0).toLocaleString(language.value, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function shortDate(value) {
  if (!value) return "—";
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(language.value);
}

function scoreWidth(score, maximum) {
  return `${Math.max(2, (Number(score || 0) / maximum) * 100)}%`;
}

function selectHero(heroId) {
  selectedHeroId.value = heroId;
  heroDirectoryOpen.value = false;
  playerSearch.value = "";
}

async function loadSeasons() {
  const rows = (await fetchVisualizationSeasons()) || [];
  seasons.value = rows.filter((season) => season.rankings_ready);
  selectAvailableLeague(seasons.value);
}

async function loadRankings() {
  if (!leagueId.value) return;
  loading.value = true;
  error.value = "";
  try {
    payload.value = await fetchPowerRankings(leagueId.value, { cache: false });
    if (!heroes.value.some((hero) => hero.hero_id === selectedHeroId.value)) {
      selectedHeroId.value = filteredHeroes.value[0]?.hero_id || 0;
    }
    if (!positions.value.some((position) => position.position === selectedPositionId.value)) {
      selectedPositionId.value = positions.value[0]?.position || 0;
    }
  } catch (err) {
    payload.value = null;
    error.value = err.message || "Could not load power rankings.";
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadSeasons();
    await loadRankings();
  } catch (err) {
    error.value = err.message || "Could not load ranking data.";
  } finally {
    finishStartupLoading();
  }
});

watch(leagueId, loadRankings);
</script>

<template>
  <main class="rankings-page">
    <header class="rankings-hero">
      <div>
        <p class="rankings-eyebrow">Cross-season form · Decayed evidence</p>
        <h1>Power Rankings</h1>
        <p>
          Current strength without pretending old results last forever. Compare
          team Elo, compare players within each position, or open any hero to
          see which active player performs best.
        </p>
      </div>
      <label class="season-control">
        <span>Competition</span>
        <select v-model="leagueId">
          <option v-if="!seasons.length" value="">No ranking data yet</option>
          <option v-for="season in seasons" :key="season.league_id" :value="season.league_id">
            {{ season.year }} · {{ season.league_name }} · S{{ season.season }}
          </option>
        </select>
      </label>
    </header>

    <p v-if="error" class="rankings-message error">{{ error }}</p>
    <p v-else-if="loading" class="rankings-message">Calculating the form table…</p>

    <template v-if="payload && !loading">
      <section class="method-strip">
        <div>
          <span>Evidence window</span>
          <strong data-i18n-ignore>
            {{ payload.history_league_ids.length }}{{ language === "zh-CN" ? " 项赛事" : " competitions" }}
          </strong>
        </div>
        <div>
          <span>As of</span>
          <strong>{{ shortDate(payload.as_of) }}</strong>
        </div>
        <a href="/methodology#rankings">
          <span>Ranking calculation</span>
          <strong>Read methodology →</strong>
        </a>
      </section>

      <div class="board-switch" role="tablist" aria-label="Ranking board">
        <button
          type="button"
          role="tab"
          :aria-selected="board === 'teams'"
          :class="{ active: board === 'teams' }"
          @click="board = 'teams'"
        >
          <span>01</span>
          <strong>Team strength</strong>
          <small>Opponent-adjusted Elo + recent wins</small>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="board === 'heroes'"
          :class="{ active: board === 'heroes' }"
          @click="board = 'heroes'"
        >
          <span>02</span>
          <strong>Best on hero</strong>
          <small>Role-normalized performance by hero</small>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="board === 'positions'"
          :class="{ active: board === 'positions' }"
          @click="board = 'positions'; playerSearch = ''"
        >
          <span>03</span>
          <strong>Best by position</strong>
          <small>All-hero performance within each role</small>
        </button>
      </div>

      <section v-if="board === 'teams'" class="team-board">
        <div class="section-heading">
          <div>
            <p class="rankings-eyebrow">Selected-season field</p>
            <h2>Team power table</h2>
          </div>
          <p data-i18n-ignore>
            {{ language === "zh-CN"
              ? "72% 时间衰减 Elo · 28% 贝叶斯衰减胜率"
              : "72% decayed Elo · 28% Bayesian decayed win rate" }}
          </p>
        </div>

        <div class="podium">
          <article v-for="team in topTeams" :key="team.team_id" :class="`place-${team.rank}`">
            <span class="podium-rank">#{{ team.rank }}</span>
            <div class="team-monogram">{{ team.team_name.slice(0, 2) }}</div>
            <h3>{{ team.team_name }}</h3>
            <strong>{{ number(team.hybrid_score, 1) }}</strong>
            <small data-i18n-ignore>
              {{ number(team.elo) }} {{ language === "zh-CN" ? "Elo 分" : "Elo" }}
            </small>
          </article>
        </div>

        <div class="ranking-table-card">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Team</th>
                  <th>Power score</th>
                  <th data-i18n-ignore>{{ language === "zh-CN" ? "Elo 分" : "Elo" }}</th>
                  <th>Current-form win rate</th>
                  <th>Recent evidence</th>
                  <th>Recent 10</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="team in teams" :key="team.team_id">
                  <td class="rank-cell">{{ String(team.rank).padStart(2, "0") }}</td>
                  <td><strong>{{ team.team_name }}</strong></td>
                  <td class="score-cell">
                    <strong>{{ number(team.hybrid_score, 1) }}</strong>
                    <span><i :style="{ width: scoreWidth(team.hybrid_score, maxTeamScore) }"></i></span>
                  </td>
                  <td>{{ number(team.elo) }}</td>
                  <td>{{ percent(team.decayed_win_rate) }}</td>
                  <td>{{ number(team.effective_games, 1) }}</td>
                  <td>{{ team.recent_10_wins }}–{{ team.recent_10_games - team.recent_10_wins }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section v-else-if="board === 'heroes'" class="hero-board">
        <aside class="hero-directory">
          <div class="directory-head">
            <div>
              <p class="rankings-eyebrow">Hero directory</p>
              <h2 data-i18n-ignore>
                {{ heroes.length }}{{ language === "zh-CN" ? " 个英雄榜" : " boards" }}
              </h2>
            </div>
            <button
              class="directory-toggle"
              type="button"
              :aria-expanded="heroDirectoryOpen"
              @click="heroDirectoryOpen = !heroDirectoryOpen"
            >
              {{ selectedHero?.hero_name || "Choose hero" }} <span>⌄</span>
            </button>
          </div>
          <label class="hero-search">
            <span>Find a hero</span>
            <input v-model="heroSearch" type="search" placeholder="Search…" />
          </label>
          <div class="hero-list" :class="{ open: heroDirectoryOpen }">
            <button
              v-for="hero in filteredHeroes"
              :key="hero.hero_id"
              type="button"
              :class="{ active: hero.hero_id === selectedHeroId }"
              @click="selectHero(hero.hero_id)"
            >
              <img v-if="heroAsset(hero.hero_id)" :src="heroAsset(hero.hero_id)" alt="" />
              <span v-else>{{ hero.hero_name.slice(0, 1) }}</span>
              <div>
                <strong>{{ hero.hero_name }}</strong>
                <small data-i18n-ignore>
                  {{ heroUsage(hero) }}{{ language === "zh-CN" ? " 场使用" : " games" }} ·
                  {{ hero.player_count }}{{ language === "zh-CN" ? " 名选手" : " players" }}
                </small>
              </div>
            </button>
          </div>
        </aside>

        <div v-if="selectedHero" class="hero-detail">
          <header class="hero-banner">
            <div class="hero-portrait">
              <img v-if="heroAsset(selectedHero.hero_id)" :src="heroAsset(selectedHero.hero_id)" :alt="selectedHero.hero_name" />
              <span v-else>{{ selectedHero.hero_name.slice(0, 1) }}</span>
            </div>
            <div>
              <p class="rankings-eyebrow">Who plays this hero best?</p>
              <h2>{{ selectedHero.hero_name }}</h2>
              <span data-i18n-ignore>
                {{ selectedHero.player_count }}{{ language === "zh-CN" ? " 名当前选手" : " active player profiles" }}
              </span>
            </div>
          </header>

          <section class="hero-filters">
            <label>
              <span>Current-season games</span>
              <select v-model.number="minimumGames">
                <option :value="1">At least 1</option>
                <option :value="2">At least 2</option>
                <option :value="3">At least 3</option>
                <option :value="5">At least 5</option>
              </select>
            </label>
            <label>
              <span>Find player or team</span>
              <input v-model="playerSearch" type="search" placeholder="Search…" />
            </label>
          </section>

          <div class="player-board-card">
            <div class="player-board-head">
              <div>
                <p class="rankings-eyebrow">Hybrid performance</p>
                <h3 data-i18n-ignore>
                  {{ shownPlayers.length }}{{ language === "zh-CN" ? " 名符合条件的选手" : " qualifying players" }}
                </h3>
              </div>
              <span data-i18n-ignore>
                {{ language === "zh-CN" ? "40% KDA · 按位置标准化" : "40% KDA · role normalized" }}
              </span>
            </div>
            <article
              v-for="(player, index) in shownPlayers"
              :key="`${selectedHero.hero_id}-${player.player_id}`"
              class="player-row"
            >
              <span class="player-rank">{{ String(index + 1).padStart(2, "0") }}</span>
              <div class="player-identity">
                <strong>{{ player.player_name }}</strong>
                <small>{{ player.current_team_name }} · {{ player.positions.join(" / ") }}</small>
                <span><i :style="{ width: scoreWidth(player.hybrid_score, maxHeroPlayerScore) }"></i></span>
              </div>
              <div class="player-stat primary">
                <strong>{{ number(player.hybrid_score, 1) }}</strong>
                <span>score</span>
              </div>
              <div class="player-stat">
                <strong>{{ number(player.decayed_kda, 2) }}</strong>
                <span>decayed KDA</span>
              </div>
              <div class="player-stat">
                <strong>{{ percent(player.decayed_win_rate) }}</strong>
                <span>Current-form win rate</span>
              </div>
              <div class="player-stat">
                <strong>{{ player.games }}</strong>
                <span>career games</span>
              </div>
              <div class="player-stat confidence">
                <strong>{{ percent(player.confidence) }}</strong>
                <span>confidence</span>
              </div>
            </article>
            <p v-if="!shownPlayers.length" class="empty-board">No players match these filters.</p>
          </div>

          <aside class="formula-note">
            <strong>How the player score works</strong>
            <p data-i18n-ignore>
              {{ language === "zh-CN"
                ? "每局表现会与同位置、同赛事的选手进行比较：KDA 占 40%、官方 MVP 评分占 18%、参团率占 12%、英雄伤害占比占 10%、每分钟经济占 8%、对局结果占 12%。旧比赛会随时间衰减，小样本会加入相当于四局有效比赛的中性先验。"
                : "Each game is compared with players in the same role and competition: KDA 40%, official MVP score 18%, participation 12%, hero damage share 10%, gold pace 8%, and the battle result 12%. Older games decay and small samples receive a four-effective-game neutral prior." }}
            </p>
          </aside>
        </div>
      </section>

      <section v-else class="position-board">
        <div class="section-heading position-heading">
          <div>
            <p class="rankings-eyebrow">Active players · All heroes</p>
            <h2>Player rankings by position</h2>
          </div>
          <p>Compare players only with peers who play the same role.</p>
        </div>

        <div class="position-tabs" role="tablist" aria-label="Player position">
          <button
            v-for="position in positions"
            :key="position.position"
            type="button"
            role="tab"
            :aria-selected="position.position === selectedPositionId"
            :class="{ active: position.position === selectedPositionId }"
            @click="selectedPositionId = position.position; playerSearch = ''"
          >
            <strong>{{ positionLabel(position) }}</strong>
            <small data-i18n-ignore>
              {{ position.player_count }}{{ language === "zh-CN" ? " 名选手" : " players" }}
            </small>
          </button>
        </div>

        <template v-if="selectedPosition">
          <section class="hero-filters position-filters">
            <label>
              <span>Current-season games</span>
              <select v-model.number="minimumGames">
                <option :value="1">At least 1</option>
                <option :value="2">At least 2</option>
                <option :value="3">At least 3</option>
                <option :value="5">At least 5</option>
              </select>
            </label>
            <label>
              <span>Find player or team</span>
              <input v-model="playerSearch" type="search" placeholder="Search…" />
            </label>
          </section>

          <div class="player-board-card position-player-card">
            <div class="player-board-head">
              <div>
                <p class="rankings-eyebrow">{{ positionLabel(selectedPosition) }}</p>
                <h3 data-i18n-ignore>
                  {{ shownPositionPlayers.length }}{{ language === "zh-CN" ? " 名符合条件的选手" : " qualifying players" }}
                </h3>
              </div>
              <span data-i18n-ignore>
                {{ language === "zh-CN" ? "跨英雄汇总 · 按位置标准化" : "All heroes · role normalized" }}
              </span>
            </div>
            <article
              v-for="(player, index) in shownPositionPlayers"
              :key="`${selectedPosition.position}-${player.player_id}`"
              class="player-row"
            >
              <span class="player-rank">{{ String(index + 1).padStart(2, "0") }}</span>
              <div class="player-identity">
                <strong>{{ player.player_name }}</strong>
                <small data-i18n-ignore>
                  {{ player.current_team_name }} ·
                  {{ player.hero_count }}{{ language === "zh-CN" ? " 个英雄" : " heroes" }}
                </small>
                <span><i :style="{ width: scoreWidth(player.hybrid_score, maxPositionPlayerScore) }"></i></span>
              </div>
              <div class="player-stat primary">
                <strong>{{ number(player.hybrid_score, 1) }}</strong>
                <span>score</span>
              </div>
              <div class="player-stat">
                <strong>{{ number(player.decayed_kda, 2) }}</strong>
                <span>decayed KDA</span>
              </div>
              <div class="player-stat">
                <strong>{{ percent(player.decayed_win_rate) }}</strong>
                <span>Current-form win rate</span>
              </div>
              <div class="player-stat">
                <strong>{{ player.games }}</strong>
                <span>career games</span>
              </div>
              <div class="player-stat confidence">
                <strong>{{ percent(player.confidence) }}</strong>
                <span>confidence</span>
              </div>
            </article>
            <p v-if="!shownPositionPlayers.length" class="empty-board">No players match these filters.</p>
          </div>

          <aside class="formula-note">
            <strong>How position rankings work</strong>
            <p data-i18n-ignore>
              {{ language === "zh-CN"
                ? "榜单汇总选手在该位置使用所有英雄的单局表现。每局仍只与同赛事、同位置的选手比较；旧比赛会随时间衰减，并加入四局中性先验。至少 5 局的默认筛选可避免当前赛季极小样本占据榜首。"
                : "The board aggregates every hero a player used in this position. Each game is still compared only with the same role and competition; older games decay and four neutral games protect against small samples. The default five-game filter keeps tiny current-season samples from leading the visible board." }}
            </p>
          </aside>
        </template>
      </section>
    </template>
  </main>
</template>

<style scoped>
.rankings-page { width:min(1440px, calc(100% - 2rem)); margin:0 auto; padding:2.25rem 0 5rem; }
.rankings-hero { display:flex; align-items:flex-end; justify-content:space-between; gap:3rem; padding:clamp(2rem,5vw,3.5rem); border:1px solid var(--line); background:radial-gradient(circle at 82% 18%,rgba(196,92,38,.18),transparent 24%),linear-gradient(135deg,#fbfaf7,#e7f1ec); }
.rankings-eyebrow { margin:0 0 .5rem; color:var(--accent-deep); font-size:.66rem; letter-spacing:.14em; text-transform:uppercase; }
.rankings-hero h1 { margin:0; font:800 clamp(2.75rem,8vw,6.5rem)/.86 var(--display); letter-spacing:-.075em; white-space:nowrap; }
.rankings-hero > div > p:last-child { max-width:670px; margin:1.45rem 0 0; color:var(--ink-soft); }
.season-control { display:grid; min-width:320px; gap:.35rem; }
.season-control span,.hero-filters span,.hero-search > span { color:var(--ink-soft); font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; }
select,input { min-height:43px; padding:.62rem .75rem; border:1px solid var(--line); background:rgba(255,255,255,.94); color:var(--ink); font:inherit; }
.rankings-message { margin:.8rem 0 0; padding:1rem; border:1px solid var(--line); background:#fff; }.rankings-message.error{color:var(--warn)}
.method-strip { display:grid; grid-template-columns:repeat(3,1fr); margin-top:.75rem; border:1px solid var(--line); background:rgba(255,255,255,.78); }
.method-strip > div,.method-strip > a { margin:0; padding:1rem 1.15rem; border-right:1px solid var(--line); }.method-strip > :last-child{border-right:0}.method-strip>a{color:var(--ink);text-decoration:none}.method-strip>a:hover{background:rgba(15,138,107,.07)}.method-strip span,.method-strip strong{display:block}.method-strip span{color:var(--ink-soft);font-size:.6rem;text-transform:uppercase}.method-strip strong{margin-top:.25rem;font:700 1rem var(--display)}
.board-switch { display:grid; grid-template-columns:repeat(3,1fr); gap:.7rem; margin-top:.75rem; }
.board-switch button { display:grid; grid-template-columns:40px 1fr; gap:.12rem .7rem; padding:1.1rem; border:1px solid var(--line); background:rgba(255,255,255,.72); color:var(--ink); text-align:left; }.board-switch button>span{grid-row:1/3;color:var(--accent);font:700 .7rem var(--mono)}.board-switch strong{font:700 1.15rem var(--display)}.board-switch small{color:var(--ink-soft)}.board-switch button.active{border-color:var(--ink);background:var(--ink);color:#fff}.board-switch button.active small{color:#b9cbc8}
.team-board { margin-top:2.4rem; }.section-heading,.player-board-head { display:flex; align-items:flex-end; justify-content:space-between; gap:2rem; }.section-heading h2{margin:0;font:800 clamp(2.2rem,5vw,4.5rem)/.9 var(--display);letter-spacing:-.06em}.section-heading>p{max-width:340px;margin:0;color:var(--ink-soft);font-size:.68rem;text-align:right}
.podium { display:grid; grid-template-columns:repeat(3,1fr); gap:.7rem; margin-top:1.5rem; }.podium article{position:relative;min-height:210px;padding:1.35rem;border:1px solid var(--line);background:rgba(255,255,255,.82);overflow:hidden}.podium article::after{content:"";position:absolute;width:150px;height:150px;right:-65px;bottom:-70px;border-radius:50%;background:rgba(15,138,107,.09)}.podium-rank{color:var(--accent);font-weight:700}.team-monogram{display:grid;width:50px;height:50px;margin-top:1.4rem;place-items:center;border-radius:50%;background:var(--ink);color:#fff;font:700 .82rem var(--display)}.podium h3{margin:1rem 0 .5rem;font:700 1.45rem var(--display)}.podium article>strong{font:800 2.4rem var(--display)}.podium article>small{margin-left:.5rem;color:var(--ink-soft)}.podium .place-1{background:linear-gradient(135deg,#fff8e7,#edf4ef);border-color:rgba(196,92,38,.35)}
.ranking-table-card,.player-board-card{margin-top:.75rem;padding:1rem 1.2rem;border:1px solid var(--line);background:rgba(255,255,255,.84)}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:850px}th,td{padding:.9rem .65rem;border-bottom:1px solid var(--line);text-align:left}th{color:var(--ink-soft);font-size:.6rem;letter-spacing:.08em;text-transform:uppercase}.rank-cell{color:var(--accent);font-weight:700}.score-cell{min-width:170px}.score-cell>strong{display:inline-block;width:42px}.score-cell>span,.player-identity>span{display:inline-block;width:95px;height:5px;overflow:hidden;background:rgba(16,42,46,.08);vertical-align:middle}.score-cell i,.player-identity i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#c45c26)}
.hero-board { display:grid; grid-template-columns:255px minmax(0,1fr); gap:.75rem; margin-top:.75rem; align-items:start; }.hero-directory{position:sticky;top:.75rem;max-height:calc(100vh - 1.5rem);padding:1rem;border:1px solid var(--line);background:rgba(255,255,255,.88);overflow:auto}.directory-head h2{margin:0;font:700 1.45rem var(--display)}.directory-toggle{display:none}.hero-search{display:grid;gap:.3rem;margin:1rem 0 .65rem}.hero-list{display:grid;gap:.25rem}.hero-list button{display:grid;grid-template-columns:40px 1fr;gap:.65rem;align-items:center;width:100%;padding:.55rem;border:1px solid transparent;background:transparent;color:var(--ink);text-align:left}.hero-list button.active{border-color:rgba(15,138,107,.3);background:rgba(15,138,107,.08)}.hero-list button>img,.hero-list button>span{width:40px;height:40px;object-fit:cover;border-radius:50%;background:#dce8e2}.hero-list button>span{display:grid;place-items:center}.hero-list strong,.hero-list small{display:block}.hero-list strong{font-family:var(--display)}.hero-list small{color:var(--ink-soft);font-size:.6rem}
.hero-banner{display:flex;align-items:center;gap:1.2rem;padding:1.4rem;border:1px solid var(--line);background:linear-gradient(120deg,rgba(255,255,255,.9),rgba(231,241,236,.88))}.hero-portrait{display:grid;width:94px;height:94px;place-items:center;overflow:hidden;border-radius:50%;background:#dce8e2;font:800 2rem var(--display)}.hero-portrait img{width:100%;height:100%;object-fit:cover}.hero-banner h2{margin:0;font:800 clamp(2.5rem,6vw,5rem)/.9 var(--display);letter-spacing:-.06em}.hero-banner>div:last-child>span{display:block;margin-top:.5rem;color:var(--ink-soft)}
.hero-filters{display:grid;grid-template-columns:minmax(180px,.5fr) minmax(240px,1fr);gap:.65rem;margin-top:.7rem;padding:1rem;border:1px solid var(--line);background:rgba(255,255,255,.76)}.hero-filters label{display:grid;gap:.3rem}.player-board-head h3{margin:0;font:700 1.55rem var(--display)}.player-board-head>span{color:var(--ink-soft);font-size:.65rem}.player-row{display:grid;grid-template-columns:30px minmax(180px,1fr) repeat(5,minmax(75px,.35fr));gap:.65rem;align-items:center;padding:.9rem 0;border-top:1px solid var(--line)}.player-row:first-of-type{margin-top:1rem}.player-rank{color:var(--accent);font-weight:700}.player-identity{min-width:0}.player-identity strong,.player-identity small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.player-identity strong{font:700 1rem var(--display)}.player-identity small{margin:.1rem 0 .42rem;color:var(--ink-soft);font-size:.62rem}.player-identity>span{width:min(150px,100%)}.player-stat strong,.player-stat span{display:block}.player-stat strong{font:700 .95rem var(--display)}.player-stat span{color:var(--ink-soft);font-size:.55rem;text-transform:uppercase}.player-stat.primary strong{color:var(--accent-deep);font-size:1.2rem}.formula-note{margin-top:.7rem;padding:1rem 1.2rem;border-left:3px solid var(--accent);background:rgba(255,255,255,.72)}.formula-note strong{font-family:var(--display)}.formula-note p{margin:.35rem 0 0;color:var(--ink-soft);font-size:.68rem;line-height:1.65}.empty-board{padding:2rem 0;color:var(--ink-soft);text-align:center}
.position-board{margin-top:2.4rem}.position-tabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:.45rem;margin-top:1.5rem}.position-tabs button{display:grid;gap:.2rem;padding:1rem;border:1px solid var(--line);background:rgba(255,255,255,.76);color:var(--ink);text-align:left}.position-tabs button.active{border-color:var(--ink);background:var(--ink);color:#fff}.position-tabs strong{font:700 1rem var(--display)}.position-tabs small{color:var(--ink-soft);font-size:.6rem}.position-tabs button.active small{color:#b9cbc8}.position-filters{margin-top:.45rem}.position-player-card{margin-top:.45rem}
@media(max-width:980px){.rankings-hero{display:grid;gap:2rem}.season-control{min-width:0}.hero-board{grid-template-columns:1fr}.hero-directory{position:static;max-height:none}.directory-head{display:flex;align-items:center;justify-content:space-between}.directory-toggle{display:block;padding:.6rem .75rem;border:1px solid var(--line);background:#fff;color:var(--ink)}.hero-search{display:none}.hero-list{display:none;grid-template-columns:repeat(3,1fr);margin-top:.8rem}.hero-list.open{display:grid}.player-row{grid-template-columns:28px minmax(160px,1fr) repeat(3,minmax(72px,.35fr))}.player-stat:nth-last-child(-n+2){display:none}}
@media(max-width:680px){.rankings-page{width:min(100% - 1rem,640px);padding-top:.6rem}.rankings-hero{padding:1.5rem}.method-strip{display:none}.board-switch{grid-template-columns:repeat(3,minmax(0,1fr));gap:.35rem}.board-switch button{display:flex;min-height:44px;align-items:center;justify-content:center;padding:.5rem .4rem;text-align:center}.board-switch button>span,.board-switch button>small{display:none}.board-switch strong{font-size:.68rem;white-space:nowrap}.podium{grid-template-columns:repeat(3,minmax(0,1fr));gap:.35rem}.podium article{min-height:0;padding:.7rem}.podium article::after{width:90px;height:90px;right:-42px;bottom:-45px}.podium .team-monogram{width:34px;height:34px;margin-top:.7rem;font-size:.62rem}.podium h3{margin:.65rem 0 .3rem;font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.podium article>strong{font-size:1.4rem}.podium article>small{display:block;margin:.2rem 0 0;font-size:.58rem}.hero-list.open{grid-template-columns:1fr}.hero-banner{align-items:flex-start}.hero-portrait{width:66px;height:66px}.hero-filters{display:none}.position-tabs{grid-template-columns:repeat(3,minmax(0,1fr));margin-top:1rem}.position-tabs button{padding:.7rem}.player-row{grid-template-columns:25px minmax(130px,1fr) 70px 75px}.player-stat:nth-of-type(n+5){display:none}.player-stat.confidence{display:none}.section-heading,.player-board-head{align-items:flex-start;flex-direction:column;gap:.5rem}.section-heading>p{text-align:left}}
</style>
