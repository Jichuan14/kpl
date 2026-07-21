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
| `init_heroes.py` | Create/seed the `heroes` reference table (`hero_id`, `hero_name`, `hero_icon`) |
