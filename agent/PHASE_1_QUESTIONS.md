# Phase 1 Agent: Supported Questions

This document defines the questions the first version of the KPL Draft Coach
can answer using analysis and model artifacts that already exist in this
repository. Phase 1 does not add team-specific draft-tendency training or a
draft win-probability model.

## Required context

The frontend should send the selected `league_id` with every question. For
questions about an active draft, it should also send:

- current `bp_order`;
- Blue and Red picks;
- Blue and Red bans;
- each team's heroes used in previous games of the current series; and
- the selected draft model (`stats` or `learnable`).

The agent should ask for clarification when the season, hero, team, or draft
state needed to answer a question is missing or ambiguous.

## 1. Next-action draft forecasts

Backed by `draft_model.json` or the optional learnable model and the existing
draft simulator.

Example questions:

- What is the most likely next pick?
- What is the most likely next ban?
- What are the top three possible choices at this step?
- How likely is Hero A to be selected next?
- Is Hero A a legal choice on the current board?
- Which legal heroes have the highest probability right now?
- How does the statistical model rank the next choices?
- How does the learnable model rank the next choices?

Tool:

```text
predict_next_draft_action
```

The answer may describe league-wide historical selection probability. It must
not describe that probability as win probability or as a team-specific
forecast.

## 2. Future draft simulations

Backed by the rollout results from the existing draft simulator.

Example questions:

- What are the likely next three BP actions?
- How might the rest of this draft develop?
- Which heroes are likely to be banned by the end?
- If this board continues normally, what heroes may appear later?
- Show several plausible continuations from the current board.

Tool:

```text
simulate_future_draft
```

Simulation results are possible continuations sampled from historical draft
behavior. They are not guarantees, optimal strategies, or battle-win
predictions.

## 3. League-wide hero relationships

Backed by:

```text
pick_synergy_stats.jsonl
counter_pick_stats.jsonl
counter_ban_stats.jsonl
ban_response_stats.jsonl
```

Example questions:

- What heroes are commonly picked with Hero A?
- What are the strongest historical pairings for Hero A?
- What is commonly picked against Hero A?
- What is commonly banned after the opponent picks Hero A?
- After Hero A is banned, what does the opponent tend to ban next?
- After banning Hero A, what does the banning team tend to pick later?
- What responses to Hero A have the largest lift over the baseline?
- How many selections and legal opportunities support this relationship?
- What is the confidence interval for this relationship?
- Does this relationship change by side or pick/ban slot?

Tool:

```text
get_hero_relationships
```

These relationships are historical associations. The agent must not call a
counter-pick relationship a proven gameplay counter or causal advantage.

## 4. Team-specific hero pairs

Backed by `team_synergy_stats.jsonl`.

Example questions:

- What hero pairs does Wolves complete most often?
- What heroes has Wolves historically paired with Hero A?
- What are AG's highest-lift hero pairs?
- How often did Wolves complete the Hero A and Hero B pair?
- What was Wolves' battle win rate when it completed that pair?
- How many opportunities and selections support the pair statistic?
- Is the pair's completion rate above Wolves' ordinary baseline?

Tool:

```text
get_team_synergies
```

Phase 1 team-pair statistics are season-wide. They cannot be filtered by Blue
versus Red side, opponent, match stage, or recent time window without new
analysis.

## 5. Season meta and individual hero BP statistics

Backed by `meta_hero_stats.jsonl` and the `hero_bp_stats` SQLite table.

Example questions:

- What are the highest-priority meta heroes this season?
- Which heroes have the highest opening-ban rate?
- Which heroes have the highest Blue first-pick rate when legal?
- What is Hero A's pick rate, ban rate, presence rate, and win rate?
- How many battles support Hero A's statistics?
- Compare the season statistics of Hero A and Hero B.

Tools:

```text
get_meta_heroes
get_hero_bp_stats
```

Win rate is descriptive and should be shown with its sample size. It does not
prove that selecting a hero caused the wins.

## 6. Existing battle BP sequences

Backed by the existing battle BP API and SQLite battle records.

Example questions:

- Show the complete BP sequence for battle ID X.
- Which hero was picked at BP step 7 in battle X?
- What had already been picked and banned before a specified action?
- Which side picked or banned Hero A in battle X?

Tool:

```text
get_battle_draft
```

The user must provide a resolvable battle ID. Searching for a battle using a
natural-language description is outside Phase 1 unless an entity-search tool
is added.

## Questions that require more than one Phase 1 tool

The agent may combine a small number of tools when the question requires it.

Examples:

- "Why is Hero A predicted next?" can combine
  `predict_next_draft_action` with `get_hero_relationships`.
- "What are the likely choices, and what works with our current first pick?"
  can combine `predict_next_draft_action` with
  `get_hero_relationships`.
- "Does Wolves commonly use this predicted pair?" can combine
  `predict_next_draft_action` with `get_team_synergies`.
- "Is this predicted hero also a high-priority meta hero?" can combine
  `predict_next_draft_action` with `get_meta_heroes`.

The agent should call only the tools needed for the question. Artifact-backed
tools should query in-memory indexes; they should not rerun analysis scripts.

## Questions Phase 1 cannot answer reliably

The agent should clearly state the limitation for these questions:

- What will Wolves specifically pick next?
- What are Wolves' usual first three BP actions?
- What does Wolves usually pick or ban in response to Hero A?
- What does Wolves do specifically against AG?
- What is Wolves' Hero A and Hero B win rate only on Blue?
- What have Wolves' last five matches changed about its draft tendencies?
- Which player will use a predicted hero?
- Which pick gives us the highest probability of winning the battle?
- What is the optimal draft action?

The first seven require additional team-, side-, opponent-, player-, or
time-filtered analysis. The last two require a validated draft-outcome model or
an explicitly defined heuristic; the existing draft model predicts selection
behavior rather than battle outcomes.

## Phase 1 evidence rules

Every factual answer should include the relevant evidence fields when they are
available:

- season;
- model type or artifact source;
- selection count;
- legal opportunity or battle count;
- probability or rate;
- baseline and lift, when applicable;
- confidence interval, when available; and
- a small-sample or data-quality warning when appropriate.

If no tool can retrieve evidence for a claim, the agent should say that the
question is not supported in Phase 1 rather than inventing an answer.
