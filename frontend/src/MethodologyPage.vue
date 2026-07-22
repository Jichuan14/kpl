<script setup>
const categories = [
  {
    number: "01",
    title: "Ban response",
    question: "After Hero A is banned, what does each team do later?",
    description:
      "The trigger is a ban. We separately record the opponent’s next ban, every later pick by the banning team, and every later pick by the opponent.",
    example:
      "Team A bans 大乔. Team B’s next ban is 公孙离, so this adds one 大乔 → ban 公孙离 selection. If Team B later picks 镜, it also adds one 大乔 → opponent pick 镜 selection.",
    caution:
      "Later picks are separate decision opportunities. This does not claim the first ban caused those choices.",
  },
  {
    number: "02",
    title: "Pick synergy",
    question: "Which heroes are selected after an allied hero is visible?",
    description:
      "For every pick decision, each hero already picked by the acting team becomes a separate source relationship. Picks can be several actions apart. The team-specific report merges A→B and B→A into one unordered pair for each team.",
    example:
      "A team has already picked 鲁班大师 and later picks 黄忠. The draft contributes one 鲁班大师 + 黄忠 selection whenever 黄忠 was legal.",
    caution:
      "A three-hero lineup is represented by multiple pairs, not one combined composition model.",
  },
  {
    number: "03",
    title: "Counter pick",
    question: "What gets picked after an opponent hero is visible?",
    description:
      "Each visible opponent hero is paired separately with the acting team’s selected hero. The target only enters the denominator when it was legal.",
    example:
      "The opponent has 不知火舞 and 关羽 visible when Team B picks 张飞. This creates two observations: vs 不知火舞 → 张飞 and vs 关羽 → 张飞.",
    caution:
      "The result is an association with the visible hero, not proof of a direct tactical counter.",
  },
  {
    number: "04",
    title: "Counter ban",
    question: "What gets banned after an opponent pick is visible?",
    description:
      "During later ban decisions, every visible opponent pick is paired separately with the selected ban.",
    example:
      "The opponent has picked 不知火舞, 朵莉亚, and 敖隐. A later 关羽 ban contributes separately to all three source relationships.",
    caution:
      "The model does not currently condition on the three-hero lineup as one combined input.",
  },
];

const glossary = [
  {
    term: "Legal opportunity",
    definition:
      "One decision where the candidate hero was available under the inferred BP rules.",
  },
  {
    term: "Selection",
    definition:
      "The number of legal opportunities where the candidate was actually picked or banned.",
  },
  {
    term: "Baseline",
    definition:
      "The hero’s normal rate for the same action and context without conditioning on the source hero.",
  },
  {
    term: "Lift",
    definition:
      "The smoothed conditional probability divided by the normal baseline probability.",
  },
  {
    term: "Support",
    definition:
      "How many times the relationship was observed as the selected action.",
  },
  {
    term: "95% likely range",
    definition:
      "A Wilson confidence interval around the raw probability. Wider ranges indicate less evidence.",
  },
];
</script>

<template>
  <main class="method-page">
    <header class="method-hero">
      <p class="method-eyebrow">Methodology · Transparent by design</p>
      <h1>How the numbers work</h1>
      <p>
        Every pattern starts from the draft state immediately before an action.
        The calculations account for hero legality, action order, side, and
        Global BP reuse rules.
      </p>
    </header>

    <section class="principle-grid">
      <article>
        <span>Core principle</span>
        <strong>Only count real choices</strong>
        <p>
          A hero enters a denominator only when the team was actually allowed
          to select it at that decision.
        </p>
      </article>
      <article>
        <span>Unit of analysis</span>
        <strong>One pre-action state</strong>
        <p>
          Every ban and pick becomes a snapshot containing the visible draft,
          acting team, legal pool, and selected hero.
        </p>
      </article>
      <article>
        <span>Interpretation</span>
        <strong>Association, not causation</strong>
        <p>
          Patterns describe what teams historically did in similar states.
          They do not prove one hero caused another choice.
        </p>
      </article>
    </section>

    <section class="method-section">
      <div class="section-intro">
        <p class="method-eyebrow">Step one</p>
        <h2>Infer the legal hero pool</h2>
        <p>
          Before each action, the pipeline reconstructs which heroes were legal
          candidates. Questionable source rows remain in the data and are
          marked as overrides instead of being silently removed.
        </p>
      </div>

      <div class="rule-list">
        <div>
          <span>Unavailable</span>
          <strong>Current battle bans and picks</strong>
          <p>A hero already banned or picked in the battle cannot be chosen.</p>
        </div>
        <div>
          <span>Global BP</span>
          <strong>Acting team’s previous picks</strong>
          <p>
            In standard BP, a team cannot pick a hero it already used earlier
            in the match.
          </p>
        </div>
        <div>
          <span>Still legal</span>
          <strong>Opponent’s previous use</strong>
          <p>
            A hero used earlier only by the opponent remains available to the
            acting team.
          </p>
        </div>
        <div>
          <span>Peak battles</span>
          <strong>Handled separately</strong>
          <p>No-ban drafts are separated from normal BP relationship rows.</p>
        </div>
      </div>
    </section>

    <section class="method-section">
      <div class="section-intro">
        <p class="method-eyebrow">Step two</p>
        <h2>The four BP relationship categories</h2>
        <p>
          Relationships are pairwise: one visible or triggering hero is
          associated with one later pick or ban.
        </p>
      </div>

      <div class="category-list">
        <article v-for="category in categories" :key="category.number">
          <div class="category-number">{{ category.number }}</div>
          <div class="category-copy">
            <p class="method-eyebrow">{{ category.question }}</p>
            <h3>{{ category.title }}</h3>
            <p>{{ category.description }}</p>
            <div class="example">
              <span>Example</span>
              <p>{{ category.example }}</p>
            </div>
            <p class="caution">{{ category.caution }}</p>
          </div>
        </article>
      </div>
    </section>

    <section class="method-section">
      <div class="section-intro">
        <p class="method-eyebrow">Step three</p>
        <h2>Probability, smoothing, and lift</h2>
        <p>
          Raw probability is easy to understand, while smoothing prevents a
          tiny sample from dominating the rankings.
        </p>
      </div>

      <div class="formula-grid">
        <article>
          <span>Raw probability</span>
          <div class="formula">
            selections ÷ legal opportunities
          </div>
          <p>
            If 张飞 was picked 12 times across 40 legal counter-pick
            opportunities, the raw probability is <strong>30%</strong>.
          </p>
        </article>
        <article>
          <span>Smoothed probability</span>
          <div class="formula">
            (selections + 10 × baseline) ÷ (opportunities + 10)
          </div>
          <p>
            With a 15% baseline, the example becomes
            <strong>(12 + 10 × 0.15) ÷ 50 = 27%</strong>.
          </p>
        </article>
        <article>
          <span>Lift</span>
          <div class="formula">
            smoothed probability ÷ baseline
          </div>
          <p>
            The smoothed 27% rate divided by the 15% baseline produces
            <strong>1.80× lift</strong>.
          </p>
        </article>
      </div>

      <div class="context-note">
        <strong>Overall versus slot context</strong>
        <p>
          Overall rows combine all sides and action slots. Slot-context rows
          compare a pattern only with the same responding side and that side’s
          ban or pick number.
        </p>
      </div>
    </section>

    <section class="method-section meta-method">
      <div class="section-intro">
        <p class="method-eyebrow">Opening draft priority</p>
        <h2>How meta heroes are ranked</h2>
        <p>
          The meta list measures which heroes teams prioritize before the
          opening draft develops: BP orders 1–4 plus Blue’s first pick.
        </p>
      </div>

      <div class="meta-formulas">
        <article>
          <span>Opening ban rate</span>
          <div class="formula">
            opening-banned battles ÷ eligible battles
          </div>
        </article>
        <article>
          <span>Blue first-pick conversion</span>
          <div class="formula">
            Blue first picks ÷ legal Blue first-pick opportunities
          </div>
        </article>
        <article>
          <span>Early priority rate</span>
          <div class="formula">
            (opening bans + Blue first picks) ÷ eligible battles
          </div>
        </article>
      </div>

      <div class="worked-example">
        <div>
          <span>Worked example</span>
          <h3>Hero A across 100 eligible battles</h3>
        </div>
        <dl>
          <div><dt>Opening bans</dt><dd>55</dd></div>
          <div><dt>Legal Blue first-pick chances</dt><dd>40</dd></div>
          <div><dt>Blue first picks</dt><dd>15</dd></div>
          <div><dt>Opening ban rate</dt><dd>55%</dd></div>
          <div><dt>Blue conversion</dt><dd>37.5%</dd></div>
          <div class="result"><dt>Early priority</dt><dd>70%</dd></div>
        </dl>
        <p>
          The combined rate uses 55 bans + 15 first picks over 100 eligible
          battles. The Blue conversion uses its own legality-adjusted
          denominator: 15 ÷ 40.
        </p>
      </div>
    </section>

    <section class="method-section">
      <div class="section-intro">
        <p class="method-eyebrow">Reference</p>
        <h2>Metric glossary</h2>
      </div>
      <dl class="glossary">
        <div v-for="item in glossary" :key="item.term">
          <dt>{{ item.term }}</dt>
          <dd>{{ item.definition }}</dd>
        </div>
      </dl>
    </section>
  </main>
</template>

<style scoped>
.method-page {
  width: min(1200px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 2.25rem 0 5rem;
}

.method-hero {
  padding: clamp(2rem, 6vw, 4.5rem);
  border: 1px solid var(--line);
  background:
    radial-gradient(circle at 86% 20%, rgba(15, 138, 107, 0.2), transparent 28%),
    linear-gradient(140deg, #f9fbfa, #e4eee9);
}

.method-eyebrow {
  margin: 0 0 0.5rem;
  color: var(--accent-deep);
  font-size: 0.67rem;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}

.method-hero h1 {
  max-width: 780px;
  margin: 0;
  font: 800 clamp(3rem, 8vw, 6.4rem)/0.88 var(--display);
  letter-spacing: -0.065em;
}

.method-hero > p:last-child {
  max-width: 700px;
  margin: 1.4rem 0 0;
  color: var(--ink-soft);
  font-size: 0.95rem;
  line-height: 1.65;
}

.principle-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1px;
  margin-top: 0.8rem;
  border: 1px solid var(--line);
  background: var(--line);
}

.principle-grid article {
  padding: 1.25rem;
  background: rgba(255, 255, 255, 0.82);
}

.principle-grid span,
.formula-grid span,
.meta-formulas span,
.example span,
.worked-example span {
  color: var(--ink-soft);
  font-size: 0.64rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

.principle-grid strong {
  display: block;
  margin-top: 0.45rem;
  font: 700 1.2rem var(--display);
}

.principle-grid p,
.formula-grid p,
.context-note p {
  margin: 0.55rem 0 0;
  color: var(--ink-soft);
  font-size: 0.73rem;
  line-height: 1.6;
}

.method-section {
  display: grid;
  grid-template-columns: minmax(230px, 0.7fr) minmax(0, 1.8fr);
  gap: 2.5rem;
  margin-top: 0.8rem;
  padding: clamp(1.5rem, 4vw, 2.5rem);
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.8);
}

.section-intro h2 {
  margin: 0;
  font: 700 clamp(1.8rem, 4vw, 2.7rem)/0.98 var(--display);
  letter-spacing: -0.05em;
}

.section-intro > p:last-child {
  margin: 1rem 0 0;
  color: var(--ink-soft);
  font-size: 0.75rem;
  line-height: 1.65;
}

.rule-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  border: 1px solid var(--line);
}

.rule-list div {
  padding: 1rem;
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.rule-list span {
  color: var(--accent-deep);
  font-size: 0.62rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.rule-list strong,
.rule-list p {
  display: block;
}

.rule-list strong {
  margin-top: 0.35rem;
}

.rule-list p {
  margin: 0.35rem 0 0;
  color: var(--ink-soft);
  font-size: 0.7rem;
  line-height: 1.55;
}

.category-list {
  border-top: 1px solid var(--line);
}

.category-list > article {
  display: grid;
  grid-template-columns: 55px minmax(0, 1fr);
  gap: 1rem;
  padding: 1.35rem 0;
  border-bottom: 1px solid var(--line);
}

.category-number {
  color: var(--accent);
  font: 700 1.5rem var(--display);
}

.category-copy h3 {
  margin: 0;
  font: 700 1.4rem var(--display);
}

.category-copy > p:not(.method-eyebrow, .caution) {
  margin: 0.65rem 0 0;
  color: var(--ink-soft);
  line-height: 1.6;
}

.example {
  margin-top: 0.85rem;
  padding: 0.8rem;
  border-left: 3px solid var(--accent);
  background: rgba(15, 138, 107, 0.06);
}

.example p {
  margin: 0.3rem 0 0;
  font-size: 0.74rem;
  line-height: 1.6;
}

.caution {
  margin: 0.6rem 0 0;
  color: var(--warn);
  font-size: 0.68rem;
}

.formula-grid,
.meta-formulas {
  display: grid;
  gap: 0.65rem;
}

.formula-grid article,
.meta-formulas article {
  padding: 1rem;
  border: 1px solid var(--line);
}

.formula {
  margin-top: 0.45rem;
  color: var(--accent-deep);
  font-size: 0.8rem;
  font-weight: 700;
}

.context-note {
  grid-column: 2;
  padding: 1rem;
  border: 1px solid rgba(15, 138, 107, 0.25);
  background: rgba(15, 138, 107, 0.06);
}

.meta-method {
  background:
    radial-gradient(circle at 90% 90%, rgba(196, 92, 38, 0.1), transparent 28%),
    rgba(255, 255, 255, 0.82);
}

.worked-example {
  grid-column: 2;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: #f6f9f7;
}

.worked-example h3 {
  margin: 0.35rem 0 0;
  font: 700 1.3rem var(--display);
}

.worked-example dl {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1px;
  margin: 1rem 0;
  background: var(--line);
}

.worked-example dl div {
  padding: 0.8rem;
  background: #fff;
}

.worked-example dt {
  color: var(--ink-soft);
  font-size: 0.62rem;
}

.worked-example dd {
  margin: 0.35rem 0 0;
  font: 700 1.2rem var(--display);
}

.worked-example .result {
  background: rgba(15, 138, 107, 0.1);
  color: var(--accent-deep);
}

.worked-example > p {
  margin: 0;
  color: var(--ink-soft);
  font-size: 0.72rem;
  line-height: 1.6;
}

.glossary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  margin: 0;
  border: 1px solid var(--line);
}

.glossary div {
  padding: 1rem;
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.glossary dt {
  font-weight: 700;
}

.glossary dd {
  margin: 0.35rem 0 0;
  color: var(--ink-soft);
  font-size: 0.7rem;
  line-height: 1.55;
}

@media (max-width: 850px) {
  .principle-grid {
    grid-template-columns: 1fr;
  }

  .method-section {
    grid-template-columns: 1fr;
    gap: 1.5rem;
  }

  .context-note,
  .worked-example {
    grid-column: 1;
  }
}

@media (max-width: 620px) {
  .method-page {
    width: calc(100% - 1rem);
    padding-top: 0.75rem;
  }

  .rule-list,
  .glossary {
    grid-template-columns: 1fr;
  }

  .worked-example dl {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
