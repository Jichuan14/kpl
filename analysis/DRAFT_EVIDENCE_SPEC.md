# Historical draft evidence contract

Schema version: **1**

This artifact describes evidence around an observed ban or pick. It does not
name a coach's intention, estimate an intention probability, or make a causal
claim. No language model or trained BP model participates in generation.

## Corpus and modes

`retrospective_all_available` is the product default. It uses every compatible
local season, including matches later than the inspected move. Every battle in
the inspected match is removed from that move's supporting sample.

`as_of_target_match` is the prospective evaluation mode. It additionally
removes matches whose start time is equal to or later than the inspected
match. Match start time is the available chronology boundary.

The corpus manifest lists every included league and hashes both source files.
Hero identity is the exact numeric hero ID; variants are never merged.

## Compatibility and denominators

The denominator is one compatible historical draft action. Comparisons must
agree on action type, side, BP order, 18/20-action schedule, game number, and
visible own/opponent pick counts. Estimates are first calculated per season.
The pooled control rate weights each season by the number of observed selected
actions in that season, so a large control pool cannot silently determine the
result. Seasons without both the selected action and another legal action are
reported but do not enter the adjusted difference.

The target must be pick-legal for the relevant team immediately before the
historical action. Later bans, steals, and missed picks remain failures in the
denominator. A control that picks the target at the trigger is excluded because
it mechanically removes the target.

## Evidence states

- `structural`: follows directly from BP rules, such as a ban removing access.
- `descriptive`: compatible selected-action and alternative-action observations
  overlap in at least one season.
- `insufficient`: no compatible within-season overlap exists.

Contradiction means the adjusted continuation difference points opposite the
candidate reading. `season_direction_disagreement` means supported seasons do
not share a direction. Neither field is a confidence or intent score.

## Dossier fields

Each move records its exact pre-action bans, picks, own and opponent picks,
schedule, order, side, teams, and inferred legal-pool size. Candidate
continuations are selected without reading the inspected battle's suffix: for
each side, the builder chooses the most common later pick in the compatible
evidence corpus. The inspected battle's actual follow-through is displayed
separately because it was not visible when the move was made.

Each continuation item retains per-season selected and alternative counts,
rates, action-level Wilson intervals, overlap, exclusions, adjusted rates,
heterogeneity flags, and a claim limit. The availability label is
`inferred_from_exported_legal_pool`: the repository has no patch-certified hero
availability timeline, so the artifact does not claim one.

Winner, battle result, player performance, and final lineup fields do not
select, weight, or score evidence.

## Artifacts

Shared corpus provenance:

```text
analysis/outputs/draft_evidence/{corpus_id}/manifest.json
analysis/outputs/draft_evidence/{corpus_id}/coverage.json
```

Target-season dossiers:

```text
analysis/outputs/{league_id}/draft_evidence/manifest.json
analysis/outputs/{league_id}/draft_evidence/matches/{match_id}.json
analysis/outputs/{league_id}/draft_evidence_validation.json
```

The corpus ID hashes source fingerprints and build configuration. Published
match shards must have the same corpus ID as their manifest.

## Simulator presentation

The BP simulator requests evidence after each completed move using the exact
pre-action board, BP schedule, game number, side, order, current-season hero
pool, and Global BP history. The server compares that synthetic move with all
compatible local-season battles. Because the simulator move is not a historical
match, target-match exclusion is not applicable; the simulated move is never
part of its supporting sample. The response remains model-free and contains no
language-model interpretation.
