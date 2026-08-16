# KPL Draft Atlas: Calculation Methodology

This document describes the calculations currently implemented in this
repository. It is a technical reference for the generated JSONL outputs and
the draft-simulation API; it describes what the code calculates, rather than
claiming causal or strategic conclusions from the results.

## 1. Pipeline and source of truth

The SQLite database (`backend/data/kpl_bp.db`) is the source of truth. The
analysis pipeline produces season-scoped files in the following order:

```text
SQLite match/BP/player data
  -> analysis/exports/{league_id}/matches.jsonl
  -> analysis/exports/{league_id}/bp_decisions.jsonl
  -> relationship, meta, and team-synergy JSONL outputs
  -> analysis/outputs/{league_id}/draft_model.json
```

The pipeline runs these stages: `export`, `decisions`, `statistics`, `meta`,
`team_synergy`, and `draft_model`. Most output rows are based on an individual
ban or pick decision, not merely a final five-hero lineup.

### Terms used below

- **Battle**: one game in a match.
- **Normal battle**: a battle with at least one ban. A no-ban battle with picks
  is treated as a **peak candidate**. Meta heroes, team synergies, and the
  draft model exclude peak candidates; relationship statistics retain them in
  a separate `is_peak_battle` stratum.
- **Candidate / opportunity**: a hero in the inferred legal pool immediately
  before an action. A candidate contributes to a denominator only at actions
  where it was legal.
- **Selected**: the hero actually banned or picked at that action.
- **Context**: `action | side | team_action_type_number`, such as
  `pick|blue|2`. The slot number counts that side's actions of the same type,
  not all actions in the draft.
- **Own/opponent**: always from the acting team's perspective at the current
  action.

All displayed probabilities in generated JSONL are rounded to six decimal
places after calculation. Calculations themselves use the underlying Python
floating-point values.

## 2. Building the pre-action decision data

`analysis/build_bp_decisions.py` writes one record immediately before every
observed BP action. It preserves source actions and attaches quality flags;
it does not delete questionable source rows.

Each decision records the acting team and side, the action order, all prior
board state, prior-battle hero usage, inferred legal heroes, selected hero and
player (for picks), and battle/match outcomes.

### Inferred legal pool

Let `R` be the roster of positive hero IDs in `heroes` (or, if that table is
unavailable, in BP data). For a normal battle, before action `t`:

```text
unavailable(t) = current_battle_bans
                 ∪ current_battle_picks
                 ∪ acting_team_picks_in_earlier_battles_of_this_match  [picks only]

legal(t) = R \ unavailable(t)
```

Thus a hero already banned or picked in the current battle cannot be selected
again by either side. Under Global BP, a team cannot pick one of its own
heroes used in an earlier battle of the same match. Heroes previously used by
the opponent remain eligible for the acting team.

For a peak candidate, only the acting team's current picks are removed for a
pick; previous-battle usage is deliberately not applied because peak lineups
may be chosen independently. A ban otherwise removes all current picks and
bans, although peak candidates normally have no bans.

If the observed selected hero falls outside this inferred pool, it is retained
and given `selected_hero_outside_inferred_legal_pool`. Downstream statistical
calculations add that selected hero back into the effective legal set and
count a `legal_override`; this prevents an imperfect inference from silently
discarding the observed action.

Other notable quality flags include unmapped acting teams, invalid hero IDs,
and picks without a player mapping.

## 3. Common statistical quantities

For a candidate hero `h` in any stated group:

```text
opportunities(h) = number of relevant decisions where h is effectively legal
selections(h)    = number of those decisions selecting h
raw probability  = selections(h) / opportunities(h)
```

The project uses a 95% Wilson score interval for a binomial probability. With
`s` selections, `n` opportunities, `p = s/n`, and `z = 1.959963984540054`:

```text
denominator = 1 + z²/n
center      = (p + z²/(2n)) / denominator
margin      = z × sqrt(p(1-p)/n + z²/(4n²)) / denominator
CI95        = [max(0, center-margin), min(1, center+margin)]
```

Where a baseline probability `b` and smoothing strength `α` are supplied,
the availability-adjusted smoothed probability and lift are:

```text
smoothed probability = (selections + α × b) / (opportunities + α)
smoothed lift        = smoothed probability / b
```

Lift is `null` when `b = 0`. Win rate fields are descriptive:
`battle wins when selected / selections`; they are not used to rank or train
the current draft model.

## 4. General hero relationship statistics

`analysis/compute_bp_statistics.py` produces four files. Its default is
`α = 10` and the management pipeline emits only rows with at least two
selections (`--min-selections 2`). Each output contains both:

- `overall`: grouped by peak/non-peak and response action; and
- `slot_context`: additionally grouped by response side and that side's
  action-type slot.

For ordinary relationship outputs, the baseline for candidate `B` is the
unconditional selection probability for the same output level, peak status,
action (and side/slot for `slot_context`):

```text
baseline(B) = baseline selections(B) / baseline legal opportunities(B)
```

The pair then uses the common formula above. `availability_rate` is the share
of source contexts in which the candidate was legal:

```text
availability rate = legal opportunities / relevant source-decision count
```

### Pick synergy: `pick_synergy_stats.jsonl`

For every pick decision, each already-selected allied hero `A` in
`current_team_picks` is a source. Every legal candidate `B` is an opportunity;
the actually picked hero increments selections for `(A, B)`. It answers:

> Given that this team has already picked A, how often does it pick B when B
> is available?

### Counter picks: `counter_pick_stats.jsonl`

This is the same calculation on pick decisions, but source `A` comes from
`current_opponent_picks`. It measures historical response/denial associations,
not a proven gameplay counter.

### Counter bans: `counter_ban_stats.jsonl`

Again the same calculation, but on ban decisions after an opponent hero `A`
is visible. It measures the tendency to ban candidate `B` in that situation.
The code does not restrict it to a named phase; the board state and
side/slot-context fields indicate when it occurred.

### Ban responses: `ban_response_stats.jsonl`

For every triggering ban of hero `A`, the script creates three kinds of later
events in the same battle:

1. the first subsequent ban by the opponent (`opponent_next_ban`), if one
   exists;
2. every later pick by the team that made the triggering ban
   (`banning_team_later_pick`); and
3. every later pick by the opponent (`opponent_later_pick`).

For each scope, candidate `B` is evaluated in the response action's effective
legal pool. The baseline is calculated within that response scope and action
(plus response side/slot for `slot_context`), then the same smoothing, lift,
confidence interval, and descriptive win-rate formulas are applied.

## 5. Team-specific hero synergy

`analysis/compute_team_synergies.py` measures which unordered pairs a
specific team completes more often than its own normal candidate baseline. It
uses normal-battle pick decisions only and requires an acting team ID.
Defaults are `α = 10` and `min_selections = 2`.

For every team pick decision with an existing allied pick `A`, and every legal
candidate `B != A`, it creates an unordered pair:

```text
pair = (min(A, B), max(A, B))
```

Consequently, `A -> B` and `B -> A` accumulate into the same pair for that
team across battles. Selecting `B` increments the pair's `selection_count`.

The team's ordinary probability of picking `B` is first computed over all of
that team's normal pick decisions:

```text
team baseline(B) = team selections(B) / team legal opportunities(B)
```

For pair `(A, B)`, the stored baseline is the average of that candidate
baseline over every pair opportunity, and the reported values are:

```text
raw completion probability      = pair selections / pair opportunities
team baseline completion prob.  = sum(team baseline(candidate)) / pair opportunities
smoothed completion probability = (pair selections + 10 × team baseline) /
                                  (pair opportunities + 10)
smoothed lift                   = smoothed completion probability / team baseline
```

`battle_win_rate_when_paired` is wins by the acting team at selections of that
pair divided by the pair's selection count. It is informative only; it does
not alter synergy lift or rank. Rows are sorted within team by selection count
then lift, and `team_pair_rank` follows that order.

## 6. Meta-hero / opening-priority ranking

`analysis/compute_meta_heroes.py` ranks early draft attention, not hero power,
win rate, or universal team suitability. It uses normal battles only and
requires at least 10 eligible battles per hero by default.

For each battle, the script:

1. takes bans at BP orders 1 through 4 as the opening-ban phase;
2. defines a hero's battle eligibility as appearing in the union of the
   effective legal pools for those opening bans;
3. finds Blue's first pick (`action = pick`, `side = blue`, and Blue's first
   pick slot); and
4. counts Blue-first-pick opportunities only when the hero is legal at that
   pick.

For hero `h`:

```text
opening ban rate(h) = opening bans of h / eligible battles for h

Blue first-pick rate given legal(h) = Blue first picks of h /
                                      legal Blue-first-pick opportunities for h

early priority count(h) = opening bans of h + Blue first picks of h
early priority rate(h)  = early priority count(h) / eligible battles for h
```

The code also reports the Wilson 95% interval of early priority and the two
shares of early priority:

```text
opening-ban share       = opening bans / early priority count
Blue-first-pick share   = Blue first picks / early priority count
```

Rows rank by descending early-priority rate, then count, then opening-ban
rate, then hero ID. `quality_flagged_priority_count` counts early-priority
events from a battle whose opening bans or Blue first pick carried a quality
flag.

## 7. Draft probability model

`analysis/build_draft_model.py` builds an interpretable next-action model.
The management pipeline runs `--per-season`: each season's model trains on
that season plus up to its four immediately preceding available season exports
(at most five seasons). Training excludes peak battles and requires a valid
ban/pick action, positive selected hero ID, and a nonempty effective legal
pool. The selected season receives weight `1.00`; each preceding season is
discounted by `0.45`, giving default weights of `1.00`, `0.45`, `0.20`,
`0.09`, and `0.04` from newest to oldest.

The model stores:

- the most common observed draft sequence `(BP order, action, side, slot)`;
- hero IDs, labels, icons, and observed eligible positions;
- action-level base counts;
- exact-context base counts; and
- retained one-hero-to-one-hero contextual relationship counts.

### Base probabilities

For candidate `h` at a next step with action `a` and context `c`:

```text
action probability q(a,h) = action selections(a,h) / action opportunities(a,h)

base probability p(c,h) = (context selections(c,h) + 12 × q(a,h)) /
                          (context opportunities(c,h) + 12)
```

If that hero never has an exact-context row, the model uses `q(a,h)` instead.
If no action-level row exists, `q` is `1e-9`. This is baseline smoothing, not
a Bayesian posterior with a separately estimated uncertainty interval.

### Relationships and their effects

Every already-visible hero is processed separately in one of four roles:

| Role | Source field |
| --- | --- |
| `own_pick` | picks already made by the acting side |
| `opponent_pick` | picks already made by the other side |
| `own_ban` | bans made by the acting side |
| `opponent_ban` | bans made by the other side |

A relationship `(context, role, source A, candidate B)` is retained only if
it was selected at least two times in training. Its opportunity count is the
number of matching source-visible decisions where `B` was legal. Given the
base `p`, the relationship computes:

```text
relationship probability r = (relationship selections + 12 × p) /
                             (relationship opportunities + 12)
lift L                     = r / p
evidence weight w          = opportunities / (opportunities + 20)
capped log effect          = clamp(log(L), -log(3), log(3))
```

The log-score for `B` starts at `log(p)`. Every retained visible relationship
adds `w × capped log effect`:

```text
score(B) = log(p) + Σ [w_i × clamp(log(L_i), -log(3), log(3))]
raw weight(B) = exp(score(B))
probability(B) = raw weight(B) / Σ(raw weight of every legal candidate)
```

`own_pick` is the hero-pair/synergy relationship. Its effect is multiplied by
`2.0`, while the other three relationship types retain weight `1.0`, so a
well-supported pair has more influence on a later pick.

### Opening meta effect

For opening actions (orders 1–5), the model also measures each hero's weighted
opening priority: opening bans plus Blue's first pick, divided by its weighted
legal opportunities. Relative to the overall opening rate, that lift is capped
at `4×` and adds `0.65 × log(lift)` to the candidate's score. This makes
current-season meta heroes more prominent without affecting later draft phases.

The final normalization means the probability list always sums to 1 across
the candidates that survive legality filtering.

### Prediction legality filters

Before scoring, the simulator removes any hero already picked or banned in
the current battle. A caller can additionally provide `legal_hero_ids`; then
the candidate list is restricted to that list.

For a pick, the backend simulator additionally removes the acting side's
`{blue|red}_used_previous_battles`, enforcing Global BP. It then checks role
feasibility: all current and prospective picks must admit a one-to-one
assignment to their observed eligible role IDs. Bans have no role check.

The offline command-line simulator also prevents reused board heroes and
enforces distinct roles, but does not accept/apply the previous-battle fields;
the API service is the implementation that enforces those fields for an
interactive match state.

### Rollouts

For a requested `bp_order`, simulation takes the learned draft sequence from
that order onward. For each rollout, it repeatedly scores the current board,
samples one hero with the normalized next-action probabilities, adds it as the
step's pick or ban, and advances to the next step. The reported probability of
a hero at a future order is:

```text
times hero was sampled at that order / requested rollout count
```

`banned_by_end` uses the same denominator across all rollouts. A seed makes
the pseudo-random sampling reproducible.

## 8. Important interpretation limits

- All relationship and synergy figures are observational associations. Patch
  changes, team preferences, unobserved draft state, and tournament mix can
  all create an apparent lift.
- The current next-action model has no direct feature for team identity,
  player form, match score, best-of length, patch notes, or win rate.
- Legal pools are inferred from available data and are deliberately auditable
  through quality flags and legal overrides.
- The draft model predicts historical choice tendencies under its training
  window; it does not prescribe an optimal draft or estimate a chance to win.

## 9. Key implementation files

- `analysis/build_bp_decisions.py` — pre-action state and legal-pool inference.
- `analysis/compute_bp_statistics.py` — general synergy/counter/ban-response
  outputs.
- `analysis/compute_team_synergies.py` — team-specific unordered pairs.
- `analysis/compute_meta_heroes.py` — early-priority meta rankings.
- `analysis/build_draft_model.py` — training, scoring, and offline rollouts.
- `backend/app/services/draft_simulator.py` — API-time prediction, legality,
  and simulation.
