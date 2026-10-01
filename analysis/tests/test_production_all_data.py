import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest
ANALYSIS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ANALYSIS));sys.path.insert(0,str(ANALYSIS.parent/'backend'))
from test_rolling_corpus import exports
from rolling_corpus import build_rolling_manifest,exported_battles
import production_all_data as production
from train_rolling_bundle import corpus_rows
from player_context.history import parse_date


def all_manifest(root):return build_rolling_manifest(root,production_all_data=True)


def test_all_series_including_first_new_season_without_any_reserved_window(tmp_path):
    root=exports(tmp_path/'exports',1);old=root/'A';new=root/'20260004';old.rename(new)
    m=all_manifest(root)
    assert m['counts']=={'train':1,'validation':0,'calibration':0,'holdout':0}
    assert m['source_seasons']==['20260004'] and m['series_weights']['20260004:m000']==1.
    assert {r['match_id'] for r in corpus_rows(m,('train',))}=={'m000'}
    trainer=production.load_trainer();battles,_,_=exported_battles(m,trainer.V1)
    assert {b.match_id for b in battles}=={'m000'}


def test_fingerprint_ignores_incomplete_fixture_and_order_but_tracks_corrections(tmp_path):
    root=exports(tmp_path/'exports',3);m=all_manifest(root);before,_=production.retrain_identity(m)
    path=root/'A'/'matches.jsonl';rows=[json.loads(x) for x in path.read_text().splitlines()]
    path.write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in reversed(rows))+json.dumps({'match_id':'future','start_time':'2028-01-01','score':{},'battles':[]})+'\n')
    assert production.retrain_identity(all_manifest(root))[0]==before
    rows[0]['battles'][0]['players'][0]['player_name']='Corrected player'
    path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    assert production.retrain_identity(all_manifest(root))[0]!=before
    assert production.retrain_identity(all_manifest(root),{**production.RECIPE,'version':'changed'})[0]!=before


def test_unchanged_production_skips_and_historical_seed_refits(tmp_path):
    m=all_manifest(exports(tmp_path/'exports',1));identity,inputs=production.retrain_identity(m)
    registry=tmp_path/'registry';registry.mkdir();(registry/'current.json').write_text('{}')
    args=SimpleNamespace(output_root=tmp_path/'out',weight_mode='season_recency',decay=.65,maximum_age_days=None,cutoff=None,seed=7,epochs=30,smoke=False,activate=True,version='new',threads=1)
    incumbent=SimpleNamespace(version='current',manifest={'promotion_status':'production_all_data','retrain_identity':identity})
    with patch.object(production,'REGISTRY_ROOT',registry),patch.object(production,'build_rolling_manifest',return_value=m),patch.object(production,'ensure_hero_vocabulary'),patch.object(production,'resolve_bundle',return_value=incumbent),patch.object(production,'train_all_data') as train:
        assert production.production_update(args)['status']=='NO_CHANGE';train.assert_not_called()
        incumbent.manifest={'promotion_status':'seed'};train.side_effect=RuntimeError('training attempted')
        with pytest.raises(RuntimeError,match='attempted'):production.production_update(args)
        assert json.loads((args.output_root/'latest_status.json').read_text())['status']=='FAILED'


def test_unknown_traits_expand_vocabulary_without_fabricating_role_or_capability(tmp_path):
    root=exports(tmp_path/'exports',1);path=root/'A'/'bp_decisions.jsonl';rows=[json.loads(x) for x in path.read_text().splitlines()]
    for row in rows:row.update(selected_hero_id=9999,selected_hero_name='New hero',legal_hero_ids=[9999])
    path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    feature=tmp_path/'hero_draft_feature_vectors.json';feature.write_text(json.dumps({'feature_names':['control__strong'],'rows':[]}))
    official=tmp_path/'official.json';official.write_text('[{"ename":9999,"cname":"New hero","roles":"5"}]')
    with patch.object(production,'ANALYSIS',tmp_path),patch.object(production,'official_input',return_value=official):
        assert production.ensure_hero_vocabulary(all_manifest(root))==[9999]
        value=json.loads(feature.read_text())['rows'][0]
        assert value['feature_known'] is False and value['vector']==[0.] and value['missing_traits']
        assert production.ensure_hero_vocabulary(all_manifest(root))==[]
    official.write_text('[]')
    with patch.object(production,'ANALYSIS',tmp_path),patch.object(production,'official_input',return_value=official),patch.object(production,'refresh_official_catalogue',side_effect=OSError('offline')):
        with pytest.raises(ValueError,match='lane information unavailable'):production.ensure_hero_vocabulary(all_manifest(root))


def test_equivalent_timestamps_use_china_calendar_date_for_context():
    assert parse_date('2026-01-01T16:30:00Z')==parse_date('2026-01-02T00:30:00+08:00')==parse_date('2026-01-02 00:30:00')


def test_unknown_mechanics_reduce_coverage_and_remain_finite():
    from lineup_value.training import rule_density,ALLY_RULES
    from app.services.lineup_value import _rule_density
    raw={105:{'__mechanics_known':1.},9999:{'__mechanics_known':0.}}
    for function in (rule_density,_rule_density):
        value,coverage=function([9999],[105],raw,ALLY_RULES,exclude_self=False)
        assert value==0. and coverage==0.
