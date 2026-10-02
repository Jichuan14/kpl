#!/usr/bin/env python3
"""Validate and snapshot the existing historical production collection unchanged."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services.model_registry import COMPONENTS, install_bundle, activate_bundle, atomic_json
from app.services.draft_simulator import load_model
from export_production_hero_feature_space import build_feature_space


def stage_collection(source: Path, stage: Path) -> dict:
    policy=json.loads((source/'personalized_draft_choice_model.json').read_text())
    if policy.get('status') != 'production':
        raise ValueError('Seed requires an existing production policy; candidates and smoke artifacts cannot seed')
    for filename in COMPONENTS:
        origin=(ROOT/'analysis'/filename) if filename in {'hero_tactical_roles.json','hero_ability_mechanics.json','hero_draft_feature_vectors.json'} else (ROOT/'knowledge/sources/official/herolist.json') if filename=='herolist.json' else source/filename
        if filename not in {'draft_model.json','learned_hero_feature_space.json'}:
            shutil.copyfile(origin,stage/filename)
    # Apply the same verified official lane definitions used by legacy serving.
    catalog={k:v for k,v in load_model(source.name).items() if not k.startswith('_')}
    heroes=set(map(int,policy['hero_ids']))
    catalog['hero_ids']=[h for h in catalog['hero_ids'] if int(h) in heroes]
    for key in ['hero_names','hero_icons','hero_positions']:
        catalog[key]={k:v for k,v in catalog[key].items() if int(k) in heroes}
    atomic_json(stage/'draft_model.json',catalog)
    space=build_feature_space(policy,str(policy['base_artifact']['target_season']),[])
    space['counts_scope']='selected_season_observations_are_supplied_separately'
    atomic_json(stage/'learned_hero_feature_space.json',space)
    context=json.loads((source/'player_draft_context.json').read_text())
    policy_seasons=list(policy['base_artifact'].get('training',{}).get('training_seasons') or context['source_seasons'])
    component_sources={name:json.loads((source/name).read_text()).get('source',{}) for name in ('ban_value_model.json','lineup_value_model.json')}
    seasons=sorted(set(policy_seasons).union(*(set(value.get('league_ids',[])) for value in component_sources.values())))
    from rolling_corpus import event_time
    complete_times=[]
    for season in seasons:
        for line in (ROOT/'analysis/exports'/season/'matches.jsonl').read_text().splitlines():
            match=json.loads(line)
            if match.get('match_winner_team_id'):
                complete_times.append(event_time(match['start_time']))
    source_upper_bound=max(complete_times).isoformat()
    return {'source_seasons':seasons,'promotion_status':'seed',
            'parameter_training_cutoff':source_upper_bound,
            'context_reference_cutoff':source_upper_bound,
            'cutoff_interpretation':'Conservative source-history upper bound; imported lineage cannot establish per-component clean holdout.',
            'component_provenance':{'policy_source_seasons':policy_seasons,**component_sources},
            'legacy_validation':{'base_training':policy['base_artifact'].get('training',{}),'calibration':json.loads((source/'personalized_draft_probability_calibration.json').read_text())},
            'seed_source':str(source.resolve()),
            'limitations':['Imported historical production collection; its upstream reference history is already seen.',
                           'Existing validation lineage is retained; seed import does not prove current-season validity.',
                           'Catalog is restricted to the existing policy vocabulary and verified official lane definitions.',
                           'Historical hero-feature vintages are unavailable.'],
            'model_fingerprint':policy['model_fingerprint'],
            'split_manifest_sha256':policy['split_manifest_sha256']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-season',default='20260003')
    parser.add_argument('--version',required=True)
    parser.add_argument('--registry-root',type=Path)
    parser.add_argument('--browser-root',type=Path)
    parser.add_argument('--activate',action='store_true')
    args=parser.parse_args()
    if not args.source_season.isdigit(): parser.error('Invalid season')
    with TemporaryDirectory(prefix='draft-seed-') as tmp:
        stage=Path(tmp); metadata=stage_collection(ROOT/'analysis/outputs'/args.source_season,stage)
        handle=install_bundle(stage,{'version':args.version,**metadata},registry_root=args.registry_root)
    if args.activate: activate_bundle(handle,registry_root=args.registry_root,browser_root=args.browser_root)
    print(json.dumps(handle.metadata(),ensure_ascii=False,indent=2))

if __name__=='__main__': main()
