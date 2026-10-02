from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import torch
ANALYSIS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ANALYSIS));sys.path.insert(0,str(ANALYSIS/'sequence_training'));sys.path.insert(0,str(ANALYSIS.parent/'backend'))
from train_personalized_draft_choice_model import extend,logits_for
from personalized_models import FamiliarityResidual
from app.services.player_draft_context import candidate_features
from app.services.personalized_model_runtime import prepare_personalized_parameters,personalized_logits
from player_context.dataset import compact_familiarity_context


class Dataset:
    match_ids=['known','debut'];battle_ids=['battle','battle'];next_positions=torch.tensor([5,5])
    def __len__(self):return 2
    def batch(self,index,device):
        return {'legal_mask':torch.ones(len(index),3,dtype=torch.bool),'next_actions':torch.ones(len(index),dtype=torch.long)}


class Builder:
    hero_ids=[105,106,107]
    games=[SimpleNamespace(event_date=date(2020,1,1),appearances=[SimpleNamespace(team_id='own'),SimpleNamespace(team_id='enemy')])]
    calls=[]
    def build(self,own,enemy,cutoff):
        self.calls.append(cutoff)
        return {'familiarity':np.arange(30).reshape(2,5,3)/30,'hero_role_prior':np.ones((3,5))/5,'coverage':[.8,.6]}


def test_frozen_holdout_features_and_numpy_logits_match_serving_including_debut_team():
    builder=Builder();cutoff=date(2020,1,10)
    rows={(match,'battle',5):{'event_date':'2020-02-20','acting_team_id':own,'opponent_team_id':'enemy','current_team_picks':[105],'current_opponent_picks':[106], 'team_used_in_previous_battles':[],'opponent_used_in_previous_battles':[]} for match,own in [('known','own'),('debut','new-team')]}
    data=extend(Dataset(),rows,builder,frozen_cutoff=cutoff)
    context=compact_familiarity_context(builder.build('own','enemy',cutoff))
    snapshot={'hero_ids':builder.hero_ids,'pairs':{'own|enemy':{'familiarity':context['familiarity'].tolist(),'hero_role_prior':context['hero_role_prior'].tolist(),'coverage':context['coverage']}}}
    expected,_=candidate_features(snapshot,'own','enemy',{'blue_picks':[105],'red_picks':[106]},'blue')
    np.testing.assert_array_equal(data.extras['candidate_features'][0].numpy(),expected)
    missing,reason=candidate_features(snapshot,'new-team','enemy',{},'blue');assert missing is None and reason=='team_pair_missing'
    assert not np.any(data.extras['candidate_features'][1].numpy())
    assert set(builder.calls)=={cutoff}
    branch=FamiliarityResidual()
    with torch.no_grad():branch.pick_head.weight.fill_(.2)
    class Base:
        def eval(self):return self
        def __call__(self,batch):return torch.tensor([[1.,2.,3.]]).expand(len(batch['next_actions']),-1)
    actual=logits_for(Base(),branch,data,2,torch.device('cpu'))
    artifact={'schema_version':1,'model_type':'sequence_familiarity_residual_choice','candidate_kind':'familiarity_residual','hero_ids':builder.hero_ids,'config':{'feature_width':9},'parameters':{k:v.detach().tolist() for k,v in branch.state_dict().items()}}
    served=personalized_logits(prepare_personalized_parameters(artifact),{'candidate_features':data.extras['candidate_features'].numpy(),'legal_mask':np.ones((2,3),dtype=bool),'next_actions':np.ones(2)},np.asarray([[1.,2.,3.],[1.,2.,3.]],dtype=np.float32))
    np.testing.assert_allclose(actual,served,atol=1e-6)
    np.testing.assert_array_equal(actual[1],np.asarray([1.,2.,3.]))
