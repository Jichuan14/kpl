<script setup>
import { onMounted, ref, watch } from "vue";
import { fetchDraftModel } from "./api";
import { selectedLeagueId } from "./selectedLeague";
import { language } from "./i18n";

const model = ref(null);

async function loadModel() {
  if (!selectedLeagueId.value) return;
  try {
    model.value = await fetchDraftModel(selectedLeagueId.value);
  } catch {
    model.value = null;
  }
}

onMounted(loadModel);
watch(selectedLeagueId, loadModel);
</script>

<template>
  <main class="method-page">
    <header>
      <p class="eyebrow">KPL Lab guide</p>
      <h1>How the draft model works</h1>
      <p class="intro">
        This page explains the data used by the draft simulator, how meta heroes
        are ranked, how hero relationships work, and how the next-action
        probability is calculated.
      </p>
      <p v-if="model" class="data-note">
        The currently selected season model contains
        <strong>{{ Number(model.training_decisions || 0).toLocaleString(language) }}</strong>
        historical ban and pick decisions.
      </p>
    </header>

    <nav class="contents" aria-label="On this page">
      <a href="#summary">Summary</a>
      <a href="#meta">Meta heroes</a>
      <a href="#relations">Four relationships</a>
      <a href="#example">Example</a>
      <a href="#rules">Rules and limits</a>
    </nav>

    <section id="summary">
      <h2>Summary</h2>
      <ol class="steps">
        <li><strong>Start with historical drafts.</strong> Each normal ban or pick from the selected season becomes one example.</li>
        <li><strong>Find the same draft moment.</strong> A Blue first pick, Red second pick, and Blue third ban are separate contexts.</li>
        <li><strong>Remove illegal heroes.</strong> The model only scores heroes that can actually be selected now.</li>
        <li><strong>Adjust for the visible board.</strong> Earlier picks and bans can increase or decrease a candidate’s score.</li>
        <li><strong>Turn scores into percentages.</strong> Every legal hero is normalized into one list that adds up to 100%.</li>
      </ol>
    </section>

    <section id="meta">
      <h2>How meta heroes are ranked</h2>
      <p>
        A meta hero is a hero that receives a lot of early draft attention. This
        page measures early priority, not hero strength, win rate, or whether a
        hero is always correct for every team.
      </p>

      <table>
        <thead><tr><th>Metric</th><th>What it means</th><th>Formula</th></tr></thead>
        <tbody>
          <tr><td>Opening ban rate</td><td>How often the hero is banned in the first four bans.</td><td>opening bans ÷ eligible drafts</td></tr>
          <tr><td>Blue first-pick conversion</td><td>How often Blue takes the hero first when the hero survives the opening bans and is legal.</td><td>Blue first picks ÷ legal Blue first-pick opportunities</td></tr>
          <tr><td>Early priority rate</td><td>How often the hero is either opening-banned or first-picked by Blue.</td><td>(opening bans + Blue first picks) ÷ eligible drafts</td></tr>
        </tbody>
      </table>

      <div class="example-box">
        <h3>Example: 鲁班大师</h3>
        <p>In one season sample, 鲁班大师 was opening-banned 259 times across 306 eligible drafts. He survived and was legal for Blue’s first pick 46 times, and Blue selected him 40 times.</p>
        <ul>
          <li>Opening ban rate: 259 ÷ 306 = <strong>84.6%</strong></li>
          <li>Blue first-pick conversion: 40 ÷ 46 = <strong>87.0%</strong></li>
          <li>Early priority: (259 + 40) ÷ 306 = <strong>97.7%</strong></li>
        </ul>
        <p class="small">The denominators are different because Blue first-pick conversion only looks at drafts where the hero actually survived the opening bans.</p>
      </div>
    </section>

    <section id="relations">
      <h2>The four hero relationships</h2>
      <p>
        For every candidate, the model looks at heroes already visible on the board.
        “Own” and “opponent” are always from the point of view of the team taking
        the next action. Each visible hero is considered separately; the model
        does not treat the entire five-hero lineup as one combined feature.
      </p>

      <table>
        <thead><tr><th>Relationship</th><th>Question the model asks</th><th>What it can reflect</th></tr></thead>
        <tbody>
          <tr><td><code>own_pick</code></td><td>After our team has already picked Hero A, how often does it later choose Hero B?</td><td>Team composition or hero synergy.</td></tr>
          <tr><td><code>opponent_pick</code></td><td>After the opponent has picked Hero A, how often do we choose Hero B?</td><td>A counter, a response, or a denial pick.</td></tr>
          <tr><td><code>own_ban</code></td><td>After our team bans Hero A, how often do we choose Hero B later?</td><td>The plan that tends to follow that ban.</td></tr>
          <tr><td><code>opponent_ban</code></td><td>After the opponent bans Hero A, how often do we choose Hero B?</td><td>How that ban changes the remaining hero pool.</td></tr>
        </tbody>
      </table>

      <p class="small">
        These are historical associations, not proof that one hero directly
        counters or causes another. A relationship can also reflect patches,
        team strategy, or other heroes already on the board.
      </p>
    </section>

    <section id="example">
      <h2>Worked example: scoring one next pick</h2>
      <p>
        Assume Blue is about to make its second pick. 戈娅 is a legal candidate.
        The current board has one example hero from each relationship type:
      </p>

      <table>
        <thead><tr><th>Visible hero</th><th>Relationship to Blue’s next action</th><th>Example historical effect on 戈娅</th></tr></thead>
        <tbody>
          <tr><td>大乔</td><td>Blue already picked her: <code>own_pick</code></td><td>1.60×</td></tr>
          <tr><td>公孙离</td><td>Red already picked her: <code>opponent_pick</code></td><td>0.75×</td></tr>
          <tr><td>盾山</td><td>Blue already banned him: <code>own_ban</code></td><td>1.15×</td></tr>
          <tr><td>鲁班大师</td><td>Red already banned him: <code>opponent_ban</code></td><td>0.90×</td></tr>
        </tbody>
      </table>

      <h3>1. Filter the candidate pool</h3>
      <p>First remove heroes that are already picked or banned in this game. For picks, also remove Blue’s heroes from earlier games under Global BP and heroes that would make it impossible to assign the team’s picks to different positions. 戈娅 remains legal in this example.</p>

      <h3>2. Build a baseline</h3>
      <p>The model looks at the exact context <code>pick | blue | 2</code>. Suppose 戈娅 was selected in 8 out of 100 legal historical opportunities in this context. Her starting score is <strong>8.0%</strong>.</p>

      <h3>3. Apply the four relationship effects</h3>
      <p>The model combines the four effects with the baseline. It gives weaker evidence less influence, so a relationship based on only a few drafts cannot dominate the result. With illustrative evidence weights, the raw score is:</p>
      <pre>8.0% × 1.60^0.70 × 0.75^0.65 × 1.15^0.55 × 0.90^0.50 = 9.45</pre>
      <p>The number 9.45 is a raw score. It is not the final displayed probability yet.</p>

      <h3>4. Normalize every legal hero</h3>
      <p>Every other legal hero is scored the same way. If all legal raw scores add up to 181.7, then 戈娅’s final next-pick chance is 9.45 ÷ 181.7 = <strong>5.2%</strong>. All legal hero chances together always equal 100%.</p>
    </section>

    <section id="rules">
      <h2>Rules, smoothing, and limits</h2>
      <h3>Legal picks come before probability</h3>
      <ul>
        <li>A hero used in the current game cannot be banned or picked again.</li>
        <li>For a pick under Global BP, each side cannot reuse its own picks from previous games.</li>
        <li>A possible pick must still allow the team’s heroes to be assigned to distinct eligible positions.</li>
        <li>If a custom hero pool is provided, the same checks are applied within that pool.</li>
      </ul>

      <h3>How the model avoids overconfidence</h3>
      <ul>
        <li>The exact-context baseline is blended with the hero’s overall pick or ban rate using 12 prior examples.</li>
        <li>A relationship must appear at least two times before it is stored.</li>
        <li>Relationship effects are shrunk toward no effect when there are few opportunities.</li>
        <li>A single relationship is capped between one-third and three times the baseline before evidence weighting.</li>
      </ul>

      <h3>How a full draft simulation works</h3>
      <ol>
        <li>Calculate the next-action probability list.</li>
        <li>Randomly sample one legal hero using that list.</li>
        <li>Add the choice to the board and calculate the next action again.</li>
        <li>Repeat until the draft ends. Many simulated drafts estimate results such as “banned by end.”</li>
      </ol>

      <p class="scope"><strong>What the model does not currently include:</strong> team identity, player form, match score, BO length, patch notes, and win rate are not separate model features. Read a prediction as: “Teams in this historical data tended to make this choice from boards like this.”</p>
    </section>
  </main>
</template>

<style scoped>
.method-page { width: min(900px, calc(100% - 2rem)); margin: 0 auto; padding: 2.5rem 0 5rem; color: var(--ink); }
header { padding-bottom: 2rem; border-bottom: 1px solid var(--line); }
.eyebrow { margin: 0 0 .5rem; color: var(--accent-deep); font-size: .72rem; letter-spacing: .1em; text-transform: uppercase; }
h1, h2, h3 { font-family: var(--display); }
h1 { margin: 0; font-size: clamp(2.6rem, 7vw, 4.5rem); line-height: 1; letter-spacing: -.04em; }
h2 { margin: 0 0 1rem; font-size: clamp(1.8rem, 4vw, 2.6rem); line-height: 1.1; letter-spacing: -.03em; }
h3 { margin: 2rem 0 .5rem; font-size: 1.15rem; }
p, li, td { color: var(--ink-soft); font-family: var(--display); line-height: 1.65; }
.intro { max-width: 700px; margin: 1rem 0 0; font-size: 1.05rem; }
.data-note { margin: 1rem 0 0; font-size: .82rem; }
.data-note strong, strong { color: var(--ink); }
.contents { display: flex; flex-wrap: wrap; gap: .5rem 1rem; padding: 1rem 0; border-bottom: 1px solid var(--line); }
.contents a { color: var(--accent-deep); font-size: .78rem; text-decoration: none; }
.contents a:hover { text-decoration: underline; }
section { scroll-margin-top: 1rem; padding: 2.75rem 0; border-bottom: 1px solid var(--line); }
section > p:first-of-type { max-width: 780px; margin-top: 0; }
.steps { padding-left: 1.25rem; }
.steps li { padding: .35rem 0; }
table { width: 100%; margin: 1.25rem 0; border-collapse: collapse; font-size: .86rem; }
th, td { padding: .8rem; border: 1px solid var(--line); text-align: left; vertical-align: top; }
th { background: rgba(16,42,46,.045); color: var(--ink); font-family: var(--display); font-weight: 700; }
td { color: var(--ink-soft); }
code, pre { font-family: var(--mono); }
code { padding: .12rem .28rem; background: rgba(15,138,107,.08); color: var(--accent-deep); font-size: .82em; }
pre { overflow-x: auto; margin: 1rem 0; padding: 1rem; border: 1px solid var(--line); background: rgba(255,255,255,.55); color: var(--ink); font-size: .82rem; line-height: 1.5; white-space: pre-wrap; }
.example-box { margin-top: 1.5rem; padding: 1rem 1.25rem; border-left: 3px solid var(--accent); background: rgba(15,138,107,.05); }
.example-box h3 { margin-top: 0; }
.example-box ul, section > ul, section > ol { padding-left: 1.25rem; }
.example-box li, section > ul li, section > ol li { padding: .2rem 0; }
.small { font-size: .82rem; }
.scope { margin-top: 2rem; padding: 1rem; background: rgba(16,42,46,.05); font-size: .84rem; }
@media (max-width: 620px) { .method-page { width: calc(100% - 1.25rem); padding-top: 1.5rem; } section { padding: 2rem 0; } table { display: block; overflow-x: auto; } th, td { min-width: 150px; } }
</style>
