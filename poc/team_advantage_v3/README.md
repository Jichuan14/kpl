# Team Advantage Score V3

This isolated experiment combines:

- recency-sensitive team Elo;
- team performance with each selected hero;
- historical league and team ally-pair performance;
- historical directional hero counters;
- mechanics-based role coverage;
- mechanics-based ally compatibility; and
- mechanics-based directional counter interactions.

The tuner uses the first five seasons for deterministic parameter search and
keeps `20260003` untouched as the final test season. Because this is an
advantage-ranking score, “optimal” means the best tested configuration by
development AUC, with log loss as the tie-breaker. It is not a claim of a
global optimum.

Every component is constrained to a nonnegative coefficient. A signal labeled
as an advantage can therefore become neutral when unsupported, but can never
silently reverse meaning in the displayed explanation.

```bash
python3 poc/team_advantage_v3/team_advantage_v3.py tune --trials 96
```

Score a completed five-versus-five matchup:

```bash
python3 poc/team_advantage_v3/team_advantage_v3.py score \
  --team-a 10001 --heroes-a 140,106,133,193,171 \
  --team-b 10003 --heroes-b 178,136,155,509,537
```

Team A is camp 1/Blue. The output is labeled as an advantage score rather than
a calibrated win probability.

```bash
python3 -m unittest discover -s poc/team_advantage_v3/tests -v
```
