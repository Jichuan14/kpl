"""Small reproducible bundles; no generated production artifacts required."""
import json
from pathlib import Path
import sys
import numpy as np
from app.services.model_registry import COMPONENTS, atomic_json
from app.services.draft_calibration import canonical_sha256, candidate_policy_fingerprint
from app.services.lineup_value import FEATURE_NAMES, ALLY_RULES, COUNTER_RULES


def write_bundle_source(root: Path, *, bias: float = 0.0):
    analysis=Path(__file__).resolve().parents[2]/'analysis'
    sys.path.insert(0,str(analysis));sys.path.insert(0,str(analysis/'sequence_training'))
    from models import ModelConfig, HybridBagGRUModel
    from personalized_models import FamiliarityResidual
    from export_production_hero_feature_space import build_feature_space
    import torch
    heroes=list(range(105,117)); names={str(h):str(h) for h in heroes}
    features={'feature_names':['control__strong'],'rows':[{'hero_id':h,'vector':[0.0],'feature_known':True} for h in heroes]}
    matrix=torch.tensor([[0.,1.] for h in heroes])
    config=ModelConfig(hero_count=len(heroes),team_count=2,feature_width=2,hidden_dim=2)
    torch.manual_seed(3);model=HybridBagGRUModel(config,matrix)
    from app.services.sequence_model_runtime import _BAG_KEYS, _GRU_KEYS
    state=model.state_dict()
    params={'residual_scale_logit':float(state['residual_scale_logit']),
            **{branch:{k[len(branch)+1:]:v.tolist() for k,v in state.items() if k.startswith(branch+'.') and k[len(branch)+1:] in (_BAG_KEYS if branch=='bag' else _GRU_KEYS)} for branch in ('bag','gru')}}
    params['bag']['hero_bias'][0]+=bias
    base={'schema_version':1,'model_type':'frozen_bag_gru_residual_choice','target_season':'A',
          'config':vars(config),'hero_ids':heroes,'team_ids':['blue','red'],'feature_names':['control__strong','feature_known'],
          'parameters':params,'parameters_sha256':canonical_sha256(params),'hero_names':names}
    residual=FamiliarityResidual();parameters={k:v.detach().tolist() for k,v in residual.state_dict().items()}
    identity={'candidate_kind':'familiarity_residual','config':{'feature_width':9},'hero_ids':heroes,'team_ids':['blue','red'],
              'player_vocab':{},'parameters':parameters,'base_parameters_sha256':base['parameters_sha256'],'split_manifest_sha256':'fixture-split'}
    policy={'schema_version':1,'model_type':'sequence_familiarity_residual_choice','status':'production',**identity,
            'model_fingerprint':canonical_sha256(identity),'base_artifact':base,'hero_feature_matrix':matrix.tolist(),
            'context_contract_version':'player_context_v1','generated_at':'fixture'}
    roles=[2,4,5,6,7]
    calibration={'schema_version':1,'method':'global_temperature','policy_model_type':'personalized','status':'eligible',
                 'temperature':1.,'model_fingerprint':policy['model_fingerprint'],'candidate_policy_id':'game_availability_v1',
                 'candidate_policy_fingerprint':candidate_policy_fingerprint('game_availability_v1',availability_config={'role_ids':roles,'global_bp_previous_game_pick_exclusion':True,'ban_uses_opponent_open_roles':False}),
                 'context_contract_version':'player_context_v1','split_manifest_sha256':'fixture-split'}
    context={'schema_version':1,'context_contract_version':'player_context_v1','hero_ids':heroes,'pairs':{},'context_id':'fixture','source_seasons':['A'],'context_as_of':'2020-01-01'}
    catalog={'schema_version':1,'hero_ids':heroes,'hero_names':names,'hero_icons':{},'role_ids':roles,
             'hero_positions':{str(h):roles for h in heroes},'draft_sequence':[{'bp_order':1,'action':'ban','side':'blue','team_action_type_number':1}],
             'base':[],'action':[],'relations':[],'generated_at':'fixture','training_inputs':[],'training_decisions':20,'config':{}}
    space=build_feature_space(policy,'A',[])
    lineup={'version':'lineup-value-model-v1','generated_at':'fixture','model':{'feature_names':list(FEATURE_NAMES),'means':[0.]*7,'scales':[1.]*7,'coefficients':[0.]*7,'intercept':0.},
            'state':{'config':{k:12. for k in ['familiarity_prior','synergy_prior','team_pair_prior','counter_prior']},'ratings':{},'team_games':{},'team_hero':[],'ally_pair':[],'counters':[],'team_pair':[]},
            'raw_mechanics':{str(h):{'control__strong':0.} for h in heroes},'hero_names':names,'team_names':{'blue':'Blue','red':'Red'},
            'mechanics_metadata':{'ally_rules':ALLY_RULES,'counter_rules':COUNTER_RULES}}
    ban={'version':'ban-value-model-v1','generated_at':'fixture','hero_names':names}
    values=[policy,calibration,context,catalog,space,ban,lineup]
    root.mkdir(parents=True,exist_ok=True)
    for name in COMPONENTS:
        if name.endswith('.jsonl'):(root/name).write_text('')
        else:atomic_json(root/name,{})
    for name,value in zip(COMPONENTS[:7],values):atomic_json(root/name,value)
    atomic_json(root/'hero_draft_feature_vectors.json',features);atomic_json(root/'hero_tactical_roles.json',{'heroes':[]})
    (root/'herolist.json').write_text('[]')
    return policy


def seed_manifest(version):
    return {'version':version,'source_seasons':['A'],'promotion_status':'seed',
            'parameter_training_cutoff':'2020-01-01','context_reference_cutoff':'2020-01-01'}
