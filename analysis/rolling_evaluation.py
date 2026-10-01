"""Fresh candidate/immutable incumbent evaluation and predeclared promotion gates."""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
from app.services.model_registry import BundleHandle, COMPONENTS, bundle_scope, digest, atomic_json, validate_bundle
from app.services.draft_simulator import predict_next_action
from app.services.lineup_value import load_lineup_value_model
from app.services.ban_recommender import load_ban_value_model
from rolling_corpus import event_time, split_keys, exported_battles


def evaluate_bundle(handle: BundleHandle, manifest: dict) -> dict:
    from train_rolling_bundle import corpus_rows
    from train_lineup_value_model import load_trainer
    rows=corpus_rows(manifest,('holdout',)); losses=[]; top5=[]; bans=[]; excluded=[]
    with bundle_scope(handle):
        ban_model=load_ban_value_model(manifest['target_season'])
        for row in rows:
            if row.get('is_peak_battle'):continue
            side=row['side']; other='red' if side=='blue' else 'blue'
            state={'bp_order':int(row['bp_order']),'legal_hero_ids':row['legal_hero_ids'],
                   f'{side}_team_id':str(row['acting_team_id']),f'{other}_team_id':str(row['opponent_team_id'])}
            for key,source in [('picks','current_team_picks'),('bans','current_team_bans'),('used_previous_battles','team_used_in_previous_battles')]:
                state[f'{side}_{key}']=row.get(source,[])
            for key,source in [('picks','current_opponent_picks'),('bans','current_opponent_bans'),('used_previous_battles','opponent_used_in_previous_battles')]:
                state[f'{other}_{key}']=row.get(source,[])
            try:
                policy=predict_next_action(manifest['target_season'],state,model_type='personalized',limit=1000)
                probabilities=policy['next_action_probabilities']; selected=int(row['selected_hero_id'])
                found=[(index,item) for index,item in enumerate(probabilities) if int(item['hero_id'])==selected]
                if not found: raise ValueError('Observed target is outside the bundle candidate vocabulary/legal contract')
                rank,item=found[0];losses.append(-math.log(max(float(item['probability']),1e-15)));top5.append(rank<5)
                if row['action']=='ban':
                    maximum=max(float(item['probability']) for item in probabilities)
                    ordered=sorted(probabilities,key=lambda item:ban_model.score(state=state,next_step=policy['next_step'],hero_id=int(item['hero_id']),policy_probability=float(item['probability']),maximum_policy_probability=maximum)['ban_value'],reverse=True)
                    bans.append(next(index for index,item in enumerate(ordered) if int(item['hero_id'])==selected)<5)
            except ValueError as error:
                excluded.append({'match_id':row['match_id'],'battle_id':row['battle_id'],'bp_order':row['bp_order'],'reason':str(error)})
        trainer=load_trainer();battles,_teams,_heroes=exported_battles(manifest,trainer.V1,splits=('holdout',))
        battles=[b for b in battles if (b.league_id,b.match_id) in split_keys(manifest,'holdout')]
        scorer=load_lineup_value_model(manifest['target_season']);outcomes=[];values=[]
        for battle in battles:
            outcomes.append(battle.team_a_won)
            values.append(scorer.score(battle.team_a_id,battle.heroes_a,battle.team_b_id,battle.heroes_b)['blue_advantage'])
    return {'policy':{'decisions':len(losses),'negative_log_likelihood':float(np.mean(losses)) if losses else None,'top_5_accuracy':float(np.mean(top5)) if top5 else None},
            'ban':{'decisions':len(bans),'top_5_accuracy':float(np.mean(bans)) if bans else None},
            'lineup':trainer.V1.metrics(np.asarray(outcomes),np.asarray(values)),
            'policy_exclusions':excluded,'evaluation_weighting':'unweighted','evaluation_series':manifest['splits']['holdout']}


def promotion_report(output: Path, manifest: dict, training: dict, incumbent: BundleHandle | None, *, smoke: bool=False) -> dict:
    from train_rolling_bundle import CRITERIA
    start=min(r['start_time'] for r in manifest['splits']['holdout'])
    report={'eligible':False,'incumbent_version':incumbent.version if incumbent else None,'evaluation_start':start,
            'evaluation_series':manifest['splits']['holdout'],
            'candidate_model_fingerprint':json.loads((output/'personalized_draft_choice_model.json').read_text())['model_fingerprint'],
            'incumbent_model_fingerprint':json.loads(incumbent.path('personalized_draft_choice_model.json').read_text())['model_fingerprint'] if incumbent else None,
            'criteria':CRITERIA,'checks':{},'reason':''}
    if smoke:
        report['reason']='SMOKE/EXPERIMENTAL budget; never promotion eligible';return report
    if incumbent is None:
        report['reason']='No verified incumbent; initialize a compatible historical seed first';return report
    cutoff=max(event_time(str(incumbent.manifest[k])) for k in ('parameter_training_cutoff','context_reference_cutoff'))
    if event_time(start)<=cutoff:
        report['reason']='Evaluation is not beyond incumbent parameter and context/reference cutoffs';return report
    metadata={'schema_version':1,'version':'candidate-evaluation','source_seasons':manifest['source_seasons'],
              'promotion_status':'experimental','components':{n:digest(output/n) for n in COMPONENTS},
              'parameter_training_cutoff':max(r['start_time'] for r in manifest['splits']['train']),
              'context_reference_cutoff':min(r['start_time'] for r in manifest['splits']['validation'])}
    atomic_json(output/'manifest.json',metadata);validate_bundle(output)
    candidate=BundleHandle(metadata['version'],output,metadata)
    a=evaluate_bundle(candidate,manifest);b=evaluate_bundle(incumbent,manifest)
    report.update(candidate=a,incumbent=b)
    if any(metric[task].get(key) is None for metric in (a,b) for task,key in (("policy","negative_log_likelihood"),("policy","top_5_accuracy"),("ban","top_5_accuracy"))):
        report["reason"]="Insufficient task evaluation support";return report
    support=lambda metric: metric['policy']['decisions']>=CRITERIA['minimum_policy_decisions'] and metric['ban']['decisions']>=CRITERIA['minimum_ban_decisions'] and metric['lineup']['battle_count']>=CRITERIA['minimum_lineup_battles']
    checks={'adequate_support':support(a) and support(b),
            'full_legal_and_vocab_coverage':not a['policy_exclusions'] and not b['policy_exclusions'],
            'familiarity_holdout_gate':bool(training['policy']['familiarity_gate_passed']),
            'policy_nll':a['policy']['negative_log_likelihood']<=b['policy']['negative_log_likelihood'],
            'policy_top5':a['policy']['top_5_accuracy']>=b['policy']['top_5_accuracy'],
            'ban_top5':a['ban']['top_5_accuracy']>=b['ban']['top_5_accuracy'],
            'lineup_logloss':a['lineup']['log_loss']<=b['lineup']['log_loss']}
    report['checks']=checks;report['eligible']=all(checks.values())
    report['reason']='All predeclared component gates passed' if report['eligible'] else 'Candidate did not pass all predeclared component gates'
    return report
