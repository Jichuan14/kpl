import sys
from pathlib import Path

import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"analysis"/"sequence_training"))
from personalized_models import FamiliarityResidual
from app.services.personalized_model_runtime import personalized_logits, prepare_personalized_parameters


def test_numpy_familiarity_parity():
    torch.manual_seed(3); model=FamiliarityResidual().eval(); features=torch.randn(4,7,9); legal=torch.ones(4,7,dtype=torch.bool);actions=torch.tensor([1,2,1,2]);base=torch.randn(4,7)
    with torch.inference_mode(): expected=model(base,features,actions,legal).numpy()
    artifact={"schema_version":1,"model_type":"sequence_familiarity_residual_choice","candidate_kind":"familiarity_residual","config":{"feature_width":9},"hero_ids":list(range(7)),"parameters":{name:value.detach().numpy().tolist() for name,value in model.state_dict().items()}}
    actual=personalized_logits(prepare_personalized_parameters(artifact),{"candidate_features":features.numpy(),"legal_mask":legal.numpy(),"next_actions":actions.numpy()},base.numpy())
    assert np.max(np.abs(expected-actual))<1e-6
