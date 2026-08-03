# Phase 2 Agent: Team-Aware Questions

Phase 2 adds team, side, opponent, player, and recent-form context to the KPL
Draft Coach. All team evidence is precomputed from the selected season's
`matches.jsonl` and `bp_decisions.jsonl`; chat requests read cached artifacts
and never rerun the analysis scripts.

## Required live-simulation context

Before a draft starts, the user must select two distinct teams from the current
season roster. The application sends authoritative IDs and names for:

- the team currently playing Blue;
- the team currently playing Red;
- the active BP order and board;
- Global BP heroes used by each team; and
- the selected `stats` or `learnable` website model.

The team identity follows the club when Blue and Red swap between games. The
coach therefore already knows which selected team is on each side and should
not ask the user to repeat it.

## Supported questions and tools

### Live team-conditioned choices

Examples:

- What will Wolves most likely pick or ban next?
- What are its top three choices on this board?
- How does changing Wolves from Blue to Red affect the next forecast?

Tool: `predict_next_draft_action`

The selected league model remains the base distribution. Phase 2 applies a
confidence-weighted tendency for the acting team at the exact side and action
slot. Opponent-slot evidence is preferred only with enough support; otherwise
the tool falls back to the team's season slot tendency, then to the league
model. These are selection probabilities, not battle-win probabilities.

### Team pick and ban tendencies

Examples:

- What does Wolves like to pick?
- What does Wolves ban most from Blue?
- What is Wolves' first-pick tendency?
- What does Wolves do against AG?

Tool: `get_team_draft_tendencies`

Filters include pick/ban, Blue/Red, team action slot, and opponent. Results use
smoothed selection probability conditional on the hero being legal.

### Opening sequences

Examples:

- What are Wolves' most common first three BP actions?
- How does its Blue opening differ from Red?
- What opening has Wolves used against AG?

Tool: `get_team_opening_sequences`

### Team combination performance

Examples:

- How often did Wolves use Hero A plus Hero B?
- What was that pair's descriptive win rate from Blue?
- What pairs did Wolves use most against AG?

Tool: `get_team_combo_performance`

Pair win rate is descriptive and must be accompanied by its battle count. It
does not isolate a causal advantage from the draft.

### Player hero pools

Examples:

- Which heroes has Player X played most this season?
- Who on Wolves has played Hero A?
- What percentage of Player X's games used Hero A?

Tool: `get_player_hero_pool`

### Recent team trends

Examples:

- Which Wolves picks increased most in its last five recorded matches?
- What has Wolves recently banned more often?
- Is a recent tendency above or below its full-season rate?

Tool: `get_recent_team_trends`

The recent window is fixed at the last five recorded matches. Small samples
must be identified when they materially affect the answer.

## Still unsupported

Phase 2 does not answer:

- which action maximizes battle-win probability;
- the probability a team will win from the current draft;
- an optimal or game-theoretic draft action;
- causal claims that a hero or pair produced a win;
- private scrim, roster-intent, or unreleased-match questions; or
- match-stage filters that are not represented by a registered tool.

Those require a separately trained and validated outcome model or new analysis
artifacts. The coach must state the limitation instead of converting selection
probability or descriptive win rate into an outcome prediction.
