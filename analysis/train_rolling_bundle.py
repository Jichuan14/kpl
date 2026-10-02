#!/usr/bin/env python3
"""Retrain a complete rolling production candidate; evaluate before activation.

All tasks consume the same exact chronological series manifest. Neural weights
are rebuilt from scratch. Smoke runs always produce experimental bundles.
"""
from __future__ import annotations
import argparse
import copy
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
ANALYSIS=ROOT/'analysis'
sys.path.insert(0,str(ROOT/'backend'))
from rolling_corpus import build_rolling_manifest, split_keys, validate_sources, DeferredTraining, event_time, exported_battles, standard_battle_keys
from app.services.model_registry import COMPONENTS, atomic_json, install_bundle, resolve_bundle, activate_bundle, REGISTRY_ROOT
from export_production_hero_feature_space import build_feature_space
from train_lineup_value_model import load_trainer
import train_ban_value_model as ban

# Predeclared component criteria. This is predictive/descriptive validation,
# never a claim of causal win or ban value.
CRITERIA={'minimum_eval_series':10,'minimum_policy_decisions':100,
          'minimum_ban_decisions':40,'minimum_lineup_battles':10,
          'policy_nll_max_regression':0.0,'policy_top5_max_regression':0.0,
          'ban_top5_max_regression':0.0,'lineup_logloss_max_regression':0.0}


def command(script: str, *args: str) -> None:
    subprocess.run([sys.executable,str(ANALYSIS/script),*map(str,args)],cwd=ROOT,check=True)


def iter_corpus_rows(manifest: dict, names: tuple[str,...]):
    """Read one decision at a time, preserving split, battle and weight rules."""
    keys=set().union(*(split_keys(manifest,name) for name in names))
    battles=standard_battle_keys(manifest)
    for season,source in manifest['source_files'].items():
        with Path(source['decisions']).open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip(): continue
                row=json.loads(line); key=(season,str(row['match_id']))
                if key in keys and (season,str(row['match_id']),str(row['battle_id'])) in battles:
                    row['_rolling_weight']=manifest['series_weights'][f'{season}:{row["match_id"]}']
                    yield row


def corpus_rows(manifest: dict, names: tuple[str,...]) -> list[dict]:
    return list(iter_corpus_rows(manifest, names))


def train_lineup(manifest: dict, output: Path, trials: int, seed: int) -> dict:
    trainer=load_trainer(); raw, metadata=trainer.load_raw_mechanics(ANALYSIS/'hero_draft_feature_vectors.json')
    battles,teams,heroes=exported_battles(manifest,trainer.V1,splits=tuple(manifest['splits']))
    _training_battles,teams,heroes=exported_battles(manifest,trainer.V1,splits=('train',))
    keys={name:split_keys(manifest,name) for name in manifest['splits']}
    selected={name:[b for b in battles if (b.league_id,b.match_id) in keys[name]] for name in keys}
    if min(len(v) for v in selected.values())<1: raise DeferredTraining('Lineup task lacks complete battles in a split')
    weights=np.asarray([manifest['series_weights'][f'{b.league_id}:{b.match_id}'] for b in selected['train']])
    results=[]; best=None
    for config in trainer.parameter_candidates(trials,seed):
        # Historical state decay has its own selected config. Loss weights apply
        # separately to normalization, objective, gradient, Hessian and intercept.
        state=trainer.make_state(config,raw)
        x,y,_leagues,state=trainer.V1.build_prequential_features(selected['train'],state=state)
        model=trainer.fit_advantage_model(x,y,l2=config['l2'],sample_weights=weights)
        model['feature_names']=list(trainer.FEATURE_NAMES)
        def evaluate(rows):
            features=np.asarray([state.features(b.team_a_id,b.heroes_a,b.team_b_id,b.heroes_b)[0] for b in rows])
            outcomes=np.asarray([b.team_a_won for b in rows])
            return trainer.V1.metrics(outcomes,trainer.V1.predict_probabilities(model,features))
        validation=evaluate(selected['validation']); results.append({'config':config,'validation':validation})
        if best is None or validation['log_loss']<best[0]:
            best=(validation['log_loss'],config,model,copy.deepcopy(state))
    _,config,model,state=best
    def evaluate(rows):
        features=np.asarray([state.features(b.team_a_id,b.heroes_a,b.team_b_id,b.heroes_b)[0] for b in rows])
        return trainer.V1.metrics(np.asarray([b.team_a_won for b in rows]),trainer.V1.predict_probabilities(model,features))
    holdout=evaluate(selected['holdout'])
    source={'league_ids':manifest['source_seasons'],'mode':'rolling','battle_count':len(selected['train']),
            'split_manifest_sha256':manifest['manifest_sha256'],'weighting':manifest['weighting'],
            'training_series':manifest['splits']['train'],'evaluation_weighting':'unweighted',
            'history_decay_is_independent_of_loss_weighting':True}
    payload={'version':trainer.VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),
             'best_config':config,'model':model,'state':state.to_dict(),'raw_mechanics':{str(k):v for k,v in raw.items()},
             'mechanics_metadata':metadata,'team_names':teams,'hero_names':{str(k):v for k,v in heroes.items()},'source':source,
             'warning':'Relative lineup advantage ranking, not causal or calibrated win probability.'}
    atomic_json(output/'lineup_value_model.json',payload)
    atomic_json(output/'lineup_value_validation.json',{'validation':results,'holdout':holdout,'evaluation_weighting':'unweighted'})
    atomic_json(output/'lineup_value_parameter_search.json',{'seed':seed,'trials':results,'best_config':config})
    return holdout


def build_references(manifest: dict, output: Path) -> None:
    work=output/'references'; work.mkdir()
    # Finish and release the corpus stream before starting any child analyzer.
    # Previously the parent retained every parsed decision while each child
    # loaded the same corpus again, exceeding the shared worker memory limit.
    merged=work/'bp_decisions.jsonl'
    inputs=[]
    with ExitStack() as stack:
        destination=stack.enter_context(merged.open('w',encoding='utf-8'))
        season_files={}
        for season in manifest['source_seasons']:
            path=work/season/'bp_decisions.jsonl';path.parent.mkdir()
            season_files[season]=stack.enter_context(path.open('w',encoding='utf-8'))
            inputs += ['--input',str(path)]
        for row in iter_corpus_rows(manifest,('train',)):
            line=json.dumps(row,ensure_ascii=False)+'\n'
            destination.write(line)
            if str(row['league_id']) in season_files:
                season_files[str(row['league_id'])].write(line)
    merged_matches=work/'matches.jsonl'; keys=split_keys(manifest,'train')
    with merged_matches.open('w', encoding='utf-8') as destination:
        for season, source in manifest['source_files'].items():
            with Path(source['matches']).open(encoding='utf-8') as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if (season, str(row['match_id'])) in keys:
                        destination.write(json.dumps(row, ensure_ascii=False) + '\n')
    command('compute_bp_statistics.py','--input',merged,'--output-dir',output)
    command('compute_meta_heroes.py','--input',merged,'--output',output/'meta_hero_stats.jsonl')
    command('compute_team_synergies.py','--input',merged,'--output',output/'team_synergy_stats.jsonl')
    command('compute_team_draft_profiles.py','--decisions',merged,'--matches',merged_matches,'--output-dir',output)
    command('build_draft_model.py',*inputs,'--use-rolling-weights','--recency-decay',.65,'--output',output/'draft_model.json')
    # Reference counts retain existing statistical definitions. Record actual
    # corpus coverage explicitly instead of relabeling mixed rows as target facts.
    atomic_json(output/'reference_coverage.json',{'scope':'rolling_model_reference','series':manifest['splits']['train'],
        'source_seasons':manifest['source_seasons'],'statistical_weighting':'existing unweighted relation definitions','catalog_weighting':manifest['weighting']})
    for path in output.glob('*.jsonl'):
        temporary=path.with_suffix('.jsonl.tmp')
        try:
            with path.open(encoding='utf-8') as source, temporary.open('w',encoding='utf-8') as destination:
                for line in source:
                    if not line.strip():continue
                    row=json.loads(line)
                    row.pop('league_id',None)
                    row.update(evidence_scope='rolling_model_reference',source_seasons=manifest['source_seasons'])
                    destination.write(json.dumps(row,ensure_ascii=False)+'\n')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def finalize_catalog(output: Path, *, manifest: dict | None = None) -> None:
    from app.services.draft_simulator import apply_official_lane_eligibility
    policy=json.loads((output/'personalized_draft_choice_model.json').read_text()); catalog=json.loads((output/'draft_model.json').read_text())
    heroes=set(policy['hero_ids'])
    if manifest is not None:
        # Observed lane evidence is pinned with the training snapshot, never a
        # stale mutable SQL hero-position table. Official entries override it.
        trainer=load_trainer()
        _battles,_teams,observed_names=exported_battles(manifest,trainer.V1)
        positions={}
        keys=split_keys(manifest,'train')
        for season,source in manifest['source_files'].items():
            for line in Path(source['matches']).read_text().splitlines():
                if not line.strip():continue
                match=json.loads(line)
                if (season,str(match['match_id'])) not in keys:continue
                for battle in match['battles']:
                    for player in battle.get('players',[]):
                        if int(player.get('position') or 0) in {2,4,5,6,7}:
                            positions.setdefault(str(player['hero_id']),set()).add(int(player['position']))
        catalog['hero_positions']={hero:sorted(roles) for hero,roles in positions.items()}
        features=json.loads((ANALYSIS/'hero_draft_feature_vectors.json').read_text())
        catalogue_names={str(r['hero_id']):str(r['hero_name']) for r in features['rows']}
        catalog['hero_names']=catalogue_names
    if manifest is not None and manifest.get('mode')=='production_all_data':
        from production_all_data import official_input
        from app.services.draft_simulator import parse_official_lane_ids
        official_rows=json.loads(official_input().read_text())
        apply_official_lane_eligibility(catalog,{int(r['ename']):parse_official_lane_ids(r.get('roles')) for r in official_rows if parse_official_lane_ids(r.get('roles'))})
    else:
        apply_official_lane_eligibility(catalog)
    catalog['hero_ids']=list(policy['hero_ids'])
    for key in ('hero_names','hero_icons','hero_positions'):
        catalog[key]={k:v for k,v in catalog[key].items() if int(k) in heroes}
    for hero in heroes:
        catalog['hero_names'].setdefault(str(hero),policy['base_artifact']['hero_names'][str(hero)])
    atomic_json(output/'draft_model.json',catalog)
    space=build_feature_space(policy,policy['base_artifact']['target_season'],[])
    space['counts_scope']='selected_season_observations_are_supplied_separately'
    atomic_json(output/'learned_hero_feature_space.json',space)
    for filename in ('hero_tactical_roles.json','hero_ability_mechanics.json','hero_draft_feature_vectors.json'):
        shutil.copyfile(ANALYSIS/filename,output/filename)
    if manifest is not None and manifest.get('mode')=='production_all_data':
        from production_all_data import official_input
        shutil.copyfile(official_input(),output/'herolist.json')
    else:
        shutil.copyfile(ROOT/'knowledge/sources/official/herolist.json',output/'herolist.json')


def train_candidate(manifest: dict, output: Path, *, epochs: int=30,trials: int=32,threads: int=1,seed: int=7) -> dict:
    started=time.monotonic(); validate_sources(manifest); output.mkdir(parents=True,exist_ok=False)
    atomic_json(output/'rolling_split_manifest.json',manifest)
    # Statistical catalog is needed by the independent legal-policy evaluator.
    build_references(manifest,output)
    command('train_production_draft_policy.py','--league-id',manifest['target_season'],
            '--rolling-manifest',output/'rolling_split_manifest.json','--output-dir',output,
            '--candidate-only','--epochs',epochs,'--threads',threads,'--seed',seed)
    train_rows=corpus_rows(manifest,('train',)); heldout=corpus_rows(manifest,('holdout',))
    ban_metrics=ban.validate(train_rows,heldout,{})
    ban_payload=ban.serialize_state(ban.build_state(train_rows,{}),manifest['source_seasons'],{},ban_metrics)
    ban_payload['source'].update(mode='rolling',split_manifest_sha256=manifest['manifest_sha256'],weighting=manifest['weighting'],training_series=manifest['splits']['train'])
    atomic_json(output/'ban_value_model.json',ban_payload);atomic_json(output/'ban_value_validation.json',{'holdout':ban_metrics,'evaluation_weighting':'unweighted'})
    lineup_metrics=train_lineup(manifest,output,trials,seed)
    finalize_catalog(output)
    policy_report=json.loads((output/'draft_policy_validation.json').read_text())
    from calibration import load_prediction_records, score_metrics
    calibration=json.loads((output/'personalized_draft_probability_calibration.json').read_text())
    policy_report['calibrated_holdout']=score_metrics(load_prediction_records(output/'sequence_familiarity_training/holdout_predictions.npz'),float(calibration['temperature']))
    report={'status':'EXPERIMENTAL','promotion_eligible':False,'wall_seconds':round(time.monotonic()-started,3),
            'counts':manifest['counts'],'weight_summary':manifest['weight_summary'],
            'weighting':manifest['weighting'],'policy':policy_report,'ban':ban_metrics,'lineup':lineup_metrics,
            'epochs':epochs,'lineup_trials':trials,'criteria':CRITERIA,
            'limitations':[manifest['feature_vintage_limitation'],'Fresh cutoff-trained checkpoints; no all-data refit after calibration.']}
    atomic_json(output/'candidate_report.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,default=ANALYSIS/'outputs/models/candidates')
    p.add_argument('--evaluation-mode',action='store_true',help='Optional historical candidate evaluation; production defaults to all-data')
    p.add_argument('--version');p.add_argument('--cutoff');p.add_argument('--epochs',type=int,default=30)
    p.add_argument('--trials',type=int,default=32);p.add_argument('--threads',type=int,default=1);p.add_argument('--seed',type=int,default=7)
    p.add_argument('--weight-mode',choices=['season_recency','date_half_life','recent_window'],default='season_recency')
    p.add_argument('--decay',type=float,default=.65);p.add_argument('--half-life-days',type=float,default=120)
    p.add_argument('--window-days',type=float,default=180);p.add_argument('--maximum-age-days',type=float)
    p.add_argument('--smoke',action='store_true');p.add_argument('--activate',action='store_true')
    args=p.parse_args()
    if args.smoke and args.activate:p.error('Smoke runs cannot activate')
    if args.epochs<1 or args.trials<1:p.error('Training budgets must be positive')
    if not args.evaluation_mode:
        from production_all_data import production_update
        production_update(args)
        return
    version=args.version or datetime.now(timezone.utc).strftime('rolling-%Y%m%dT%H%M%S')
    config={'mode':args.weight_mode,'decay':args.decay,'half_life_days':args.half_life_days,'window_days':args.window_days,'winning_pick_multiplier':1.0}
    if args.maximum_age_days:config['maximum_age_days']=args.maximum_age_days
    try:
        manifest=build_rolling_manifest(ANALYSIS/'exports',cutoff=args.cutoff,weighting=config)
    except DeferredTraining as error:
        atomic_json(args.output_root/'latest_status.json',{'status':'DEFERRED','reason':str(error),'active_preserved':True});return
    incumbent=resolve_bundle() if (REGISTRY_ROOT/'current.json').is_file() else None
    smoke=args.smoke or args.epochs<30 or args.trials<32
    if not smoke and incumbent:
        start=min(event_time(r['start_time']) for r in manifest['splits']['holdout'])
        cutoff=max(event_time(str(incumbent.manifest[k])) for k in ('parameter_training_cutoff','context_reference_cutoff'))
        if start<=cutoff:
            atomic_json(args.output_root/'latest_status.json',{'status':'DEFERRED','reason':'No adequately supported evaluation beyond incumbent source history','active_preserved':True,'active_model_version':incumbent.version});return
    output=args.output_root/version
    try:
        report=train_candidate(manifest,output,epochs=args.epochs,trials=args.trials,threads=args.threads,seed=args.seed)
    except DeferredTraining as error:
        atomic_json(args.output_root/'latest_status.json',{'status':'DEFERRED','reason':str(error),'active_preserved':True});return
    except Exception as error:
        atomic_json(args.output_root/'latest_status.json',{'status':'FAILED','reason':str(error),'active_preserved':True});raise
    try:
        # Promotion evaluation uses the same future series for the candidate and
        # incumbent, only when those series are later than every incumbent cutoff.
        from rolling_evaluation import promotion_report
        incumbent=resolve_bundle() if (REGISTRY_ROOT/'current.json').is_file() else None
        promotion=promotion_report(output,manifest,report,incumbent,smoke=smoke)
        eligible=promotion['eligible']
        if eligible:
            policy=json.loads((output/'personalized_draft_choice_model.json').read_text());sidecar=json.loads((output/'personalized_draft_probability_calibration.json').read_text())
            policy['status']='production';policy['calibration']['status']='eligible';sidecar['status']='eligible'
            atomic_json(output/'personalized_draft_choice_model.json',policy);atomic_json(output/'personalized_draft_probability_calibration.json',sidecar)
        metadata={'version':version,'source_seasons':manifest['source_seasons'],'promotion_status':'eligible' if eligible else 'experimental',
            'parameter_training_cutoff':max(r['start_time'] for r in manifest['splits']['train']),
            'context_reference_cutoff':min(r['start_time'] for r in manifest['splits']['validation']),
            'split_manifest':manifest,'promotion':promotion,'metrics':report,'limitations':report['limitations'],
            'reference_coverage':json.loads((output/'reference_coverage.json').read_text())}
        handle=install_bundle(output,metadata)
        status={'status':'ELIGIBLE' if eligible else 'DEFERRED','model_version':version,'reason':promotion['reason'],'active_preserved':True,'report':str(output/'candidate_report.json')}
        if args.activate and eligible:
            activate_bundle(handle);status.update(status='ACTIVATED',active_preserved=False)
        atomic_json(args.output_root/'latest_status.json',status)
        print(json.dumps(status,indent=2))
    except Exception as error:
        atomic_json(args.output_root/'latest_status.json',{'status':'FAILED','reason':str(error),'active_preserved':True})
        raise

if __name__=='__main__':main()
