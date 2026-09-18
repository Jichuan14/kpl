# Researching the intention behind a BP move

This research asks what evidence supports a strategic explanation for one
observed move. The existing BP policy and recommendation models remain the
production systems. This study does not train, promote, or alter them.

The intended website output is a short explanation with a named beneficiary or
target, supporting historical drafts, alternatives, and an explicit strength of
evidence. The source logs have no coach-intention labels, so even a convincing
explanation is an inference. A model's contribution to its own recommendation is
also not evidence that a human coach had that reason.

## What the two motivating examples actually ask

Hero identity must be exact: 元坦 is **元流之子(坦克), ID 581**. The support variant
is ID 585. 鲁班大师 is 525, 海月 is 521, and 关羽 is 140.

| Hypothesis | Observable evidence to seek | Evidence that is insufficient by itself |
| --- | --- | --- |
| Ban 海月 to prepare our 关羽 | Our later 关羽 selection is more common after this ban than after comparable alternatives; timing, own hero pool, and current board fit | 关羽 happened to appear later in this one game |
| Ban 元坦 to discourage opponent 鲁班大师 | Opponent 鲁班大师 selection falls relative to comparable alternatives that also leave 鲁班大师 open | 元坦 and 鲁班大师 often appear together |
| Ban 元坦 to weaken an opponent 鲁班大师 composition | The opponent loses a relevant partner and remaining substitutes offer less support for its plan; 鲁班大师 may still be picked | The probability of the impossible banned 元坦 + 鲁班大师 pair becomes zero |
| Ban A to protect an already visible own B | A was a feasible opponent response, the relevant relationship has evidence, and replacements are less threatening | A generic control/damage tag or an outcome win rate |
| Pick A to complete an own package | Existing allies, compatible remaining roles, ordered pair evidence, and alternatives support this reading | A appears in every simulated lineup after we force-pick A |
| Pick A before the opponent can take it | Opponent access to A in alternatives, the next pick window, and both teams' eligibility | A is globally popular |

One move can serve multiple purposes. Opponent denial and own preparation are
not mutually exclusive. A ban can also weaken a package without changing the
probability of selecting its central hero. Therefore a follow-up frequency test
is only one component of an eventual intention explanation.

## Existing evidence and its limits

`bp_decisions.jsonl` records the state immediately before each action: order,
side, teams, visible picks and bans, both teams' prior-game usage, and inferred
legal candidates. Joining `matches.jsonl` supplies match start time and ordered
raw actions. Final player mappings and winners are retrospective fields, not
inputs for intention inference. Per-action wall-clock timestamps, patch IDs,
coach communications, and confirmed intention labels are absent.

The existing relationship artifacts already handle legal opportunities and
ordered responses. Their denominator, however, is usually a **later pick
decision where the candidate is still legal**, rather than the original ban
decision. That answers a different question from "how often does this original
ban lead to our eventual B?" A hero banned or stolen during the intervening
sequence disappears from that later-opportunity denominator. Multiple later
pick decisions can also refer to the same original ban.

For example, the full 20260003 `ban_response_stats.jsonl` has 32 selections in
349 later legal pick opportunities for own 关羽 following a 海月 ban, with
smoothed lift 1.776172. The opponent-side relation has 38/339 and lift 2.164267.
Those are valid descriptive relationship rows; neither is a percentage of 海月
bans, nor an intention probability. Both directions deserve inspection before
attributing the ban to our own 关羽 plan.

The same season's full `pick_synergy_stats.jsonl` records 鲁班大师 → 元坦 in
13/187 legal later-pick opportunities (lift 3.313875), whereas 元坦 → 鲁班大师
has only 2 legal opportunities, both selected. The reversed direction is sparse
because the draft order and availability differ. An unordered pair frequency
would hide this distinction.

## Measured findings from the local exports

The case study was run on 2026-09-15 against all seven available leagues, with
seven predefined probes per league (49 comparisons). Leagues are analyzed
separately. These are exploratory observations, not independent confirmation
or statistically established effects. The machine-readable results retain
source SHA-256 fingerprints and exclusions.

For 20260003, the decision export contains 548 battles. The probe accepts 540
matching complete schedules, excludes seven quality-flagged battles, and
excludes one unrecognized/incomplete schedule. This strict decision-export
subset can differ from counts based solely on raw match action completeness.

| Continuation in 20260003 | Eligible actual A moves | Later B picks | Adjusted A rate | Adjusted other-choice rate | Difference |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ban 海月 → own 关羽 | 152 | 31 | 20.4% | 16.5% | +3.9 percentage points |
| Ban 海月 → opponent 关羽 | 143 | 38 | 26.6% | 15.4% | +11.1 percentage points |
| Ban 元坦 → opponent 鲁班大师 | 18 | 11 | 61.1% | 50.2% | +11.0 percentage points |
| Pick 鲁班大师 → own 元坦 | 60 | 13 | 19.6% | 7.1% | +12.5 percentage points |

The final row uses 56/60 actual-A moves with comparable alternatives for its
adjusted rates; 13/60 is the unadjusted count. The first three retain all actual-A
moves in their comparisons. Different perspectives have different eligible
populations, so their rates are not a head-to-head experiment.

The own 关羽 preparation association is positive in all seven separately
analyzed leagues, but varies considerably: the adjusted difference is +23.7
points in 20250001, +3.0 in 20260001, and +3.9 in 20260003. For 20260003, the
opponent-side association is larger. The data merits a more specific test of
team, slot, and board context; a 海月 ban alone does not identify whose 关羽 plan
is being served. Neither this statistic nor the source mechanics establishes
a gameplay counter relationship.

The 元坦 example has a more decisive **eligibility** finding. Among all 124
validated 元坦 bans in 20260003, 鲁班大师 was:

- already banned in 96;
- still pick-eligible for the opponent in 18;
- already picked by the opponent in four;
- already picked by the banning team in three;
- unavailable to the opponent through prior-game use in three.

Thus "discourage an opponent's future 鲁班大师" is incompatible with most of
these boards. In the 18 remaining cases, the observed continuation does not
show reduced 鲁班大师 selection. A package-weakening motive can still be
plausible in individual boards, including the four where 鲁班大师 was already
visible. It requires separate partner/substitution evidence.

One concrete example of the latter is match `2026072203`, battle
`1222656528_4_1784721638`, game 1. 北京WB picked 鲁班大师 at order 5; 广州TTG
banned 元坦 at order 13. "Remove a partner of the visible 鲁班大师" is a
testable candidate explanation here. Calling it the proven reason would still
overstate the evidence. The context inventory includes the prefix known at
the ban so the case can be inspected without seeing the later draft.

An illustrative earlier-evidence probe also demonstrates the cutoff and slot
filters: using only matches starting before `2026072203`, blue-side 海月 bans
at order 3 were followed by own 关羽 in 7/15 cases (46.7%), versus a
game-number-standardized alternative rate of 22.9%. Five of those 15 were
followed by a 关羽 ban; all five correctly remain failed continuations. This
small, exploratory query is not a held-out validation of motive.

Reproduce the season comparisons and the cutoff example from the repo root:

```bash
python3 analysis/run_intention_case_study.py \
  --league-ids 20250001 20250002 20250003 20250004 20260001 20260002 20260003

python3 analysis/explore_draft_intentions.py \
  --league-id 20260003 --action ban --hero-id 521 --target-id 140 \
  --perspective own --before-match-id 2026072203 --bp-order 3 --side blue \
  --output analysis/outputs/draft_intention_research/haiyue_before_2026072203.json \
  --report analysis/outputs/draft_intention_research/haiyue_before_2026072203.md
```

The full generated table is
`analysis/outputs/draft_intention_research/summary.md`. Each JSON report includes
individual strata, distinct match and battle counts, continuation outcomes,
example IDs, and a pre-action target-state inventory. Source files changing will
change these snapshot findings; regenerate rather than treating this table as
a maintained live statistic.

## First experiment: a reproducible continuation probe

`explore_draft_intentions.py` reads one season's existing exports. It compares
an action selecting A with other legal actions at comparable decision points,
and measures whether the chosen team later picks B. Its output is descriptive
research evidence; it does not produce an automatic tactical verdict.

The design uses these rules:

1. A trial is one original trigger decision. B must be pick-eligible for the
   relevant team at that point, including Global BP restrictions. At least one
   later pick slot must remain within the requested horizon.
2. Alternatives must have A available, and must leave B open immediately after
   the trigger. For an indirect A → B probe, an alternative that bans or picks B
   at the trigger is excluded: otherwise the result can be mechanically true.
3. Later bans, steals, or other failures to obtain B count as zero outcomes.
   They do not remove a previously eligible trial. Later actions are used only
   to measure the continuation, never as pre-action evidence.
4. Comparisons are within the same action type, side, absolute order, full BP
   schedule, game number, and visible-pick counts. Only strata containing both
   A and alternative actions enter the standardized comparison. Report how
   much evidence is lost through lack of overlap.
5. The alternative rate is standardized to the distribution of A's decisions
   across those overlapping strata. Report raw rates as well, since their
   difference can expose a misleading pooled pattern.
6. A match cutoff uses the exported start time and excludes the entire target
   match and all concurrent/later matches. The data offers scheduled match
   starts, not verified completion times; this is a historical time filter,
   not proof that another match's result was already publicly available.
7. Peak, incomplete, quality-flagged, or malformed battles are excluded with
   diagnostics. No synthetic replacement statistics are produced.

The matching does not control the exact visible heroes, roster, patch, team
strength, or unobserved strategy. Team/opponent filters narrow the scope but may
leave too little overlap. Repeated decisions and games within a match are
dependent; raw Wilson intervals are descriptive action-level intervals, not
match-adjusted inference. There is no confidence interval for a causal effect,
no significance claim, and no probability of a coach's intention.

Same-hero opponent probes (A = B) are explicitly structural access-denial checks.
They quantify how often the opponent obtains A under alternatives; banning or
picking A itself removes opponent access by construction. They do not validate
denial as the coach's reason.

## Why the current model is not a historical oracle

The production policy is useful for prospective simulations. The currently
available 20260003 artifact was generated on 2026-09-06 and trained using most of
the target season, with later validation/calibration/holdout splits. The serving
player context has `context_as_of = 2026-09-05` and
`maximum_source_event_date = 2026-09-04`. Using these artifacts to explain a June
game as if standing at the June draft would include later information.

The existing `sample_forced_draft_completions` function can force a legal next
action and sample the remaining canonical sequence. A future research stage can
call this frozen policy to show **how today's policy responds to a hypothetical
move**. Historical evaluation needs verified earlier model/context snapshots,
and schedule compatibility checks. Neither mode can identify private intent.

For a branch comparison, keep the state and action slot fixed, compare multiple
legal alternatives that leave the secondary target open, and measure the target
team's next pick window as well as its final draft. Report the variation across
alternative actions and Monte Carlo error. The public 50-rollout scenario tool
is not sufficient evidence for small probability differences. Sampling intervals
describe uncertainty in the simulation, not uncertainty in the model's knowledge
of real coaching motives.

A+B completion is not a valid outcome to prove package denial after banning A:
its disappearance is guaranteed. To investigate package weakening, compare
the remaining partners, their own eligibility and role fit, and complete legal
lineups under a separately stated scoring criterion. Current lineup scores are
relative model evaluations, not causal win gains or a coach's utility function.

There is a specific additional limit in the current 20260003 lineup artifact:
the coefficients for mechanics ally compatibility, league pair synergy, and
team pair synergy are all zero (mechanics counter advantage is also zero).
The active terms are team strength, hero familiarity, and historical counters.
Its score therefore cannot substantiate a claim that this ban reduced an ally
synergy bonus. This is a constraint on using that model as explanation evidence,
not a reason to modify the user's chosen BP model. Pair-completion evidence and
expert assessment are more relevant to the package hypothesis at this stage.

## Conditions for website use

The first website feature should be an evidence card for a selected move, with
the exact pre-move board and a visible distinction between what was known then
and what happened afterward. Suitable wording is "consistent with preparing
关羽" or "removes a frequent partner of 鲁班大师" when the relevant evidence
supports it. "The coach banned A because ..." exceeds the source evidence.

Before generating these cards automatically:

- Validate on later matches using earlier evidence; compare a slot-only baseline
  against exact-board and team-aware evidence. Report coverage and abstentions.
- Evaluate the candidate mechanism independently of the displayed continuation.
  Predicting a later hero alone does not validate its motive.
- Review diverse cases with a KPL expert, including contradictory evidence,
  unusual bans, mixed motives, and low-sample cases. Allow multiple labels and
  disagreement. Coach/interview evidence, when available, is stronger than an
  analyst's plausible explanation.
- Treat scanning many hero pairs as exploration. Freeze a hypothesis and test
  it on unseen matches before calling it supported; avoid selecting the largest
  historical lift and reusing that data as confirmation.
- Record hero IDs, action/state identity, chronology, source fingerprints,
  alternative definitions, denominators, and evidence references in each card.

An additional motive worth studying later is preserving hero resources for the
rest of a BO5/BO7. Global BP history makes this plausible, but a current-game
continuation test cannot establish series-level planning.
