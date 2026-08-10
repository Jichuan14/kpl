<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { fetchDraftModel } from "./api";
import { heroAsset } from "./heroAssets";
import { language, t } from "./i18n";
import { selectedLeagueId } from "./selectedLeague";
import { finishStartupLoading } from "./startupLoader";

const model = ref(null);
const activeSection = ref("system-map");

const guideSections = [
  { id: "system-map", number: "01", label: "System map" },
  { id: "training", number: "02", label: "Training data" },
  { id: "prediction", number: "03", label: "One prediction" },
  { id: "team-aware", number: "04", label: "Team awareness" },
  { id: "outputs", number: "05", label: "Simulator & coach" },
  { id: "rankings", number: "06", label: "Power rankings" },
];

const exampleHeroes = {
  lubanMaster: { id: 525, name: "鲁班大师" },
  gongsunLi: { id: 199, name: "公孙离" },
  dunshan: { id: 509, name: "盾山" },
  goya: { id: 548, name: "戈娅" },
  zhaoYun: { id: 107, name: "赵云" },
  xiahouDun: { id: 126, name: "夏侯惇" },
};

const teamProbabilityExamples = [
  {
    team: "Wolves",
    pick: [
      { hero: exampleHeroes.zhaoYun, probability: 28 },
      { hero: exampleHeroes.xiahouDun, probability: 19 },
      { hero: exampleHeroes.goya, probability: 14 },
    ],
    ban: [
      { hero: exampleHeroes.lubanMaster, probability: 32 },
      { hero: exampleHeroes.dunshan, probability: 23 },
      { hero: exampleHeroes.goya, probability: 14 },
    ],
  },
  {
    team: "AG",
    pick: [
      { hero: exampleHeroes.goya, probability: 27 },
      { hero: exampleHeroes.zhaoYun, probability: 15 },
      { hero: exampleHeroes.xiahouDun, probability: 12 },
    ],
    ban: [
      { hero: exampleHeroes.dunshan, probability: 30 },
      { hero: exampleHeroes.lubanMaster, probability: 21 },
      { hero: exampleHeroes.goya, probability: 13 },
    ],
  },
];

const activeSectionIndex = computed(() =>
  Math.max(0, guideSections.findIndex((section) => section.id === activeSection.value))
);
const activeSectionMeta = computed(() => guideSections[activeSectionIndex.value]);
const sectionProgress = computed(
  () => `${((activeSectionIndex.value + 1) / guideSections.length) * 100}%`
);

const learnableModel = computed(() =>
  model.value?.available_models?.find((candidate) => candidate.id === "learnable")
);
const heroCount = computed(() => model.value?.heroes?.length || 0);
const draftSlotCount = computed(() => model.value?.draft_sequence?.length || 0);

function number(value) {
  return Number(value || 0).toLocaleString(language.value);
}

async function loadModel() {
  if (!selectedLeagueId.value) return;
  try {
    model.value = await fetchDraftModel(selectedLeagueId.value);
  } catch {
    model.value = null;
  }
}

let scrollFrame = null;

function updateActiveSection() {
  scrollFrame = null;
  const pageBottom = window.scrollY + window.innerHeight;
  const documentBottom = document.documentElement.scrollHeight;

  if (pageBottom >= documentBottom - 8) {
    activeSection.value = guideSections.at(-1).id;
    return;
  }

  const readingLine = Math.min(window.innerHeight * 0.3, 240);
  let currentSection = guideSections[0].id;

  for (const section of guideSections) {
    const element = document.getElementById(section.id);
    if (element && element.getBoundingClientRect().top <= readingLine) {
      currentSection = section.id;
    }
  }

  activeSection.value = currentSection;
}

function scheduleScrollSpy() {
  if (scrollFrame === null) {
    scrollFrame = window.requestAnimationFrame(updateActiveSection);
  }
}

function selectSection(sectionId) {
  activeSection.value = sectionId;
}

function scrollToHashSection() {
  const sectionId = decodeURIComponent(window.location.hash.slice(1));
  if (!guideSections.some((section) => section.id === sectionId)) return;
  document.getElementById(sectionId)?.scrollIntoView({ block: "start" });
  activeSection.value = sectionId;
}

onMounted(async () => {
  // The rankings page links here with #rankings. This component is lazy-loaded,
  // so browsers may resolve the hash before its target has entered the DOM.
  await nextTick();
  scrollToHashSection();
  await loadModel();
  await nextTick();
  scrollToHashSection();
  window.addEventListener("scroll", scheduleScrollSpy, { passive: true });
  window.addEventListener("resize", scheduleScrollSpy);
  updateActiveSection();
  finishStartupLoading();
});

onBeforeUnmount(() => {
  window.removeEventListener("scroll", scheduleScrollSpy);
  window.removeEventListener("resize", scheduleScrollSpy);
  if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame);
});

watch(activeSection, async (sectionId) => {
  if (window.innerWidth > 820) return;
  await nextTick();
  document
    .querySelector(`.guide-rail a[data-section-id="${sectionId}"]`)
    ?.scrollIntoView({ block: "nearest", inline: "center" });
});

watch(selectedLeagueId, loadModel);
</script>

<template>
  <main class="method-page">
    <header class="method-hero">
      <div class="hero-copy">
        <p class="eyebrow"><span>Model field guide</span><b data-i18n-ignore> · {{ selectedLeagueId }}</b></p>
        <h1>Evidence in.<br /><span>Next move out.</span></h1>
        <p class="hero-intro">
          Follow one KPL draft decision from historical match data to a
          team-aware probability—and see exactly where the AI coach enters the
          system.
        </p>
        <div class="hero-actions">
          <a class="primary-action" href="#system-map">Trace the system</a>
          <a class="secondary-action" href="/simulator">Open the simulator ↗</a>
        </div>
      </div>

      <div class="model-terminal" aria-label="Current model summary">
        <div class="terminal-head">
          <span>Current model</span>
          <i :class="{ ready: learnableModel?.available }"></i>
          <strong>{{ learnableModel?.available ? "Ready" : "Unavailable" }}</strong>
        </div>
        <div class="terminal-body">
          <p><span>SEASON</span><strong>{{ selectedLeagueId || "—" }}</strong></p>
          <p><span>ENGINE</span><strong>Team-aware learnable</strong></p>
          <p><span>TRAINING ACTIONS</span><strong>{{ model ? number(model.training_decisions) : "—" }}</strong></p>
          <p><span>HERO VOCABULARY</span><strong>{{ model ? number(heroCount) : "—" }}</strong></p>
          <p><span>DRAFT SLOTS</span><strong>{{ model ? number(draftSlotCount) : "—" }}</strong></p>
        </div>
        <div class="terminal-foot">
          <span>MODEL OUTPUT</span>
          <strong>Next legal pick / ban probability</strong>
        </div>
      </div>
    </header>

    <section class="truth-strip" aria-label="Key facts">
      <article>
        <span>01</span>
        <strong>Backend computes</strong>
        <p>Probabilities and statistics never come from Kimi’s memory.</p>
      </article>
      <article>
        <span>02</span>
        <strong>Teams are learned</strong>
        <p>Acting-team and opponent embeddings are part of the trained model.</p>
      </article>
      <article>
        <span>03</span>
        <strong>Legality comes first</strong>
        <p>A hero is removed before scoring if the current rules make it unavailable.</p>
      </article>
      <article>
        <span>04</span>
        <strong>Every step recalculates</strong>
        <p>The distribution changes after each pick, ban, team, or side change.</p>
      </article>
    </section>

    <div class="guide-layout">
      <aside class="guide-rail">
        <div class="rail-status" aria-live="polite">
          <div>
            <span>Current section</span>
            <strong>{{ t(activeSectionMeta.label) }}</strong>
          </div>
          <b data-i18n-ignore>
            {{ activeSectionMeta.number }} / {{ String(guideSections.length).padStart(2, "0") }}
          </b>
          <div class="rail-progress" aria-hidden="true">
            <i :style="{ width: sectionProgress }"></i>
          </div>
        </div>
        <p>On this page</p>
        <nav aria-label="On this page">
          <a
            v-for="section in guideSections"
            :key="section.id"
            :href="`#${section.id}`"
            :data-section-id="section.id"
            :class="{ active: activeSection === section.id }"
            :aria-current="activeSection === section.id ? 'location' : undefined"
            @click="selectSection(section.id)"
          >
            <span data-i18n-ignore>{{ section.number }}</span>{{ t(section.label) }}
          </a>
        </nav>
      </aside>

      <div class="guide-content">
        <section id="system-map" class="guide-section system-section">
          <div class="section-heading">
            <span>01 · System map</span>
            <h2>Five layers, one answer.</h2>
            <p>
              The project is not one giant AI model. It is a chain of small,
              inspectable systems with a clear handoff between each layer.
            </p>
          </div>

          <div class="system-map">
            <article class="system-card source-card">
              <span>INPUT</span>
              <b>Official match records</b>
              <p>Matches, battles, BP actions, teams, players, sides, and winners.</p>
            </article>
            <span class="flow-arrow" aria-hidden="true">→</span>
            <article class="system-card">
              <span>PREPARE</span>
              <b>Decision rows</b>
              <p>One pre-action state for every normal pick and ban.</p>
            </article>
            <span class="flow-arrow" aria-hidden="true">→</span>
            <article class="system-card model-card">
              <span>LEARN</span>
              <b>Team-aware model</b>
              <p>Shared draft patterns plus acting-team and opponent embeddings.</p>
            </article>
            <span class="flow-arrow" aria-hidden="true">→</span>
            <article class="system-card">
              <span>SCORE</span>
              <b>Legal probability list</b>
              <p>Every available hero receives one normalized probability.</p>
            </article>
            <span class="flow-arrow" aria-hidden="true">→</span>
            <article class="system-card output-card">
              <span>USE</span>
              <b>Simulator + coach</b>
              <p>Run rollouts or turn structured evidence into a concise answer.</p>
            </article>
          </div>
        </section>

        <section id="training" class="guide-section">
          <div class="section-heading split-heading">
            <div>
              <span>02 · Training data</span>
              <h2>Every action becomes a choice problem.</h2>
            </div>
            <p>
              Training does not ask “who won this draft?” It asks: given the
              board, teams, draft moment, and legal pool, which hero was actually
              selected?
            </p>
          </div>

          <div class="training-grid">
            <article class="decision-record">
              <div class="record-head">
                <span>ONE TRAINING ROW</span>
                <strong>before_action.json</strong>
              </div>
              <dl>
                <div><dt>action</dt><dd>pick</dd></div>
                <div><dt>side / slot</dt><dd>blue · 2</dd></div>
                <div><dt>acting team</dt><dd>Wolves</dd></div>
                <div><dt>opponent</dt><dd>AG</dd></div>
                <div>
                  <dt>visible board</dt>
                  <dd class="hero-strip compact-strip" data-i18n-ignore>
                    <img :src="heroAsset(exampleHeroes.lubanMaster.id)" :alt="exampleHeroes.lubanMaster.name" />
                    <img :src="heroAsset(exampleHeroes.gongsunLi.id)" :alt="exampleHeroes.gongsunLi.name" />
                    <img :src="heroAsset(exampleHeroes.dunshan.id)" :alt="exampleHeroes.dunshan.name" />
                    <img :src="heroAsset(exampleHeroes.goya.id)" :alt="exampleHeroes.goya.name" />
                  </dd>
                </div>
                <div><dt>legal candidate</dt><dd class="hero-name" data-i18n-ignore><img :src="heroAsset(exampleHeroes.zhaoYun.id)" :alt="exampleHeroes.zhaoYun.name" />{{ exampleHeroes.zhaoYun.name }}</dd></div>
                <div class="record-target"><dt>observed target</dt><dd class="hero-name" data-i18n-ignore><img :src="heroAsset(exampleHeroes.zhaoYun.id)" :alt="exampleHeroes.zhaoYun.name" />{{ exampleHeroes.zhaoYun.name }}</dd></div>
              </dl>
            </article>

            <article class="weight-card">
              <div class="card-label">RECENCY WEIGHT</div>
              <h3>Recent seasons speak louder.</h3>
              <p>The target season receives full weight. Each prior season is multiplied by 0.45 again.</p>
              <div class="weight-bars" data-i18n-ignore>
                <div><span>S0</span><i style="--weight: 100%"></i><b>1.000</b></div>
                <div><span>S−1</span><i style="--weight: 45%"></i><b>0.450</b></div>
                <div><span>S−2</span><i style="--weight: 20.25%"></i><b>0.203</b></div>
                <div><span>S−3</span><i style="--weight: 9.11%"></i><b>0.091</b></div>
                <div><span>S−4</span><i style="--weight: 4.10%"></i><b>0.041</b></div>
              </div>
              <div class="outcome-weight">
                <span>Winning pick</span>
                <strong>× 1.5</strong>
                <p>Bans and other picks retain their normal recency weight.</p>
              </div>
            </article>
          </div>
        </section>

        <section id="prediction" class="guide-section">
          <div class="section-heading">
            <span>03 · One prediction</span>
            <h2 class="prediction-title"><span>Build a query.</span><span>Score only legal heroes.</span></h2>
            <p>
              Four learned signals are added into one draft-state query. That
              query is compared with every candidate hero representation.
            </p>
          </div>

          <div class="signal-grid">
            <article><span>01</span><b>Draft moment</b><p>Pick or ban, Blue or Red, and that team’s action slot.</p></article>
            <article><span>02</span><b>Visible board</b><p>Own and opponent picks and bans, represented separately.</p></article>
            <article><span>03</span><b>Acting team</b><p>A learned vector captures the team’s recurring draft preferences.</p></article>
            <article><span>04</span><b>Opponent</b><p>A second vector captures how choices shift into this opponent.</p></article>
          </div>

          <figure class="model-architecture" aria-labelledby="architecture-title">
            <figcaption>
              <span>MODEL ARCHITECTURE</span>
              <strong id="architecture-title">How one legal hero receives a probability.</strong>
            </figcaption>
            <div class="architecture-flow">
              <div class="architecture-inputs">
                <article class="architecture-node context-node">
                  <span>01 · DRAFT CONTEXT</span>
                  <b>Blue pick · slot 2</b>
                </article>
                <article class="architecture-node board-node">
                  <span>02 · VISIBLE BOARD</span>
                  <div class="architecture-board" data-i18n-ignore>
                    <div><small>BLUE PICK</small><img :src="heroAsset(exampleHeroes.lubanMaster.id)" :alt="exampleHeroes.lubanMaster.name" /><b>{{ exampleHeroes.lubanMaster.name }}</b></div>
                    <div><small>RED PICK</small><img :src="heroAsset(exampleHeroes.gongsunLi.id)" :alt="exampleHeroes.gongsunLi.name" /><b>{{ exampleHeroes.gongsunLi.name }}</b></div>
                    <div><small>BANS</small><span><img :src="heroAsset(exampleHeroes.dunshan.id)" :alt="exampleHeroes.dunshan.name" /><img :src="heroAsset(exampleHeroes.goya.id)" :alt="exampleHeroes.goya.name" /></span></div>
                  </div>
                </article>
                <article class="architecture-node team-node">
                  <span>03 · TEAM MATCHUP</span>
                  <b>Wolves <i>vs</i> AG</b>
                </article>
              </div>

              <div class="architecture-arrow" aria-hidden="true">→</div>

              <article class="architecture-node encoder-node">
                <span>STATE ENCODER</span>
                <b>Learned draft-state query <em>q</em></b>
                <p>context + board + acting team + opponent</p>
                <div class="vector-dots" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
              </article>

              <div class="architecture-arrow" aria-hidden="true">→</div>

              <div class="architecture-candidate">
                <article class="architecture-node hero-vector-node">
                  <span>LEGAL CANDIDATE</span>
                  <div class="candidate-profile" data-i18n-ignore>
                    <img :src="heroAsset(exampleHeroes.zhaoYun.id)" :alt="exampleHeroes.zhaoYun.name" />
                    <div><b>{{ exampleHeroes.zhaoYun.name }}</b><small>hero vector <em>h</em></small></div>
                  </div>
                </article>
                <div class="architecture-merge" aria-hidden="true"><span>q · h</span><i>↓</i></div>
                <article class="architecture-node score-node">
                  <span>SCORE + LEGAL MASK</span>
                  <b>Only legal heroes continue</b>
                  <p>softmax over the remaining scores</p>
                  <div class="probability-output"><img :src="heroAsset(exampleHeroes.zhaoYun.id)" :alt="exampleHeroes.zhaoYun.name" /><i></i><strong>P(赵云)</strong></div>
                </article>
              </div>
            </div>
            <p class="architecture-note">The model repeats the candidate branch for every legal hero, then normalizes those scores into one probability list.</p>
          </figure>

          <div class="prediction-workbench">
            <article class="equation-card" data-i18n-ignore>
              <div class="example-board-head">
                <span>EXAMPLE · BLUE PICK 2</span>
                <div class="hero-strip">
                  <img :src="heroAsset(exampleHeroes.lubanMaster.id)" :alt="exampleHeroes.lubanMaster.name" title="鲁班大师 · Blue pick" />
                  <img :src="heroAsset(exampleHeroes.gongsunLi.id)" :alt="exampleHeroes.gongsunLi.name" title="公孙离 · Red pick" />
                  <img :src="heroAsset(exampleHeroes.dunshan.id)" :alt="exampleHeroes.dunshan.name" title="盾山 · Blue ban" />
                  <img :src="heroAsset(exampleHeroes.goya.id)" :alt="exampleHeroes.goya.name" title="戈娅 · Red ban" />
                  <i>→</i>
                  <img class="candidate-hero" :src="heroAsset(exampleHeroes.zhaoYun.id)" :alt="exampleHeroes.zhaoYun.name" title="赵云 · legal candidate" />
                </div>
              </div>
              <code>query = context + board + acting_team + opponent</code>
              <code>score(赵云) = hero_vector · query + hero_bias</code>
              <code>P(赵云) = softmax(legal_scores)</code>
              <p>Σ probability(legal heroes) = 100%</p>
            </article>
            <article class="legal-gate">
              <span class="card-label">LEGAL GATE</span>
              <h3>Filtering happens before softmax.</h3>
              <div class="availability-example">
                <span>Already on board</span>
                <img :src="heroAsset(exampleHeroes.lubanMaster.id)" :alt="exampleHeroes.lubanMaster.name" />
                <img :src="heroAsset(exampleHeroes.goya.id)" :alt="exampleHeroes.goya.name" />
                <i>×</i>
                <span>Legal candidate</span>
                <img class="available" :src="heroAsset(exampleHeroes.zhaoYun.id)" :alt="exampleHeroes.zhaoYun.name" />
                <i class="check">✓</i>
              </div>
              <ul>
                <li>Already picked or banned this game</li>
                <li>Used earlier by this team under Global BP</li>
                <li>Breaks distinct-role feasibility for a pick</li>
                <li>Outside a supplied custom legal pool</li>
              </ul>
              <p>An illegal hero receives no probability—not a very small one.</p>
            </article>
          </div>
        </section>

        <section id="team-aware" class="guide-section team-section">
          <div class="section-heading split-heading">
            <div>
              <span>04 · Team awareness</span>
              <h2>Same board. Different matchup. Different distribution.</h2>
            </div>
            <p>
              Team identity is inside the learnable model. It is not a
              post-processing multiplier applied after prediction.
            </p>
          </div>

          <div class="team-probability-example">
            <header class="team-probability-head">
              <div>
                <span>ILLUSTRATIVE TEAM OUTPUTS</span>
                <b>Same league. Different team. Different BP probability.</b>
              </div>
              <div class="shared-board" data-i18n-ignore>
                <small>SAME EXAMPLE BOARD</small>
                <img :src="heroAsset(exampleHeroes.lubanMaster.id)" :alt="exampleHeroes.lubanMaster.name" />
                <img :src="heroAsset(exampleHeroes.gongsunLi.id)" :alt="exampleHeroes.gongsunLi.name" />
                <img :src="heroAsset(exampleHeroes.dunshan.id)" :alt="exampleHeroes.dunshan.name" />
                <img :src="heroAsset(exampleHeroes.goya.id)" :alt="exampleHeroes.goya.name" />
              </div>
            </header>

            <div class="team-probability-grid">
              <article v-for="example in teamProbabilityExamples" :key="example.team" class="team-probability-card">
                <header>
                  <span>ACTING TEAM</span>
                  <b data-i18n-ignore>{{ example.team }}</b>
                </header>
                <div class="probability-group">
                  <strong>Next pick · Blue slot 2</strong>
                  <div v-for="row in example.pick" :key="`pick-${example.team}-${row.hero.id}`" class="team-probability-row" data-i18n-ignore>
                    <img :src="heroAsset(row.hero.id)" :alt="row.hero.name" />
                    <span>{{ row.hero.name }}</span>
                    <i><em :style="{ width: `${row.probability}%` }"></em></i>
                    <b>{{ row.probability }}%</b>
                  </div>
                </div>
                <div class="probability-group ban-group">
                  <strong>Next ban · Blue slot 1</strong>
                  <div v-for="row in example.ban" :key="`ban-${example.team}-${row.hero.id}`" class="team-probability-row" data-i18n-ignore>
                    <img :src="heroAsset(row.hero.id)" :alt="row.hero.name" />
                    <span>{{ row.hero.name }}</span>
                    <i><em :style="{ width: `${row.probability}%` }"></em></i>
                    <b>{{ row.probability }}%</b>
                  </div>
                </div>
              </article>
            </div>
            <p>Illustrative hard-coded values: the live simulator always calculates its own probabilities from the selected teams and current BP state.</p>
          </div>

          <div class="team-notes">
            <article><strong>Known team</strong><p>Use its trained embedding from the five-season team vocabulary.</p></article>
            <article><strong>Unknown team</strong><p>Fall back to the shared league and draft representation.</p></article>
            <article><strong>No double count</strong><p>The learnable model skips the separate statistical team-tendency adjustment.</p></article>
          </div>
        </section>

        <section id="outputs" class="guide-section">
          <div class="section-heading">
            <span>05 · Simulator & coach</span>
            <h2>One model, two different jobs.</h2>
            <p>
              The simulator consumes probabilities directly. The coach combines
              model output with other registered evidence tools and explains it.
            </p>
          </div>

          <div class="output-grid">
            <article class="output-panel simulator-panel">
              <div class="panel-head"><span>PROBABILITY CONSUMER</span><b>Draft simulator</b></div>
              <ol class="loop-list">
                <li><span>1</span><p><strong>Calculate</strong> the next legal probability list.</p></li>
                <li><span>2</span><p><strong>Sample</strong> one hero using that distribution.</p></li>
                <li><span>3</span><p><strong>Add</strong> the action to the board.</p></li>
                <li><span>4</span><p><strong>Repeat</strong> with a newly calculated distribution.</p></li>
              </ol>
              <div class="loop-mark">↻</div>
            </article>

            <article class="output-panel coach-panel">
              <div class="panel-head"><span>EVIDENCE ORCHESTRATOR</span><b>AI Draft Coach</b></div>
              <div class="coach-flow">
                <div><span>QUESTION</span><p>“What might Wolves pick next?”</p></div>
                <i>↓</i>
                <div><span>ROUTE</span><p>Kimi selects the registered prediction tool.</p></div>
                <i>↓</i>
                <div><span>COMPUTE</span><p>The backend loads the model and returns structured evidence.</p></div>
                <i>↓</i>
                <div><span>EXPLAIN</span><p>Kimi writes a short, human-readable KPL answer.</p></div>
              </div>
            </article>
          </div>

          <div class="responsibility-line">
            <div><span>MODEL</span><strong>Produces probabilities</strong></div>
            <div><span>TOOLS</span><strong>Retrieve evidence</strong></div>
            <div><span>KIMI</span><strong>Routes and explains</strong></div>
          </div>
        </section>

        <section id="rankings" class="guide-section rankings-method-section">
          <div class="section-heading split-heading">
            <div>
              <span>06 · Power rankings</span>
              <h2>Two boards, two questions.</h2>
            </div>
            <p>
              The team board asks who is strongest now. The hero board asks
              which active player has performed best when using one specific
              hero. Both combine results across available competitions while
              giving more influence to recent matches.
            </p>
          </div>

          <div class="ranking-method-grid">
            <article class="ranking-method-card team-ranking-method">
              <header>
                <span>TEAM POWER SCORE</span>
                <strong>Opponent strength + current form</strong>
              </header>
              <ol>
                <li>
                  <b>01</b>
                  <div><strong>Update Elo after every game</strong><p>Every team begins at 1,500. Beating a stronger opponent earns more Elo than beating a weaker one, and losing to a weaker opponent costs more.</p></div>
                </li>
                <li>
                  <b>02</b>
                  <div><strong>Reduce stale evidence</strong><p>Older results gradually receive less influence. Elo also moves back toward 1,500 when a team has been inactive for a long period.</p></div>
                </li>
                <li>
                  <b>03</b>
                  <div><strong>Estimate current-form win rate</strong><p>Recent weighted wins are divided by recent weighted games. Six neutral prior games—three wins and three losses—keep small samples close to 50%.</p></div>
                </li>
              </ol>
              <div class="ranking-equation" data-i18n-ignore>
                <span>{{ language === "zh-CN" ? "战队实力分" : "TEAM POWER" }}</span>
                <strong>{{ language === "zh-CN" ? "72% Elo 强度 + 28% 近期胜率" : "72% Elo strength + 28% current-form win rate" }}</strong>
              </div>
            </article>

            <article class="ranking-method-card player-ranking-method">
              <header>
                <span>PLAYER–HERO SCORE</span>
                <strong>Performance in the selected hero</strong>
              </header>
              <ol>
                <li>
                  <b>01</b>
                  <div><strong>Compare players in the same role</strong><p>Each game is compared with players in the same competition and position, so support and carry statistics are not judged on the same raw scale.</p></div>
                </li>
                <li>
                  <b>02</b>
                  <div><strong>Build one game-performance score</strong><p>KDA contributes 40%, official MVP score 18%, participation 12%, hero-damage share 10%, gold pace 8%, and whether the player won 12%.</p></div>
                </li>
                <li>
                  <b>03</b>
                  <div><strong>Protect against tiny samples</strong><p>Recent games receive more influence. Four neutral games at a score of 50 are added before ranking, preventing one exceptional appearance from leading the board.</p></div>
                </li>
              </ol>
              <div class="ranking-equation" data-i18n-ignore>
                <span>{{ language === "zh-CN" ? "选手英雄综合分" : "PLAYER–HERO SCORE" }}</span>
                <strong>{{ language === "zh-CN" ? "（加权表现 + 4 × 50）÷（有效局数 + 4）" : "(weighted performance + 4 × 50) ÷ (effective games + 4)" }}</strong>
              </div>
            </article>
          </div>

          <div class="ranking-reading-guide">
            <span>HOW TO READ THE BOARDS</span>
            <div>
              <p><strong>Power score is comparative.</strong> It is designed for ordering teams or players inside the available evidence, not as a prediction that the top entry will win every next match.</p>
              <p><strong>Evidence still matters.</strong> Current-season games, effective games, and confidence show how much support sits behind a score. Treat close scores and small samples as approximately even.</p>
            </div>
          </div>
        </section>

      </div>
    </div>
  </main>
</template>

<style scoped>
.method-page { width:min(1500px, calc(100% - 2rem)); margin:0 auto; padding:2rem 0 6rem; color:var(--ink); }
.method-hero { display:grid; grid-template-columns:minmax(0, 1.2fr) minmax(390px, .7fr); gap:clamp(2rem, 7vw, 7rem); align-items:end; min-height:520px; padding:4.5rem clamp(1rem, 4vw, 4rem); overflow:hidden; border:1px solid rgba(16,42,46,.16); border-radius:1.2rem; background:radial-gradient(circle at 76% 18%, rgba(255,209,109,.26), transparent 30%), linear-gradient(135deg, rgba(255,255,255,.92), rgba(224,239,231,.9)); box-shadow:0 2rem 5rem rgba(16,42,46,.08); position:relative; }
.method-hero::before { content:""; position:absolute; width:430px; height:430px; right:-170px; bottom:-210px; border:1px solid rgba(15,138,107,.24); border-radius:50%; box-shadow:0 0 0 55px rgba(15,138,107,.035), 0 0 0 110px rgba(15,138,107,.025); }
.hero-copy, .model-terminal { position:relative; z-index:1; }
.eyebrow, .section-heading > span, .section-heading > div > span, .card-label { color:var(--accent-deep); font-size:.66rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
.eyebrow b { color:inherit; font:inherit; }
.hero-copy h1 { max-width:760px; margin:.8rem 0 0; font:800 clamp(4rem, 8vw, 7.8rem)/.84 var(--display); letter-spacing:-.075em; }
.hero-copy h1 span { color:var(--accent); }
.hero-intro { max-width:610px; margin:1.6rem 0 0; color:var(--ink-soft); font:500 clamp(.9rem, 1.4vw, 1.1rem)/1.65 var(--display); }
.hero-actions { display:flex; flex-wrap:wrap; gap:.7rem; margin-top:2rem; }
.hero-actions a { display:inline-flex; align-items:center; min-height:44px; padding:.75rem 1rem; border:1px solid var(--ink); border-radius:.35rem; font-size:.7rem; font-weight:700; text-decoration:none; }
.primary-action { background:var(--ink); color:#fff; }.secondary-action { color:var(--ink); background:rgba(255,255,255,.55); }
.hero-actions a:hover { transform:translateY(-1px); box-shadow:0 .55rem 1rem rgba(16,42,46,.1); }
.model-terminal { overflow:hidden; border:1px solid rgba(255,255,255,.16); border-radius:.8rem; background:#102a2e; color:#fff; box-shadow:0 1.5rem 3rem rgba(16,42,46,.24); }
.terminal-head { display:flex; align-items:center; gap:.45rem; min-height:44px; padding:0 1rem; border-bottom:1px solid rgba(255,255,255,.12); color:rgba(255,255,255,.58); font-size:.6rem; letter-spacing:.1em; text-transform:uppercase; }
.terminal-head > span { flex:1; }.terminal-head i { width:.48rem; height:.48rem; border-radius:50%; background:#c45c26; }.terminal-head i.ready { background:#62d49c; box-shadow:0 0 12px rgba(98,212,156,.7); }.terminal-head strong { color:#fff; font-size:.58rem; }
.terminal-body { padding:.9rem 1rem; }.terminal-body p { display:flex; justify-content:space-between; gap:1rem; margin:0; padding:.67rem 0; border-bottom:1px solid rgba(255,255,255,.08); }.terminal-body p:last-child { border:0; }.terminal-body span, .terminal-foot span { color:rgba(255,255,255,.46); font-size:.55rem; letter-spacing:.09em; }.terminal-body strong { color:#fff; font-size:.72rem; text-align:right; }
.terminal-foot { display:grid; gap:.28rem; padding:.9rem 1rem; background:rgba(98,212,156,.1); }.terminal-foot strong { color:#8fe0c8; font:700 .75rem var(--display); }
.truth-strip { display:grid; grid-template-columns:repeat(4, 1fr); margin:1rem 0 0; border:1px solid var(--line); border-radius:.75rem; background:rgba(255,255,255,.62); }
.truth-strip article { min-height:150px; padding:1.15rem; border-right:1px solid var(--line); }.truth-strip article:last-child { border:0; }.truth-strip span { display:block; color:var(--accent); font-size:.58rem; letter-spacing:.1em; }.truth-strip strong { display:block; margin:.8rem 0 .35rem; font:700 1rem var(--display); }.truth-strip p { margin:0; color:var(--ink-soft); font-size:.67rem; line-height:1.55; }
.guide-layout { display:grid; grid-template-columns:220px minmax(0, 1fr); gap:clamp(2rem, 5vw, 5.5rem); margin-top:5rem; align-items:start; }
.guide-rail { position:sticky; top:1rem; align-self:start; width:220px; height:max-content; }.rail-status { display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:.45rem .75rem; margin-bottom:1rem; padding:.9rem; border:1px solid var(--line); border-radius:.55rem; background:rgba(255,255,255,.74); box-shadow:0 .65rem 1.8rem rgba(16,42,46,.06); backdrop-filter:blur(14px); }.rail-status > div:first-child { display:grid; gap:.2rem; min-width:0; }.rail-status span { color:var(--ink-soft); font-size:.5rem; font-weight:700; letter-spacing:.11em; text-transform:uppercase; }.rail-status strong { overflow:hidden; font:700 .75rem var(--display); text-overflow:ellipsis; white-space:nowrap; }.rail-status > b { align-self:center; color:var(--accent-deep); font:700 .58rem var(--mono); }.rail-progress { grid-column:1/-1; height:3px; overflow:hidden; border-radius:999px; background:rgba(16,42,46,.1); }.rail-progress i { display:block; height:100%; border-radius:inherit; background:linear-gradient(90deg, var(--accent-deep), #62d49c); transition:width .25s ease; }
.guide-rail > p { margin:0 0 .65rem; color:var(--ink-soft); font-size:.58rem; letter-spacing:.12em; text-transform:uppercase; }.guide-rail nav { display:grid; border-top:1px solid var(--line); }.guide-rail nav a { position:relative; display:grid; grid-template-columns:2rem 1fr; gap:.4rem; padding:.72rem 0; border-bottom:1px solid var(--line); color:var(--ink-soft); font:600 .68rem var(--display); text-decoration:none; transition:padding .18s ease, color .18s ease, background .18s ease; }.guide-rail nav a::before { content:""; position:absolute; top:.45rem; bottom:.45rem; left:0; width:2px; border-radius:999px; background:var(--accent); transform:scaleY(0); transition:transform .18s ease; }.guide-rail nav a span { color:var(--accent); font:600 .56rem var(--mono); }.guide-rail nav a:hover { color:var(--ink); padding-left:.25rem; }.guide-rail nav a.active { padding-left:.55rem; color:var(--ink); background:linear-gradient(90deg, rgba(15,138,107,.09), transparent); }.guide-rail nav a.active::before { transform:scaleY(1); }.guide-rail nav a.active span { color:var(--accent-deep); }
.rail-note { margin-top:1rem; padding:.9rem; border-radius:.5rem; background:var(--ink); }.rail-note span { color:#8fe0c8; font-size:.55rem; letter-spacing:.1em; text-transform:uppercase; }.rail-note p { margin:.55rem 0 0; color:rgba(255,255,255,.72); font-size:.65rem; line-height:1.55; }
.guide-content { min-width:0; }.guide-section { scroll-margin-top:1rem; padding:0 0 6rem; margin-bottom:6rem; border-bottom:1px solid var(--line); }.guide-section:last-child { margin-bottom:0; }
.section-heading { max-width:880px; margin-bottom:2rem; }.section-heading h2 { max-width:850px; margin:.55rem 0 .8rem; font:800 clamp(2.4rem, 5vw, 4.7rem)/.95 var(--display); letter-spacing:-.06em; }.section-heading > p, .split-heading > p { max-width:720px; margin:0; color:var(--ink-soft); font:500 .84rem/1.7 var(--display); }.prediction-title span { display:block; }
.split-heading { display:grid; grid-template-columns:minmax(0, 1.1fr) minmax(280px, .7fr); gap:2rem; max-width:none; align-items:end; }.split-heading h2 { margin-bottom:0; }.split-heading > p { padding-bottom:.25rem; }
.system-map { display:grid; grid-template-columns:repeat(4, minmax(120px, 1fr) 28px) minmax(120px, 1fr); align-items:stretch; }
.system-card { min-height:190px; padding:1rem; border:1px solid var(--line); border-radius:.55rem; background:rgba(255,255,255,.65); }.system-card span { color:var(--accent); font-size:.54rem; letter-spacing:.12em; }.system-card b { display:block; margin:1.5rem 0 .55rem; font:700 .9rem/1.15 var(--display); }.system-card p { margin:0; color:var(--ink-soft); font-size:.62rem; line-height:1.55; }.source-card { background:rgba(255,255,255,.85); }.model-card { border-color:var(--accent); background:rgba(15,138,107,.09); }.output-card { border-color:var(--ink); background:var(--ink); color:#fff; }.output-card b { color:#fff; }.output-card p { color:rgba(255,255,255,.66); }.output-card span { color:#8fe0c8; }.flow-arrow { display:grid; place-items:center; color:var(--accent); }
.plain-callout { display:grid; grid-template-columns:180px 1fr; gap:1.2rem; margin-top:1rem; padding:1.1rem; border-left:3px solid #e8bf6c; background:rgba(232,191,108,.13); }.plain-callout span { color:var(--ink); font:700 .72rem var(--display); }.plain-callout p { margin:0; color:var(--ink-soft); font-size:.68rem; line-height:1.55; }
.training-grid, .prediction-workbench, .output-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:1rem; }
.decision-record, .weight-card, .legal-gate, .output-panel { border:1px solid var(--line); border-radius:.7rem; background:rgba(255,255,255,.68); overflow:hidden; }
.record-head, .panel-head { display:flex; justify-content:space-between; gap:1rem; padding:.85rem 1rem; border-bottom:1px solid var(--line); background:rgba(16,42,46,.035); }.record-head span, .panel-head span { color:var(--accent-deep); font-size:.55rem; letter-spacing:.1em; }.record-head strong { font-size:.62rem; }.decision-record dl { margin:0; padding:.65rem 1rem 1rem; }.decision-record dl div { display:grid; grid-template-columns:1fr 1.2fr; padding:.58rem 0; border-bottom:1px solid var(--line); }.decision-record dt { color:var(--ink-soft); font-size:.62rem; }.decision-record dd { margin:0; color:var(--ink); font:600 .68rem var(--mono); text-align:right; }.hero-strip { display:flex; align-items:center; justify-content:flex-end; gap:.3rem; }.hero-strip img, .hero-name img, .availability-example img { width:2rem; height:2rem; border:1px solid rgba(255,255,255,.2); border-radius:.28rem; object-fit:cover; background:#102a2e; }.hero-strip.compact-strip { gap:.2rem; }.hero-strip.compact-strip img { width:1.55rem; height:1.55rem; }.hero-strip .candidate-hero, .availability-example img.available { border-color:#62d49c; box-shadow:0 0 0 2px rgba(98,212,156,.2); }.hero-strip i { color:#ffd16d; font:700 .8rem var(--mono); font-style:normal; }.hero-name { display:inline-flex; align-items:center; justify-content:flex-end; gap:.4rem; }.hero-name img { width:1.5rem; height:1.5rem; border-color:var(--line); }.decision-record .record-target { margin-top:.55rem; padding:.72rem; border:0; border-radius:.35rem; background:rgba(15,138,107,.09); }.record-target dd { color:var(--accent-deep); }
.weight-card { padding:1rem; }.weight-card h3, .legal-gate h3 { margin:.55rem 0 .4rem; font:700 1.45rem/1.1 var(--display); letter-spacing:-.035em; }.weight-card > p, .legal-gate > p { margin:0; color:var(--ink-soft); font-size:.67rem; line-height:1.55; }.weight-bars { display:grid; gap:.5rem; margin:1.4rem 0; }.weight-bars div { display:grid; grid-template-columns:2.1rem 1fr 3rem; gap:.5rem; align-items:center; }.weight-bars span, .weight-bars b { font-size:.58rem; }.weight-bars b { text-align:right; }.weight-bars i { display:block; width:var(--weight); height:.46rem; border-radius:999px; background:linear-gradient(90deg, var(--accent-deep), #62d49c); }.outcome-weight { display:grid; grid-template-columns:1fr auto; align-items:end; gap:.2rem 1rem; padding:.9rem; border-radius:.45rem; background:var(--ink); color:#fff; }.outcome-weight span { color:rgba(255,255,255,.56); font-size:.58rem; text-transform:uppercase; }.outcome-weight strong { color:#ffd16d; font:800 1.7rem var(--display); }.outcome-weight p { grid-column:1/-1; margin:.25rem 0 0; color:rgba(255,255,255,.66); font-size:.6rem; }
.signal-grid { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:.65rem; margin-bottom:1rem; }.signal-grid article { min-height:150px; padding:.9rem; border-top:2px solid var(--accent); background:rgba(255,255,255,.62); }.signal-grid span { color:var(--accent); font-size:.56rem; }.signal-grid b { display:block; margin:1rem 0 .4rem; font:700 .85rem var(--display); }.signal-grid p { margin:0; color:var(--ink-soft); font-size:.62rem; line-height:1.5; }
.model-architecture { margin:1rem 0; padding:1.1rem; border:1px solid var(--line); border-radius:.75rem; background:rgba(255,255,255,.72); }.model-architecture figcaption { display:grid; gap:.25rem; margin-bottom:1rem; }.model-architecture figcaption span, .architecture-node > span { color:var(--accent-deep); font-size:.54rem; font-weight:700; letter-spacing:.11em; }.model-architecture figcaption strong { font:700 1.1rem var(--display); }.architecture-flow { display:grid; grid-template-columns:minmax(220px, 1.2fr) 28px minmax(180px, .9fr) 28px minmax(220px, 1.1fr); gap:.55rem; align-items:center; }.architecture-inputs { display:grid; gap:.45rem; }.architecture-node { border:1px solid var(--line); border-radius:.45rem; background:rgba(255,255,255,.86); }.architecture-node > span { display:block; }.context-node, .team-node { display:grid; grid-template-columns:1fr auto; align-items:center; gap:.5rem; min-height:44px; padding:.65rem .75rem; }.context-node b, .team-node b { font:700 .68rem var(--display); }.team-node i { color:var(--accent-deep); font-style:normal; }.board-node { padding:.65rem .75rem; }.architecture-board { display:grid; grid-template-columns:repeat(3, 1fr); gap:.45rem; margin-top:.5rem; }.architecture-board > div { display:grid; grid-template-columns:auto minmax(0, 1fr); gap:.25rem .35rem; align-items:center; min-width:0; }.architecture-board small { grid-column:1/-1; color:var(--ink-soft); font-size:.45rem; letter-spacing:.06em; }.architecture-board img, .candidate-profile img, .probability-output img { width:1.75rem; height:1.75rem; border-radius:.25rem; object-fit:cover; }.architecture-board b { overflow:hidden; font:600 .53rem var(--display); text-overflow:ellipsis; white-space:nowrap; }.architecture-board > div:last-child span { display:flex; gap:.15rem; }.architecture-board > div:last-child img { width:1.2rem; height:1.2rem; }.architecture-arrow { color:var(--accent-deep); font:700 1.35rem var(--mono); text-align:center; }.encoder-node { display:grid; align-content:center; gap:.65rem; min-height:188px; padding:1rem; border-color:var(--accent); background:rgba(15,138,107,.08); }.encoder-node b, .score-node b { font:700 .82rem/1.25 var(--display); }.encoder-node em, .candidate-profile em { color:var(--accent-deep); font:700 1.1rem var(--mono); font-style:normal; }.encoder-node p, .score-node p { margin:0; color:var(--ink-soft); font-size:.58rem; line-height:1.45; }.vector-dots { display:flex; gap:.25rem; }.vector-dots i { display:block; width:.45rem; height:.45rem; border-radius:50%; background:var(--accent); }.vector-dots i:nth-child(2n) { opacity:.5; }.vector-dots i:nth-child(3n) { opacity:.22; }.architecture-candidate { display:grid; gap:.45rem; }.hero-vector-node { padding:.75rem; }.candidate-profile { display:flex; align-items:center; gap:.55rem; margin-top:.5rem; }.candidate-profile img { width:2.4rem; height:2.4rem; box-shadow:0 0 0 2px rgba(15,138,107,.18); }.candidate-profile div { display:grid; gap:.15rem; }.candidate-profile b { font:700 .8rem var(--display); }.candidate-profile small { color:var(--ink-soft); font:.55rem var(--mono); }.architecture-merge { display:grid; justify-items:center; gap:.1rem; color:var(--accent-deep); }.architecture-merge span { padding:.12rem .35rem; border-radius:999px; background:rgba(15,138,107,.1); font:700 .58rem var(--mono); }.architecture-merge i { font-style:normal; }.score-node { display:grid; gap:.4rem; padding:.75rem; border-color:var(--ink); background:#102a2e; color:#fff; }.score-node > span { color:#8fe0c8; }.score-node p { color:rgba(255,255,255,.6); }.probability-output { display:grid; grid-template-columns:1.5rem 1fr auto; gap:.4rem; align-items:center; margin-top:.15rem; }.probability-output img { width:1.5rem; height:1.5rem; }.probability-output i { display:block; height:.36rem; border-radius:999px; background:linear-gradient(90deg, #62d49c 72%, rgba(255,255,255,.16) 72%); }.probability-output strong { color:#ffd16d; font:700 .6rem var(--mono); }.architecture-note { margin:.85rem 0 0; color:var(--ink-soft); font-size:.6rem; line-height:1.5; }
.equation-card { display:grid; align-content:center; gap:.8rem; min-height:330px; padding:1.3rem; border-radius:.7rem; background:#102a2e; box-shadow:inset 0 0 0 1px rgba(255,255,255,.08); }.equation-card span { color:#8fe0c8; font-size:.57rem; letter-spacing:.1em; }.example-board-head { display:grid; gap:.5rem; }.example-board-head .hero-strip { justify-content:flex-start; }.equation-card code { display:block; padding:.85rem; border:1px solid rgba(255,255,255,.1); border-radius:.3rem; color:#fff; background:rgba(255,255,255,.04); font-size:.68rem; white-space:normal; }.equation-card p { margin:0; color:#ffd16d; font-size:.64rem; }.legal-gate { padding:1.2rem; }.availability-example { display:flex; flex-wrap:wrap; align-items:center; gap:.35rem; margin-top:1rem; padding:.65rem; border:1px solid var(--line); border-radius:.4rem; background:rgba(16,42,46,.035); }.availability-example span { color:var(--ink-soft); font-size:.53rem; letter-spacing:.06em; }.availability-example img { width:1.75rem; height:1.75rem; border-color:var(--line); }.availability-example i { color:var(--warn); font:800 .85rem var(--mono); font-style:normal; }.availability-example i.check { color:var(--accent-deep); }.legal-gate ul { margin:1.2rem 0; padding:0; list-style:none; }.legal-gate li { position:relative; padding:.62rem 0 .62rem 1.5rem; border-bottom:1px solid var(--line); color:var(--ink-soft); font:500 .68rem/1.45 var(--display); }.legal-gate li::before { content:"×"; position:absolute; left:0; color:var(--warn); font:700 .8rem var(--mono); }.legal-gate > p { padding:.75rem; background:rgba(196,92,38,.07); color:var(--warn); }
.matchup-visual { padding:1.3rem; border:1px solid var(--line); border-radius:.8rem; background:rgba(255,255,255,.64); }.matchup-row { display:grid; grid-template-columns:minmax(130px, .75fr) 24px minmax(130px, .75fr) minmax(40px, 1fr) minmax(150px, .9fr); gap:.6rem; align-items:center; }.team-pill, .query-pill { display:grid; gap:.22rem; padding:.8rem; border:1px solid var(--line); border-radius:.45rem; background:#fff; }.team-pill span, .query-pill span { color:var(--ink-soft); font-size:.5rem; letter-spacing:.1em; }.team-pill b, .query-pill b { font:700 .82rem var(--display); }.opponent-pill { background:rgba(232,191,108,.12); }.query-pill { border-color:var(--accent); background:rgba(15,138,107,.08); }.query-pill span { color:var(--accent-deep); }.plus { color:var(--ink-soft); text-align:center; }.route-line { height:1px; background:linear-gradient(90deg, var(--line), var(--accent)); }.same-board { display:grid; grid-template-columns:1fr auto 1fr; gap:.7rem; align-items:center; margin:.8rem 0; color:var(--ink-soft); font-size:.52rem; letter-spacing:.1em; }.same-board > div { display:grid; justify-items:center; gap:.35rem; }.same-board .hero-strip { justify-content:center; }.same-board span { height:1px; background:var(--line); }
.team-probability-example { overflow:hidden; border:1px solid var(--line); border-radius:.8rem; background:rgba(255,255,255,.7); }.team-probability-head { display:flex; align-items:center; justify-content:space-between; gap:1rem; padding:1rem 1.15rem; border-bottom:1px solid var(--line); background:linear-gradient(90deg, rgba(15,138,107,.08), rgba(255,255,255,.4)); }.team-probability-head > div:first-child { display:grid; gap:.28rem; }.team-probability-head span, .team-probability-card header span { color:var(--accent-deep); font-size:.54rem; font-weight:700; letter-spacing:.11em; }.team-probability-head b { font:700 1rem var(--display); }.shared-board { display:flex; align-items:center; gap:.25rem; }.shared-board small { margin-right:.25rem; color:var(--ink-soft); font-size:.48rem; letter-spacing:.06em; }.shared-board img { width:1.7rem; height:1.7rem; border-radius:.25rem; object-fit:cover; }.team-probability-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:1px; background:var(--line); }.team-probability-card { padding:1rem 1.15rem 1.1rem; background:rgba(255,255,255,.78); }.team-probability-card header { display:flex; align-items:end; justify-content:space-between; gap:.5rem; padding-bottom:.65rem; border-bottom:1px solid var(--line); }.team-probability-card header b { font:800 1.25rem var(--display); }.probability-group { display:grid; gap:.42rem; margin-top:.85rem; }.probability-group + .probability-group { margin-top:1rem; padding-top:.85rem; border-top:1px solid var(--line); }.probability-group > strong { color:var(--ink-soft); font-size:.56rem; letter-spacing:.05em; }.team-probability-row { display:grid; grid-template-columns:1.55rem minmax(3.5rem, .8fr) minmax(3rem, 1.4fr) 2.1rem; gap:.38rem; align-items:center; }.team-probability-row img { width:1.55rem; height:1.55rem; border-radius:.22rem; object-fit:cover; }.team-probability-row span { overflow:hidden; color:var(--ink); font:600 .6rem var(--display); text-overflow:ellipsis; white-space:nowrap; }.team-probability-row > i { display:block; height:.35rem; overflow:hidden; border-radius:999px; background:rgba(16,42,46,.1); }.team-probability-row > i em { display:block; height:100%; border-radius:inherit; background:linear-gradient(90deg, var(--accent-deep), #62d49c); }.ban-group .team-probability-row > i em { background:linear-gradient(90deg, #c45c26, #e8bf6c); }.team-probability-row b { color:var(--ink-soft); font:700 .58rem var(--mono); text-align:right; }.team-probability-example > p { margin:0; padding:.7rem 1.15rem; border-top:1px solid var(--line); color:var(--ink-soft); font-size:.57rem; line-height:1.45; }
.team-notes { display:grid; grid-template-columns:repeat(3, 1fr); gap:.65rem; margin-top:.7rem; }.team-notes article { padding:.9rem; border-left:2px solid var(--accent); background:rgba(255,255,255,.55); }.team-notes strong { font:700 .75rem var(--display); }.team-notes p { margin:.35rem 0 0; color:var(--ink-soft); font-size:.61rem; line-height:1.5; }
.output-panel { position:relative; min-height:410px; }.panel-head { display:grid; }.panel-head b { margin-top:.3rem; font:700 1.25rem var(--display); }.loop-list { display:grid; gap:0; margin:0; padding:1rem; list-style:none; }.loop-list li { display:grid; grid-template-columns:2rem 1fr; align-items:center; gap:.6rem; padding:.72rem 0; border-bottom:1px solid var(--line); }.loop-list li > span { display:grid; place-items:center; width:1.7rem; height:1.7rem; border-radius:50%; background:rgba(15,138,107,.1); color:var(--accent-deep); font-size:.58rem; }.loop-list p { margin:0; color:var(--ink-soft); font-size:.66rem; }.loop-list strong { color:var(--ink); }.loop-mark { position:absolute; right:1rem; bottom:.7rem; color:rgba(15,138,107,.18); font:800 5rem var(--display); }
.coach-panel { background:#102a2e; }.coach-panel .panel-head { border-color:rgba(255,255,255,.12); background:rgba(255,255,255,.04); }.coach-panel .panel-head span { color:#8fe0c8; }.coach-panel .panel-head b { color:#fff; }.coach-flow { display:grid; grid-template-columns:1fr 24px 1fr 24px 1fr 24px 1fr; gap:.4rem; align-items:center; padding:1rem; }.coach-flow div { min-height:170px; padding:.75rem; border:1px solid rgba(255,255,255,.1); border-radius:.4rem; background:rgba(255,255,255,.035); }.coach-flow span { color:#8fe0c8; font-size:.5rem; letter-spacing:.1em; }.coach-flow p { margin:2rem 0 0; color:rgba(255,255,255,.72); font:500 .65rem/1.55 var(--display); }.coach-flow i { color:#ffd16d; font-style:normal; text-align:center; transform:rotate(-90deg); }
.responsibility-line { display:grid; grid-template-columns:repeat(3, 1fr); margin-top:1rem; border:1px solid var(--line); }.responsibility-line div { padding:.85rem; border-right:1px solid var(--line); }.responsibility-line div:last-child { border:0; }.responsibility-line span { display:block; color:var(--accent); font-size:.52rem; letter-spacing:.1em; }.responsibility-line strong { display:block; margin-top:.3rem; font:700 .72rem var(--display); }
.ranking-method-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.8rem; }.ranking-method-card { overflow:hidden; border:1px solid var(--line); border-radius:.8rem; background:rgba(255,255,255,.7); }.ranking-method-card header { display:grid; gap:.35rem; padding:1.15rem; border-bottom:1px solid var(--line); }.ranking-method-card header span,.ranking-reading-guide>span { color:var(--accent-deep); font-size:.55rem; font-weight:700; letter-spacing:.11em; }.ranking-method-card header strong { font:700 1.2rem var(--display); }.ranking-method-card ol { margin:0; padding:0 1.15rem; list-style:none; }.ranking-method-card li { display:grid; grid-template-columns:2rem 1fr; gap:.65rem; padding:1rem 0; border-bottom:1px solid var(--line); }.ranking-method-card li>b { color:var(--accent); font:.7rem var(--mono); }.ranking-method-card li strong { font:700 .78rem var(--display); }.ranking-method-card li p { margin:.3rem 0 0; color:var(--ink-soft); font-size:.65rem; line-height:1.55; }.ranking-equation { display:grid; gap:.35rem; margin:1rem 1.15rem 1.15rem; padding:1rem; border-radius:.45rem; background:#102a2e; color:#fff; }.ranking-equation span { color:#8fe0c8; font-size:.52rem; letter-spacing:.1em; }.ranking-equation strong { font:700 .78rem/1.45 var(--display); }.ranking-reading-guide { display:grid; grid-template-columns:180px 1fr; gap:1.2rem; margin-top:.8rem; padding:1.1rem; border-left:3px solid #e8bf6c; background:rgba(232,191,108,.12); }.ranking-reading-guide>div { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1rem; }.ranking-reading-guide p { margin:0; color:var(--ink-soft); font-size:.68rem; line-height:1.6; }.ranking-reading-guide strong { color:var(--ink); }
.meaning-grid { display:grid; grid-template-columns:repeat(2, 1fr); gap:1rem; }.meaning-grid article { padding:1.2rem; border-radius:.7rem; }.meaning-grid article > span { font-size:.55rem; font-weight:700; letter-spacing:.12em; }.meaning-grid ul { margin:1rem 0 0; padding:0; list-style:none; }.meaning-grid li { position:relative; padding:.7rem 0 .7rem 1.6rem; border-bottom:1px solid rgba(16,42,46,.09); color:var(--ink-soft); font:500 .69rem/1.45 var(--display); }.meaning-grid li::before { position:absolute; left:0; font-weight:700; }.means-yes { background:rgba(15,138,107,.08); }.means-yes > span, .means-yes li::before { color:var(--accent-deep); }.means-yes li::before { content:"✓"; }.means-no { background:rgba(196,92,38,.07); }.means-no > span, .means-no li::before { color:var(--warn); }.means-no li::before { content:"×"; }
.honesty-note { display:grid; grid-template-columns:190px 1fr; gap:1rem; margin-top:1rem; padding:1.1rem; border:1px solid #e8bf6c; border-radius:.55rem; background:rgba(232,191,108,.12); }.honesty-note span { color:#8b641e; font-size:.55rem; font-weight:700; letter-spacing:.1em; }.honesty-note p { margin:0; color:var(--ink-soft); font-size:.68rem; line-height:1.6; }
.closing-link { display:grid; grid-template-columns:1fr auto; gap:.25rem 1rem; margin-top:1rem; padding:1.2rem; border-radius:.6rem; background:var(--ink); color:#fff; text-decoration:none; }.closing-link span { color:rgba(255,255,255,.52); font-size:.57rem; letter-spacing:.1em; text-transform:uppercase; }.closing-link strong { font:700 1.15rem var(--display); }.closing-link b { grid-column:2; grid-row:1/3; align-self:center; color:#8fe0c8; font-size:1.5rem; }.closing-link:hover b { transform:translateX(.25rem); }
@media (max-width:1180px) { .method-hero { grid-template-columns:1fr minmax(330px, .75fr); min-height:460px; padding:3rem 2rem; gap:2rem; }.hero-copy h1 { font-size:clamp(3.7rem, 7vw, 6rem); }.guide-layout { grid-template-columns:170px minmax(0,1fr); gap:2rem; }.guide-rail { width:170px; }.system-map { grid-template-columns:repeat(2, 1fr); gap:.65rem; }.flow-arrow { display:none; }.system-card:last-child { grid-column:1/-1; min-height:150px; }.coach-flow { grid-template-columns:1fr; }.coach-flow div { min-height:auto; }.coach-flow i { transform:none; }.output-panel { min-height:auto; } }
@media (max-width:820px) { .method-page { width:min(100% - 1.2rem, 720px); padding-top:.6rem; }.method-hero { grid-template-columns:1fr; min-height:auto; padding:2.2rem 1.2rem; }.model-terminal { width:100%; }.truth-strip { grid-template-columns:repeat(2, 1fr); }.truth-strip article:nth-child(2) { border-right:0; }.truth-strip article:nth-child(-n+2) { border-bottom:1px solid var(--line); }.guide-layout { display:block; margin-top:3rem; }.guide-rail { position:sticky; z-index:20; top:0; width:auto; max-height:none; margin:0 -.6rem 3rem; padding:.55rem .6rem .65rem; overflow:visible; border-bottom:1px solid var(--line); background:rgba(243,246,244,.92); box-shadow:0 .7rem 1.4rem rgba(16,42,46,.06); backdrop-filter:blur(16px); }.guide-rail > p, .rail-note { display:none; }.rail-status { grid-template-columns:minmax(0, 1fr) auto; margin:0 0 .45rem; padding:0; border:0; background:transparent; box-shadow:none; backdrop-filter:none; }.rail-status span { display:none; }.rail-status strong { font-size:.7rem; }.rail-status > b { font-size:.54rem; }.rail-progress { height:2px; }.guide-rail nav { display:flex; gap:.35rem; overflow-x:auto; border:0; scrollbar-width:none; scroll-snap-type:x proximity; }.guide-rail nav::-webkit-scrollbar { display:none; }.guide-rail nav a { display:flex; flex:0 0 auto; grid-template-columns:none; gap:.3rem; align-items:center; padding:.43rem .58rem; border:1px solid var(--line); border-radius:999px; background:rgba(255,255,255,.76); scroll-snap-align:center; }.guide-rail nav a::before { display:none; }.guide-rail nav a:hover { padding-left:.58rem; }.guide-rail nav a.active { padding-left:.58rem; border-color:var(--ink); background:var(--ink); color:#fff; }.guide-rail nav a.active span { color:#8fe0c8; }.guide-section { scroll-margin-top:7.5rem; padding-bottom:4rem; margin-bottom:4rem; }.split-heading { grid-template-columns:1fr; gap:1rem; }.training-grid, .prediction-workbench, .output-grid { grid-template-columns:1fr; }.signal-grid { grid-template-columns:repeat(2, 1fr); }.matchup-row { grid-template-columns:1fr 18px 1fr; }.matchup-row .route-line { display:none; }.query-pill { grid-column:1/-1; }.same-board { margin:1rem 0; }.team-notes { grid-template-columns:1fr; }.coach-flow { grid-template-columns:1fr; }.responsibility-line { grid-template-columns:1fr; }.responsibility-line div { border-right:0; border-bottom:1px solid var(--line); }.meaning-grid { grid-template-columns:1fr; } }
@media (max-width:520px) { .method-page { width:calc(100% - .8rem); }.method-hero { border-radius:.8rem; }.hero-copy h1 { font-size:3.5rem; }.hero-intro { font-size:.8rem; }.hero-actions { display:grid; }.hero-actions a { justify-content:center; }.terminal-body p { align-items:start; }.truth-strip { grid-template-columns:1fr; }.truth-strip article { min-height:auto; border-right:0; border-bottom:1px solid var(--line); }.truth-strip article:nth-child(3) { border-bottom:1px solid var(--line); }.guide-rail { margin-left:0; margin-right:0; }.section-heading h2 { font-size:2.75rem; }.system-map, .signal-grid { grid-template-columns:1fr; }.system-card:last-child { grid-column:auto; }.plain-callout, .honesty-note { grid-template-columns:1fr; }.matchup-row { grid-template-columns:1fr; }.plus { display:none; }.same-board b { text-align:center; }.decision-record dl div { grid-template-columns:1fr; gap:.2rem; }.decision-record dd { text-align:left; }.coach-flow p { margin-top:.8rem; }.closing-link { grid-template-columns:1fr auto; } }
@media (max-width:1180px) { .architecture-flow { grid-template-columns:minmax(190px, 1fr) 24px minmax(170px, .8fr) 24px minmax(190px, 1fr); gap:.35rem; }.architecture-board { gap:.25rem; }.architecture-board b { display:none; } }
@media (max-width:820px) { .architecture-flow { grid-template-columns:1fr; gap:.6rem; }.architecture-arrow { transform:rotate(90deg); }.architecture-inputs { grid-template-columns:1fr; }.encoder-node { min-height:auto; }.architecture-candidate { grid-template-columns:1fr; }.architecture-board b { display:block; } }
@media (max-width:820px) { .ranking-method-grid { grid-template-columns:1fr; }.ranking-reading-guide { grid-template-columns:1fr; }.ranking-reading-guide>div { grid-template-columns:1fr; } }
@media (max-width:640px) { .team-probability-head { align-items:start; flex-direction:column; }.team-probability-grid { grid-template-columns:1fr; }.team-probability-card { padding:1rem; }.shared-board small { display:none; }.team-probability-example > p { padding:.7rem 1rem; } }
</style>
