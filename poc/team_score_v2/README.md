# Hero Team Score proof of concept — version 2

Version 2 is isolated from both the application and version 1. It reads the
existing SQLite database and hero feature-vector artifact, then writes only to
`poc/team_score_v2/artifacts/`.

The hierarchical synergy representation contains three evidence levels:

1. Composition mechanics: role coverage, damage diversity, control/mobility,
   and sustain/support coverage from the existing hero feature vectors.
2. League pair performance: an ally pair's performance residual beyond the
   participating team's pre-battle Elo expectation.
3. Team pair performance: the same residual for a specific team, with stronger
   shrinkage because these observations are sparse.

The model also retains team Elo, team/hero familiarity, and directional enemy
hero counters. All historical statistics are prequential and season-decayed.

All hierarchical candidates are retained in validation. The scoring artifact
activates only features that beat the version-1-equivalent configuration in
the exploratory rolling comparison. Weak candidates remain visible in the
validation artifact but receive no scoring coefficient.

## Train and validate

```bash
python3 poc/team_score_v2/team_score_v2.py train
```

## Score a completed matchup

Team A is camp 1/Blue and Team B is camp 2/Red:

```bash
python3 poc/team_score_v2/team_score_v2.py score \
  --team-a 10001 --heroes-a 140,106,133,193,171 \
  --team-b 10003 --heroes-b 178,136,155,509,537
```

Version 2 still requires five selected heroes per side. Partial-draft current
and projected scoring remains a separate follow-up.

## Test

```bash
python3 -m unittest discover -s poc/team_score_v2/tests -v
```
