# Hero Team Score proof of concept

This isolated proof of concept reads the existing KPL SQLite database and
writes only inside `poc/team_score/artifacts/`. It does not modify the current
analysis pipeline, generated artifacts, backend, or frontend.

The model produces a zero-sum matchup score:

```text
Team A score = 100 * P(Team A wins)
Team B score = 100 - Team A score
```

The probability comes from four auditable, pre-battle components:

1. `team_strength`: the difference in recency-sensitive Elo ratings.
2. `hero_familiarity`: team/hero performance beyond the team's expected result.
3. `ally_synergy`: league-wide ally-pair performance beyond team expectation.
4. `counter_advantage`: directional hero-vs-hero performance beyond team expectation.

All historical component statistics are computed sequentially. A battle is
scored before its result updates Elo or any hero statistic. Sparse statistics
are shrunk toward zero with configurable evidence priors. Ratings and effective
evidence decay toward neutral at season boundaries (0.65 by default), reducing
stale patch and roster influence.

## Train and validate

From the repository root:

```bash
python3 poc/team_score/team_score_poc.py train
```

This performs rolling chronological validation, with each season evaluated
using only earlier seasons, and writes:

- `artifacts/team_score_model.json`
- `artifacts/validation.json`

The validation compares the full model against an intercept-only baseline, an
Elo-only baseline, and Elo plus each hero component separately. The relevant
release metrics are log loss, Brier score, accuracy, AUC, and calibration
error. The artifact includes a deliberately strict release assessment. This
remains a POC until the full model shows material, repeatable chronological
improvement and acceptable calibration.

## Score a matchup

Team and hero arguments accept IDs or exact names. Team A is treated as camp
1/Blue and Team B as camp 2/Red, so the intercept includes historical side
advantage:

```bash
python3 poc/team_score/team_score_poc.py score \
  --team-a 10001 --heroes-a 140,106,133,193,171 \
  --team-b 10003 --heroes-b 178,136,155,509,537
```

Useful discovery commands:

```bash
python3 poc/team_score/team_score_poc.py list-teams
python3 poc/team_score/team_score_poc.py list-heroes
```

The score response includes component contributions, evidence coverage, and a
confidence label. Unknown or unseen combinations safely fall back to zero
effect rather than inventing evidence.

## Run tests

```bash
python3 -m unittest discover -s poc/team_score/tests -v
```
