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

### BP QA

Defaults to **2026 挑战者杯**. Pass flags for any other season:

```bash
python3 analysis/qa_bp.py
python3 analysis/qa_bp.py --year 2026 --name 挑战者杯
python3 analysis/qa_bp.py --league-id 20260002
python3 analysis/qa_bp.py --year 2025 --name 挑战者杯 --json-out analysis/outputs/qa_2025_challenger.json
```

### Sync teams / players

Backfills from battles already in the DB (calls battle detail API). Stores raw `camp` plus `match_camp` aligned to match `camp1`/`camp2`.

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
  --input analysis/exports/20260002_matches.jsonl \
  --output analysis/exports/20260002_bp_decisions.jsonl
```
