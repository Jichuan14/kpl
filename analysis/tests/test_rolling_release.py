from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import json
import pytest
ANALYSIS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ANALYSIS));sys.path.insert(0,str(ANALYSIS.parent/'backend'))
import train_rolling_bundle as rolling
import rolling_evaluation


def manifest():
    return {'source_seasons':['S4'],'splits':{name:[{'season':'S4','match_id':str(i),'start_time':'2026-01-01T00:00:00Z'}] for i,name in enumerate(('train','validation','calibration','holdout'))}}


def test_gate_exception_records_failed_attempt_and_keeps_incumbent(tmp_path):
    root=tmp_path/'registry';root.mkdir();pointer=root/'current.json';pointer.write_text('{"version":"incumbent"}')
    incumbent=SimpleNamespace(version='incumbent',manifest={'parameter_training_cutoff':'2020-01-01','context_reference_cutoff':'2020-01-01'})
    with patch.object(sys,'argv',['train','--evaluation-mode','--output-root',str(tmp_path/'candidates'),'--version','candidate']),patch.object(rolling,'REGISTRY_ROOT',root),patch.object(rolling,'resolve_bundle',return_value=incumbent),patch.object(rolling,'build_rolling_manifest',return_value=manifest()),patch.object(rolling,'train_candidate',return_value={}),patch.object(rolling_evaluation,'promotion_report',side_effect=ValueError('bad calibration lineage')):
        with pytest.raises(ValueError,match='lineage'):rolling.main()
    status=json.loads((tmp_path/'candidates/latest_status.json').read_text())
    assert status['status']=='FAILED' and status['active_preserved']
    assert json.loads(pointer.read_text())['version']=='incumbent'


def test_low_budget_without_smoke_flag_is_always_experimental(tmp_path):
    output=tmp_path/'candidates'/'candidate';output.mkdir(parents=True);(output/'reference_coverage.json').write_text('{}')
    report={'limitations':[]};handle=SimpleNamespace(version='candidate')
    with patch.object(sys,'argv',['train','--evaluation-mode','--output-root',str(tmp_path/'candidates'),'--version','candidate','--epochs','1','--trials','1','--activate']),patch.object(rolling,'REGISTRY_ROOT',tmp_path/'empty'),patch.object(rolling,'build_rolling_manifest',return_value=manifest()),patch.object(rolling,'train_candidate',return_value=report),patch.object(rolling_evaluation,'promotion_report',return_value={'eligible':False,'reason':'Experimental budget'}) as gate,patch.object(rolling,'install_bundle',return_value=handle) as install,patch.object(rolling,'activate_bundle') as activate:
        rolling.main()
    assert gate.call_args.kwargs['smoke'] is True
    assert install.call_args.args[1]['promotion_status']=='experimental'
    activate.assert_not_called()


def test_zero_or_few_current_games_defers_before_training(tmp_path):
    root=tmp_path/'registry';root.mkdir();(root/'current.json').write_text('{"version":"incumbent"}')
    incumbent=SimpleNamespace(version='incumbent',manifest={'parameter_training_cutoff':'2026-09-05','context_reference_cutoff':'2026-09-05'})
    with patch.object(sys,'argv',['train','--evaluation-mode','--output-root',str(tmp_path/'candidates'),'--activate']),patch.object(rolling,'REGISTRY_ROOT',root),patch.object(rolling,'resolve_bundle',return_value=incumbent),patch.object(rolling,'build_rolling_manifest',return_value=manifest()),patch.object(rolling,'train_candidate') as train:
        rolling.main()
    train.assert_not_called()
    status=json.loads((tmp_path/'candidates/latest_status.json').read_text())
    assert status['status']=='DEFERRED' and status['active_model_version']=='incumbent'
