"""Fixed-recipe fresh production refits, with no evaluation reservation or search."""
from __future__ import annotations
import copy
import os
from tempfile import TemporaryDirectory
from datetime import timedelta, datetime, timezone
import json
from pathlib import Path
import time
import sys
from uuid import uuid4
import numpy as np
from rolling_corpus import build_rolling_manifest, training_context_games, event_time, exported_battles, validate_sources
from sequence_training.splits import canonical_sha256, file_sha256
from app.services.model_registry import atomic_json, install_bundle, activate_bundle, resolve_bundle, REGISTRY_ROOT
from app.services.draft_calibration import candidate_policy_fingerprint
from train_rolling_bundle import ROOT, ANALYSIS, command, corpus_rows, iter_corpus_rows, build_references, finalize_catalog
import train_ban_value_model as ban
sys.path.insert(0,str(ANALYSIS/"sequence_training"))
from train_lineup_value_model import load_trainer

LINEUP_CONFIG={'elo_k':16.,'season_decay':.55,'familiarity_prior':64.,'synergy_prior':96.,'counter_prior':40.,'team_pair_prior':30.,'l2':12.}
RECIPE={'version':'all-data-v1','epochs':30,'hidden_dim':48,'seed':7,'winning_pick_multiplier':1.,'season_recency':.65,'lineup_config':LINEUP_CONFIG,
        'calibration':'uncalibrated_temperature_1','familiarity_context':'strictly_earlier_China_dates','serving_context':'next_China_date_after_latest_finished_series'}
OFFICIAL_CACHE=ANALYSIS/'outputs/models/inputs/herolist.json'

def official_input():
    return OFFICIAL_CACHE if OFFICIAL_CACHE.is_file() else ROOT/'knowledge/sources/official/herolist.json'

def maintained_inputs():
    return [ANALYSIS/n for n in ('hero_draft_feature_vectors.json','hero_tactical_roles.json','hero_ability_mechanics.json')]+[official_input()]

def refresh_official_catalogue():
    import httpx
    from app.services.draft_simulator import parse_official_lane_ids
    response=httpx.get('https://pvp.qq.com/web201605/js/herolist.json',timeout=10.,follow_redirects=True)
    response.raise_for_status();rows=response.json()
    if not isinstance(rows,list) or not rows or any(not isinstance(r,dict) or not str(r.get('ename','')).isdigit() or not r.get('cname') for r in rows):
        raise ValueError('Official hero catalogue has an invalid schema')
    if len({int(r['ename']) for r in rows})!=len(rows):raise ValueError('Official hero catalogue contains duplicate identities')
    atomic_json(OFFICIAL_CACHE,rows)
    return rows


def ensure_hero_vocabulary(manifest):
    """Expand only missing IDs; unknown traits are explicitly unavailable."""
    feature_path=ANALYSIS/'hero_draft_feature_vectors.json'
    artifact=json.loads(feature_path.read_text());existing={int(r['hero_id']) for r in artifact['rows']}
    needed=set(); names={}
    for row in iter_corpus_rows(manifest, ('train',)):
        if row.get('is_peak_battle'): continue
        hero=int(row['selected_hero_id']); needed.add(hero)
        needed.update(int(h) for h in row.get('legal_hero_ids', []))
        names[hero]=str(row.get('selected_hero_name') or hero)
    official={int(r['ename']):r for r in json.loads(official_input().read_text())}
    observed_roles={}
    keys={(str(r['season']),str(r['match_id'])) for r in manifest['splits']['train']}
    for season,source in manifest['source_files'].items():
        for line in Path(source['matches']).read_text().splitlines():
            if not line.strip():continue
            match=json.loads(line)
            if (season,str(match['match_id'])) not in keys:continue
            for battle in match['battles']:
                for player in battle.get('players',[]):
                    if int(player.get('position') or 0) in {2,4,5,6,7}:observed_roles.setdefault(int(player['hero_id']),set()).add(int(player['position']))
    from app.services.draft_simulator import parse_official_lane_ids
    missing={h for h in needed if not observed_roles.get(h) and not parse_official_lane_ids(official.get(h,{}).get('roles'))}
    if missing:
        try:official={int(r['ename']):r for r in refresh_official_catalogue()}
        except Exception as error:raise ValueError(f'Hero lane information unavailable for {sorted(missing)}; official catalogue refresh failed: {error}') from error
        unresolved={h for h in missing if not parse_official_lane_ids(official.get(h,{}).get('roles'))}
        if unresolved:raise ValueError(f'Official catalogue has no verified lane for heroes {sorted(unresolved)}')
    for hero in sorted(needed-existing):
        row=official.get(hero,{})
        artifact['rows'].append({'hero_id':hero,'hero_name':str(row.get('cname') or names.get(hero) or hero),'feature_known':False,'vector':[0.]*len(artifact['feature_names']),
            'missing_traits':True,'identity_source':'official_hero_catalogue' if row else 'pinned_completed_series','feature_limitation':'No verified specialty or skill-mechanics traits; neutral unknown encoding.'})
    if needed-existing:
        artifact['rows'].sort(key=lambda r:int(r['hero_id']))
        artifact.setdefault('coverage',{})['hero_count']=len(artifact['rows'])
        artifact['coverage']['explicit_unknown_trait_heroes']=[int(r['hero_id']) for r in artifact['rows'] if r.get('missing_traits')]
        atomic_json(feature_path,artifact)
    return sorted(needed-existing)


def retrain_identity(manifest, recipe=RECIPE):
    """Hash eligible semantic records; upcoming/incomplete fixtures do not count."""
    keys={(str(r['season']),str(r['match_id'])) for r in manifest['splits']['train']}
    matches=[]
    for season,source in manifest['source_files'].items():
        for line in Path(source['matches']).read_text().splitlines():
            if not line.strip():continue
            row=json.loads(line)
            if (season,str(row['match_id'])) not in keys:continue
            row=copy.deepcopy(row);row['start_time']=event_time(row['start_time']).isoformat()
            row['teams']=sorted(row['teams'],key=lambda t:int(t['match_camp']))
            row['battles']=sorted(row['battles'],key=lambda b:int(b['battle_seq']))
            for battle in row['battles']:
                battle['players']=sorted(battle['players'],key=lambda p:(int(p['camp']),int(p.get('position') or 0),str(p.get('player_name') or '')))
                if 'bp_actions' in battle:battle['bp_actions']=sorted(battle['bp_actions'],key=lambda a:int(a['order']))
            matches.append({'season':season,'match':row})
    decisions=corpus_rows(manifest,('train',))
    decisions.sort(key=lambda r:(str(r['league_id']),str(r['match_id']),str(r['battle_id']),int(r['bp_order'])))
    maintained={str(p.relative_to(ROOT)):canonical_sha256(json.loads(p.read_text())) for p in maintained_inputs()}
    identity={'matches':sorted(matches,key=lambda r:(r['season'],str(r['match']['match_id']))),'decisions':decisions,'maintained_inputs':maintained,'recipe':recipe}
    import hashlib
    digest=hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':')).iterencode(identity):
        digest.update(chunk.encode())
    return digest.hexdigest(),maintained


def run_production_stage(stage, manifest, output, *, epochs=30, threads=1, seed=7):
    # Each stage exits before the next one starts, returning allocator arenas,
    # tensors, imported ML libraries and corpus objects to the operating system.
    with TemporaryDirectory(prefix='kpl-stage-') as directory:
        directory=Path(directory);manifest_path=directory/'manifest.json';result_path=directory/'result.json'
        atomic_json(manifest_path, manifest)
        print(f'[production-stage] start {stage}', flush=True)
        command('production_training_stage.py', '--stage', stage, '--manifest', manifest_path,
                '--output', output, '--result', result_path, '--epochs', epochs,
                '--threads', threads, '--seed', seed)
        print(f'[production-stage] complete {stage}', flush=True)
        return json.loads(result_path.read_text())


def release_unused_memory():
    """Return freed preparation arenas where Linux/glibc supports it."""
    import gc
    gc.collect()
    if sys.platform.startswith('linux'):
        import ctypes
        try:
            trim = ctypes.CDLL(None).malloc_trim
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
        except AttributeError:
            pass  # Other libc implementations still benefit from stage exit.


def train_base(manifest, output, epochs, threads, seed):
    import torch
    from models import prepare_data,ModelConfig,BagAblationModel,HybridBagGRUModel,train_model,save_checkpoint,seed_everything
    torch.set_num_threads(threads);device=torch.device('cpu');work=output/'sequence_familiarity_training';work.mkdir()
    data=prepare_data(ROOT,target_season=manifest['target_season'],previous_seasons=0,validation_matches=0,holdout_matches=0,holdout_offset_matches=0,recency_decay=.65,winning_pick_weight=1.,split_manifest=manifest)
    expected=sum(not r.get('is_peak_battle') for r in iter_corpus_rows(manifest, ('train',)))
    if len(data.train)!=expected:
        raise ValueError(f'Neural coverage mismatch: {len(data.train)} of {expected} eligible decisions; unknown heroes or malformed action context require input repair')
    release_unused_memory()
    config=ModelConfig(hero_count=len(data.hero_ids),team_count=len(data.team_ids),feature_width=int(data.hero_features.shape[1]),hidden_dim=48,use_series_context=True)
    bag=None;history={}
    for offset,kind in enumerate((BagAblationModel,HybridBagGRUModel)):
        seed_everything(seed+offset);model=kind(config,data.hero_features)
        if bag is not None:model.initialize_from_bag(bag)
        model,hist,_,_=train_model(model,data.train,None,None,epochs=epochs,batch_size=getattr(model,'recommended_batch_size',256),learning_rate=getattr(model,'recommended_learning_rate',.003),weight_decay=.0001,device=device,seed=seed+offset)
        history[model.model_name]=hist;save_checkpoint(work/f'{model.model_name}.pt',model,data,{})
        if offset==0:bag=model
    experiment={'training_seasons':manifest['source_seasons'],'season_weights':data.season_weights,'train_decisions':len(data.train),'config':{'split_manifest':str(output/'rolling_split_manifest.json')},'release_refit':{'trained_on_all_available_matches':True,'train_decisions':len(data.train)}}
    atomic_json(work/'results.json',experiment)
    return {'training_decisions': len(data.train)}


def train_familiarity(manifest, output, epochs, threads, seed):
    import torch
    from models import prepare_data,load_checkpoint,seed_everything
    from personalized_models import FamiliarityResidual
    from train_personalized_draft_choice_model import extend,source_rows
    from player_context.history import TemporalContextBuilder
    torch.set_num_threads(threads);device=torch.device('cpu');work=output/'sequence_familiarity_training'
    data=prepare_data(ROOT,target_season=manifest['target_season'],previous_seasons=0,validation_matches=0,holdout_matches=0,holdout_offset_matches=0,recency_decay=.65,winning_pick_weight=1.,split_manifest=manifest)
    release_unused_memory()
    model=load_checkpoint(work/'hybrid_bag_gru.pt',device)
    games=training_context_games(manifest);builder=TemporalContextBuilder(games,data.hero_ids)
    train=extend(data.train,source_rows(manifest['source_seasons']),builder)
    del games, builder
    release_unused_memory()
    seed_everything(seed);branch=FamiliarityResidual();optimizer=torch.optim.AdamW(branch.parameters(),lr=.001,weight_decay=.0001);generator=torch.Generator().manual_seed(seed)
    model.eval()
    for epoch in range(epochs):
        branch.train();order=torch.randperm(len(train),generator=generator)
        for start in range(0,len(train),128):
            batch=train.batch(order[start:start+128],device);optimizer.zero_grad(set_to_none=True)
            with torch.no_grad():base=model(batch)
            logits=branch(base,batch['candidate_features'],batch['next_actions'],batch['legal_mask'])
            losses=torch.nn.functional.cross_entropy(logits,batch['targets'],reduction='none');loss=(losses*batch['sample_weights']).sum()/batch['sample_weights'].sum()+branch.regularization_loss()
            loss.backward();torch.nn.utils.clip_grad_norm_(branch.parameters(),1.);optimizer.step()
        print(f'all-data familiarity epoch {epoch+1}/{epochs}',flush=True)
    torch.save({'schema_version':1,'model_type':branch.model_name,'config':{'feature_width':9},'state_dict':branch.state_dict(),'hero_ids':data.hero_ids,'team_ids':data.team_ids,'player_vocab':{},'split_manifest_sha256':manifest['manifest_sha256']},work/'familiarity.pt')
    return {'training_decisions': len(data.train)}


def train_neural(manifest, output, epochs, threads, seed):
    work=output/'sequence_familiarity_training'
    base=run_production_stage('base', manifest, output, epochs=epochs, threads=threads, seed=seed)
    command('export_sequence_draft_choice_model.py','--league-id',manifest['target_season'],'--checkpoint',work/'hybrid_bag_gru.pt','--experiment-results',work/'results.json','--output',work/'sequence_base.json')
    familiarity=run_production_stage('familiarity', manifest, output, epochs=epochs, threads=threads, seed=seed)
    if familiarity['training_decisions'] != base['training_decisions']:
        raise ValueError('Base and familiarity training coverage differs')
    command('export_personalized_draft_choice_model.py','--checkpoint',work/'familiarity.pt','--base-artifact',work/'sequence_base.json','--output',output/'personalized_draft_choice_model.json')
    # Existing contexts use dates strictly before the cutoff. The next local
    # date includes every latest-day finished series, without adding future games.
    local=timezone(timedelta(hours=8));asof=(max(event_time(r['start_time']).astimezone(local).date() for r in manifest['splits']['train'])+timedelta(days=1)).isoformat()
    command('export_player_draft_context.py','--split-manifest',output/'rolling_split_manifest.json','--source-seasons',','.join(manifest['source_seasons']),'--hero-ids',output/'personalized_draft_choice_model.json','--context-as-of',asof,'--output',output/'player_draft_context.json')
    return {'training_decisions':base['training_decisions'],'epochs_each_stage':epochs,'serving_context_as_of_exclusive_China_date':asof}


def train_ban(manifest, output):
    rows=corpus_rows(manifest,('train',));payload=ban.serialize_state(ban.build_state(rows,{}),manifest['source_seasons'],{},{})
    payload['source'].update(mode='production_all_data',split_manifest_sha256=manifest['manifest_sha256'],training_series=manifest['splits']['train'],weighting=manifest['weighting']);atomic_json(output/'ban_value_model.json',payload)
    return {'ban_decisions': payload['source']['ban_decisions']}


def train_lineup_all_data(manifest, output):
    trainer=load_trainer();raw,metadata=trainer.load_raw_mechanics(ANALYSIS/'hero_draft_feature_vectors.json');battles,teams,heroes=exported_battles(manifest,trainer.V1)
    state=trainer.make_state(LINEUP_CONFIG,raw);x,y,_,state=trainer.V1.build_prequential_features(battles,state=state)
    weights=np.asarray([manifest['series_weights'][f'{b.league_id}:{b.match_id}'] for b in battles]);fitted=trainer.fit_advantage_model(x,y,l2=LINEUP_CONFIG['l2'],sample_weights=weights);fitted['feature_names']=list(trainer.FEATURE_NAMES)
    atomic_json(output/'lineup_value_model.json',{'version':trainer.VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),'best_config':LINEUP_CONFIG,'model':fitted,'state':state.to_dict(),'raw_mechanics':{str(k):v for k,v in raw.items()},'mechanics_metadata':metadata,'team_names':teams,'hero_names':{str(k):v for k,v in heroes.items()},'source':{'mode':'production_all_data','training_series':manifest['splits']['train'],'split_manifest_sha256':manifest['manifest_sha256'],'weighting':manifest['weighting'],'battle_count':len(battles)}})
    return {'lineup_battles': len(battles)}


def train_all_data(manifest,output,*,epochs=30,threads=1,seed=7):
    started=time.monotonic();validate_sources(manifest);output.mkdir(parents=True,exist_ok=False)
    atomic_json(output/'rolling_split_manifest.json',manifest);run_production_stage('references', manifest, output)
    policy_metrics=train_neural(manifest,output,epochs,threads,seed)
    ban_metrics=run_production_stage('ban', manifest, output)
    lineup_metrics=run_production_stage('lineup', manifest, output)
    run_production_stage('catalog', manifest, output)
    policy=json.loads((output/'personalized_draft_choice_model.json').read_text());catalog=json.loads((output/'draft_model.json').read_text())
    policy.update(status='production_all_data',training_series=manifest['splits']['train']);policy['calibration']={'status':'uncalibrated','temperature':1.,'reason':'all_data_refit_has_no_independent_calibration'};atomic_json(output/'personalized_draft_choice_model.json',policy)
    atomic_json(output/'personalized_draft_probability_calibration.json',{'schema_version':1,'method':'none','policy_model_type':'personalized','status':'uncalibrated','temperature':1.,'model_fingerprint':policy['model_fingerprint'],'candidate_policy_id':'game_availability_v1','candidate_policy_fingerprint':candidate_policy_fingerprint('game_availability_v1',availability_config={'role_ids':catalog['role_ids'],'global_bp_previous_game_pick_exclusion':True,'ban_uses_opponent_open_roles':False}),'context_contract_version':policy['context_contract_version'],'split_manifest_sha256':manifest['manifest_sha256'],'calibration_match_ids':[],'reason':'all_data_refit_has_no_independent_calibration'})
    context=json.loads((output/'player_draft_context.json').read_text());context['training_series']=manifest['splits']['train'];context['split_manifest_sha256']=manifest['manifest_sha256'];atomic_json(output/'player_draft_context.json',context)
    report={'status':'SMOKE/EXPERIMENTAL' if epochs<30 else 'PRODUCTION_ALL_DATA','counts':manifest['counts'],'excluded_series_count':len(manifest['excluded_series']),'weight_summary':manifest['weight_summary'],'policy':policy_metrics,'ban_decisions':ban_metrics['ban_decisions'],'lineup_battles':lineup_metrics['lineup_battles'],'lineup_config':LINEUP_CONFIG,'calibration':'uncalibrated_temperature_1','wall_seconds':round(time.monotonic()-started,3),'limitations':[manifest['feature_vintage_limitation'],'All-data production fit; no held-out quality or calibration claim.']}
    atomic_json(output/'candidate_report.json',report);return report


def production_update(args):
    from memory_telemetry import record
    previous = os.environ.get('KPL_MEMORY_LOG')
    args.output_root.mkdir(parents=True, exist_ok=True)
    if previous is None:
        os.environ['KPL_MEMORY_LOG'] = str(args.output_root / 'training_memory.jsonl')
    record('update_start', 'production_all_data')
    try:
        return _production_update(args)
    except BaseException:
        record('update_failed', 'production_all_data')
        raise
    finally:
        record('update_end', 'production_all_data')
        if previous is None:
            os.environ.pop('KPL_MEMORY_LOG', None)


def _production_update(args):
    status_path=args.output_root/'latest_status.json'
    try:
        if args.weight_mode!='season_recency' or args.decay!=.65 or args.maximum_age_days or args.cutoff:
            raise ValueError('Production uses all available complete series with established .65 weights; use --evaluation-mode for research windows')
        manifest=build_rolling_manifest(ANALYSIS/'exports',production_all_data=True)
        prepared=run_production_stage('inputs', manifest, args.output_root, epochs=args.epochs, threads=args.threads, seed=args.seed)
        identity,inputs,raw_inputs=prepared['identity'],prepared['inputs'],prepared['raw_inputs']
        incumbent=resolve_bundle() if (REGISTRY_ROOT/'current.json').is_file() else None
        if incumbent and incumbent.manifest.get('promotion_status')=='production_all_data' and incumbent.manifest.get('retrain_identity')==identity:
            status={'status':'NO_CHANGE','active_model_version':incumbent.version,'reason':'Eligible corpus, maintained inputs and recipe are unchanged','active_preserved':True}
        else:
            smoke=args.smoke or args.epochs<30
            if smoke and args.activate:raise ValueError('Low-budget all-data smoke runs cannot activate')
            version=args.version or datetime.now(timezone.utc).strftime('all-data-%Y%m%dT%H%M%S-')+uuid4().hex[:8]
            output=args.output_root/version;report=train_all_data(manifest,output,epochs=args.epochs,threads=args.threads,seed=args.seed)
            validate_sources(manifest)
            verified=run_production_stage('identity', manifest, output, epochs=args.epochs, threads=args.threads, seed=args.seed)
            if verified['raw_inputs']!=raw_inputs or verified['identity']!=identity:raise ValueError('Maintained inputs changed during training')
            cutoff=max(r['start_time'] for r in manifest['splits']['train'])
            metadata={'version':version,'promotion_status':'experimental' if smoke else 'production_all_data','training_contract':'production_all_data','source_seasons':manifest['source_seasons'],'parameter_training_cutoff':cutoff,'context_reference_cutoff':cutoff,'split_manifest':manifest,'retrain_identity':identity,'maintained_inputs':inputs,'maintained_input_raw_sha256':raw_inputs,'recipe':{**RECIPE,'seed':args.seed,'epochs':args.epochs},'metrics':report,'limitations':report['limitations'],'reference_coverage':json.loads((output/'reference_coverage.json').read_text()),'promotion':{'incumbent_version':incumbent.version if incumbent else None,'incumbent_model_fingerprint':json.loads(incumbent.path('personalized_draft_choice_model.json').read_text())['model_fingerprint'] if incumbent else None}}
            # Smoke still exercises full immutable server validation, with a
            # separate experimental registry; it never touches production.
            handle=install_bundle(output,metadata,registry_root=args.output_root/'smoke-registry' if smoke else None)
            status={'status':'SMOKE/EXPERIMENTAL' if smoke else 'TRAINED','model_version':version,'reason':'Fresh all-data fixed-recipe fit; probabilities uncalibrated','active_preserved':True,'report':str(output/'candidate_report.json')}
            if args.activate:activate_bundle(handle);status.update(status='ACTIVATED',active_preserved=False)
        atomic_json(status_path,status);print(json.dumps(status,indent=2));return status
    except Exception as error:
        atomic_json(status_path,{'status':'FAILED','reason':str(error),'active_preserved':True});raise
