# Analysis

Offline BP analysis scripts and notebooks. Reads from `backend/data/kpl_bp.db`.

## Setup

```bash
# from repo root
python3 analysis/init_heroes.py
```

## Scripts

| File | Purpose |
|---|---|
| `common.py` | Shared DB path, connect, league resolution |
| `init_heroes.py` | Create/seed the `heroes` reference table (`hero_id`, `hero_name`, `hero_icon`) |
| `sync_battle_players.py` | Sync `teams`, `players`, `battle_players` from battle detail API |
| `qa_bp.py` | Per-league BP data QA (completeness, peak candidates, pick reuse) |
| `export_match_data.py` | Export ordered match BP, players, sides, winners, and quality flags to JSONL |
| `build_bp_decisions.py` | Convert match JSONL into one pre-action state per ban/pick |
| `compute_bp_statistics.py` | Compute availability-adjusted response, synergy, counter-pick, and counter-ban statistics |
| `compute_meta_heroes.py` | Rank opening-priority heroes from first-phase bans and Blue first picks |
| `compute_team_synergies.py` | Rank availability-adjusted hero pairs preferred by each team |

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

Defaults to **2026 挑战者杯**. Pass flags for any other season:

```bash
python3 analysis/qa_bp.py
python3 analysis/qa_bp.py --year 2026 --name 挑战者杯
python3 analysis/qa_bp.py --league-id 20260002
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
python3 analysis/sync_battle_players.py --year 2026 --name 挑战者杯
python3 analysis/sync_battle_players.py --league-id 20260002 --battle-limit 5
python3 analysis/sync_battle_players.py --year 2026 --name 挑战者杯 --only-missing
```

### Export analysis data

SQLite stays the source of truth. The exporter writes one complete match per
JSONL line and preserves questionable rows with `quality_flags`.

```bash
# One match
python3 analysis/export_match_data.py --match-id 2026042501

# Entire season
python3 analysis/export_match_data.py --year 2026 --name 挑战者杯
```

### Build BP decision states

Creates one JSONL record per ban/pick with the acting team, side, prior match
usage, current draft, inferred legal hero pool, selected hero/player, and
outcomes. It preserves source quality flags.

```bash
python3 analysis/build_bp_decisions.py
python3 analysis/build_bp_decisions.py \
  --input analysis/exports/20260002/matches.jsonl \
  --output analysis/exports/20260002/bp_decisions.jsonl
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
python3 analysis/compute_meta_heroes.py --league-id 20260002
python3 analysis/compute_meta_heroes.py --league-id 20260001 --min-battles 20
```

Output:

```text
analysis/outputs/{league_id}/meta_hero_stats.jsonl
```

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
