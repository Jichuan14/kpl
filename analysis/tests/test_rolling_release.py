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


def test_reference_match_stream_decodes_once_and_keeps_season_selection_order(tmp_path):
    source_a = tmp_path / 'a.jsonl'; source_b = tmp_path / 'b.jsonl'
    source_a.write_text('\n{"match_id":"same","teams":[]}\n{"match_id":"excluded"}\n')
    source_b.write_text('{"match_id":"same","teams":[]}\n')
    value = {'source_files': {'S3': {'matches': str(source_a)}, 'S4': {'matches': str(source_b)}},
             'source_seasons': ['S3','S4'], 'splits': {'train': []}, 'weighting': {}}
    output = tmp_path / 'output'; output.mkdir()
    original_loads = json.loads
    with patch.object(rolling, 'iter_corpus_rows', return_value=iter([])), patch.object(rolling, 'split_keys', return_value={('S4','same')}), patch.object(rolling, 'command'), patch.object(json, 'loads', wraps=original_loads) as decode:
        rolling.build_references(value, output)
        assert decode.call_count == 3
    rows = [original_loads(line) for line in (output / 'references/matches.jsonl').read_text().splitlines()]
    assert rows == [{'match_id':'same', 'teams':[]}]


def test_streamed_references_preserve_rows_weights_and_historical_scope(tmp_path):
    from test_rolling_corpus import exports
    from rolling_corpus import build_rolling_manifest
    manifest=build_rolling_manifest(exports(tmp_path/'exports',30),production_all_data=True)
    expected=rolling.corpus_rows(manifest,('train',))
    output=tmp_path/'output';output.mkdir()
    calls=[]
    def analyzer(script,*args):
        calls.append(script)
        # Every child can see complete, closed reference files.
        merged=output/'references/bp_decisions.jsonl'
        assert [json.loads(line) for line in merged.read_text().splitlines()]==expected
        if script=='compute_bp_statistics.py':
            (output/'pick_synergy_stats.jsonl').write_text(
                '\n'+json.dumps({'league_id':'A','support':7,'confidence_interval':[.1,.9]})+'\n')
    with patch.object(rolling,'command',side_effect=analyzer):
        rolling.build_references(manifest,output)
    for season in manifest['source_seasons']:
        path=output/'references'/season/'bp_decisions.jsonl'
        assert [json.loads(line) for line in path.read_text().splitlines()]==[
            row for row in expected if str(row['league_id'])==season]
    assert len(calls)==5
    result=json.loads((output/'pick_synergy_stats.jsonl').read_text())
    assert result=={'support':7,'confidence_interval':[.1,.9],
                    'evidence_scope':'rolling_model_reference','source_seasons':['A','B']}
    assert not list(output.glob('*.tmp'))
