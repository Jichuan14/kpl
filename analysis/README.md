# Analysis

Offline BP analysis scripts and notebooks. Reads from `backend/data/kpl_bp.db`.

## Setup

```bash
# from repo root
python3 analysis/init_heroes.py
```

This also maintains `hero_positions`: one row per hero and observed role. A
hero may have several rows when it has been played in multiple positions.

## Scripts

| File | Purpose |
|---|---|
| `common.py` | Shared DB path, connect, league resolution |
| `init_heroes.py` | Create/refresh hero metadata and observed role eligibility |
| `sync_battle_players.py` | Sync `teams`, `players`, `battle_players` from battle detail API |
| `qa_bp.py` | Per-league BP data QA (completeness, peak candidates, pick reuse) |
| `export_match_data.py` | Export ordered match BP, players, sides, winners, and quality flags to JSONL |
| `build_bp_decisions.py` | Convert match JSONL into one pre-action state per ban/pick |
| `compute_bp_statistics.py` | Compute availability-adjusted response, synergy, counter-pick, and counter-ban statistics |
| `compute_meta_heroes.py` | Rank opening-priority heroes from first-phase bans and Blue first picks |
| `compute_team_synergies.py` | Rank availability-adjusted hero pairs preferred by each team |
| `compute_team_draft_profiles.py` | Build season rosters, team tendencies/openings/combos, player pools, and recent trends |
| `compute_power_rankings.py` | Build decayed team Elo plus player-position and player-hero performance boards across available seasons |
| `build_hero_tactical_roles.py` | Build the commentary-only hero class and tactical-role artifact from Tencent sources |
| `build_draft_model.py` | Train an interpretable next-action probability model and run BP rollouts |
| `train_learnable_draft_choice_model.ipynb` | Train the team-aware learnable choice model with acting-team and opponent-team embeddings |

The Vue management page runs the complete pipeline automatically after a
league download, or lets each stage run separately. Its outputs are isolated
by league:

```text
analysis/exports/{league_id}/matches.jsonl
analysis/exports/{league_id}/bp_decisions.jsonl
analysis/outputs/{league_id}/*.jsonl
```

The commands below remain useful for manual runs and custom paths.

### BP QA

Defaults to **2026 KPL 夏季赛** (`20260003`). Pass flags for any other season:

```bash
python3 analysis/qa_bp.py
python3 analysis/qa_bp.py --year 2026 --name 夏季赛
python3 analysis/qa_bp.py --league-id 20260003
python3 analysis/qa_bp.py --year 2025 --name 挑战者杯 --json-out analysis/outputs/qa_2025_challenger.json
```

### Legacy team / player backfill

The backend's league download now stores teams, players, and battle-player
mappings from the same battle-detail request as BP actions. This script remains
available only for older databases that need a backfill. It stores raw `camp`
plus `match_camp` aligned to match `camp1`/`camp2`.

```bash
# prefer backend venv (has httpx)
source backend/.venv/bin/activate
python3 analysis/sync_battle_players.py --year 2026 --name 夏季赛
python3 analysis/sync_battle_players.py --league-id 20260003 --battle-limit 5
python3 analysis/sync_battle_players.py --year 2026 --name 夏季赛 --only-missing
```

### Export analysis data

SQLite stays the source of truth. The exporter writes one complete match per
JSONL line and preserves questionable rows with `quality_flags`.

```bash
# One match
python3 analysis/export_match_data.py --match-id 2026042501

# Entire season
python3 analysis/export_match_data.py --year 2026 --name 夏季赛
```

### Build BP decision states

Creates one JSONL record per ban/pick with the acting team, side, prior match
usage, current draft, inferred legal hero pool, selected hero/player, and
outcomes. It preserves source quality flags.

```bash
python3 analysis/build_bp_decisions.py
python3 analysis/build_bp_decisions.py \
  --input analysis/exports/20260003/matches.jsonl \
  --output analysis/exports/20260003/bp_decisions.jsonl
```

### Compute statistical BP relationships

Only decisions where a candidate hero was legal count toward that candidate's
denominator. Results include raw and smoothed probabilities, baseline rates,
lift, 95% Wilson intervals, outcomes, sample counts, and legal overrides.

```bash
python3 analysis/compute_bp_statistics.py
python3 analysis/compute_bp_statistics.py --alpha 10 --min-selections 2
```

Generated under `analysis/outputs/`:

- `ban_response_stats.jsonl` — opponent's next ban plus all later picks,
  separated between the banning team and its opponent
- `pick_synergy_stats.jsonl`
- `counter_pick_stats.jsonl`
- `counter_ban_stats.jsonl`

### Compute opening-priority meta heroes

Ranks heroes by whether they were banned in BP orders 1–4 or selected with
Blue's first pick. It also reports those components separately and adjusts the
Blue first-pick denominator for hero legality.

```bash
python3 analysis/compute_meta_heroes.py --league-id 20260003
python3 analysis/compute_meta_heroes.py --league-id 20260001 --min-battles 20
```

Output:

```text
analysis/outputs/{league_id}/meta_hero_stats.jsonl
```

### Build team, player-position, and player-hero power rankings

The selected season determines eligible teams, player-position pairs, and
player-hero pairs. All available match exports up to that season contribute
with a 180-day half-life. Team strength blends opponent-adjusted Elo with a
decayed Bayesian win rate.
Player-hero strength blends role-and-season-normalized KDA, MVP score,
participation, hero damage share, gold pace, and battle results, with a neutral
prior protecting sparse samples. Position boards use that same single-game
score but aggregate every hero an active player used in the role. The website
defaults to at least five selected-season games when displaying those boards.

```bash
python3 analysis/compute_power_rankings.py --league-id 20260003
```

Output:

```text
analysis/outputs/{league_id}/power_rankings.json
```

### Build a draft probability model

Builds a smoothed, contextual model for the next legal pick or ban. It uses
the action, side, draft slot, and already-visible own/opponent picks and bans.
The default training set includes all available BP decision exports and writes
the artifact to the 2026 S3 output folder.

```bash
# Train the default model.
python3 analysis/build_draft_model.py

# Write one artifact per season. Each uses that season and its four most
# recent available predecessors (up to five seasons total), with the selected
# season weighted much more heavily than older data.
python3 analysis/build_draft_model.py --per-season

# Score the next action and simulate the rest of an example draft.
python3 analysis/build_draft_model.py \
  --state analysis/example_draft_state.json \
  --rollouts 1000 \
  --seed 7
```

The state requires `bp_order`, the next action number. It may also provide
`blue_picks`, `red_picks`, `blue_bans`, `red_bans`, and an exact
`legal_hero_ids` list. Omitting the legal list uses every trained hero that is
not already on the board.

### Train the team-aware learnable model

The learnable model trains on the target season and its four immediately
preceding exports. Its input combines the original lane/damage/control profile
with Tencent-catalogue-derived ability mechanics and conditions, such as hard
control, cleanse, projectile blocking, ally repositioning, skill refresh, and
channeling. It also learns 16-dimensional acting-team and opponent-team
embeddings from IDs already present in `bp_decisions.jsonl`. Unknown teams fall
back to the shared league/draft representation at inference time.

Regenerate the combined feature vectors, then train from the repository root:

```bash
python3 analysis/build_hero_draft_feature_vectors.py
python3 analysis/train_learnable_draft_choice_model.py --league-id 20260003
```

Earlier seasons are included with geometric recency weights: the target season
has weight `1.0`, then each older season is multiplied by `--recency-decay`
(default `0.65`). Test a value against the latest held-out matches without
writing over the production artifacts:

```bash
python3 analysis/train_learnable_draft_choice_model.py \
  --league-id 20260003 \
  --recency-decay 0.65 \
  --holdout-current-season-matches 6 \
  --model-output /tmp/kpl-recency-test-model.json \
  --feature-space-output /tmp/kpl-recency-test-space.json
```

Holdout metrics use one equal-weighted vote per future pick or ban, rather than
the winning-pick training weight.

The normal `learnable_draft_model` pipeline step runs the vector build first.
Training writes the schema-v2 artifact to:

```text
analysis/outputs/20260003/learnable_draft_choice_model.json
```

The backend uses these learned team embeddings directly for the `learnable`
model. It does not apply `team_action_tendencies.jsonl` afterward; that artifact
continues to calibrate only the statistical model.

### Train the chronological bag + GRU web model

The sequence model is trained with PyTorch and exported as a schema-v3 JSON
artifact. The live backend uses its existing NumPy dependency for inference;
PyTorch is installed in the deployed API image only so the private management
pipeline can retrain the artifact.

From a Python environment containing PyTorch, run:

```bash
python analysis/build_hero_lane_profiles.py --through-season 20260003
python analysis/train_sequence_draft_choice_model.py --league-id 20260003
```

`hero_lane_profiles.json` is a separate, versioned constraint artifact. It
uses repeated cross-lane play to label flex heroes, exempts ambiguous heroes,
and applies second-round single-lane ban constraints across clash, mid,
jungle, farm, and roam. Multi-lane heroes are never removed by this rule.

This command trains the frozen bag baseline and GRU residual with chronological
validation and holdout windows, then atomically writes:

```text
analysis/outputs/20260003/sequence_draft_choice_model.json
```

To export an already trained checkpoint without retraining:

```bash
python analysis/export_sequence_draft_choice_model.py \
  --league-id 20260003 \
  --checkpoint /path/to/hybrid_bag_gru.pt \
  --experiment-results /path/to/results.json
```

The web simulator exposes this artifact as the `sequence` model. The current
`learnable` model remains the default until the sequence artifact has passed a
future holdout and production rollout benchmark.

The management pipeline exposes training as the `sequence_draft_model` step.
The `all`/full-update flow runs it after the existing learnable model, so
refreshed BP decisions automatically produce a fresh sequence artifact.

### Build hero tactical roles for commentary

This sidecar describes what a hero does in a lineup rather than treating every
control or damage tag as interchangeable. It records Tencent's official hero
classes and tank classification, numeric attribute bars, structured official
hero relationships, and conservative tactical labels such as `frontline`,
`primary_engage`, `peel_disengage`, `ally_reposition`, and `long_range_poke`.
The generated JSON retains only controlled labels and short matched evidence,
not full webpage prose.

```bash
source backend/.venv/bin/activate
python3 analysis/build_hero_tactical_roles.py
```

Output:

```text
analysis/hero_tactical_roles.json
```

This artifact is for the commentary evidence layer only. It is deliberately
separate from `hero_draft_feature_vectors.json`, so rebuilding it neither
changes the English ML feature space nor requires model retraining. The build
fails validation when official class/page coverage is incomplete or when a
tactical role is invalid.

### Compute team-specific hero synergies

For each team, measures how often it completes an unordered hero pair when one
hero is already visible and the other is legal. Results include pair support,
the team's normal candidate baseline, smoothed lift, confidence interval, and
battle win rate.

```bash
python3 analysis/compute_team_synergies.py --league-id 20260001
```

Output:

```text
analysis/outputs/{league_id}/team_synergy_stats.jsonl
```

### Build Phase 2 team draft profiles

This step reads the season's match and pre-action decision exports once and
writes the team-aware artifacts used by the coach. It does not run during a
chat request.

```bash
python3 analysis/compute_team_draft_profiles.py --league-id 20260003
```

Outputs:

```text
season_teams.jsonl
team_action_tendencies.jsonl
team_opening_sequences.jsonl
team_combo_performance.jsonl
player_hero_pools.jsonl
team_recent_trends.jsonl
```
