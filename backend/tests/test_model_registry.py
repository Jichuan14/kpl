import copy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from app.services import model_registry as registry
from app.services.model_registry import install_bundle,activate_bundle,resolve_bundle,bundle_scope
from app.services.draft_simulator import load_personalized_model,load_model,learned_feature_space,_prepare_prediction
from app.services.lineup_value import load_lineup_value_model
from app.services.model_tool_scope import pinned_model_operation
from app.agent.conversation import board_fingerprint
from bundle_fixture import write_bundle_source,seed_manifest


@pytest.fixture
def collection(tmp_path):
    source=tmp_path/'source';write_bundle_source(source)
    root=tmp_path/'registry';browser=tmp_path/'browser'
    a=install_bundle(source,seed_manifest('a'),registry_root=root)
    activate_bundle(a,registry_root=root,browser_root=browser)
    return source,root,browser,a


def test_bundle_pins_all_loaders_and_selected_season_counts(collection,tmp_path):
    source,root,browser,a=collection
    facts=tmp_path/'analysis';(facts/'exports'/'S4').mkdir(parents=True);(facts/'exports'/'S4'/'bp_decisions.jsonl').write_text('')
    with bundle_scope(a),patch('app.services.draft_simulator.ANALYSIS_DIR',facts):
        assert load_personalized_model('S4')['model_fingerprint']==json.loads((source/'personalized_draft_choice_model.json').read_text())['model_fingerprint']
        assert load_model('S4')['hero_ids']==list(range(105,117))
        assert load_lineup_value_model('S4').payload['generated_at']=='fixture'
        assert all(r['pick_count']==r['ban_count']==0 for r in learned_feature_space('S4')['rows'])
        old_fingerprint=board_fingerprint('S4',{'blue_picks':[]})
        with pytest.raises(ValueError,match='legacy'):_prepare_prediction('S4',{},'sequence')
    assert old_fingerprint != board_fingerprint('S4',{'blue_picks':[]})


def test_unknown_version_and_corruption_fail_explicitly(collection):
    source,root,browser,a=collection
    with pytest.raises(FileNotFoundError):resolve_bundle('missing',registry_root=root)
    with pytest.raises(ValueError):resolve_bundle('../a',registry_root=root)
    (a.root/'ban_value_model.json').write_text('{}')
    with pytest.raises(ValueError,match='hash mismatch'):resolve_bundle('a',registry_root=root)


@pytest.mark.parametrize('component,mutate,match',[
    ('personalized_draft_probability_calibration.json',lambda x:x.update(model_fingerprint='wrong'),'Calibration'),
    ('personalized_draft_probability_calibration.json',lambda x:x.update(candidate_policy_fingerprint='wrong'),'contract'),
    ('hero_draft_feature_vectors.json',lambda x:x['rows'][0].update(vector=[1.]),'features differ'),
    ('draft_model.json',lambda x:x['hero_positions'].update({'105':[]}),'lane coverage'),
    ('lineup_value_model.json',lambda x:x['mechanics_metadata'].update(ally_rules=[]),'rule contract'),
    ('personalized_draft_choice_model.json',lambda x:x['hero_feature_matrix'][0].append(1.),'matrix'),
])
def test_semantic_mismatches_rejected(collection,component,mutate,match):
    source,root,browser,a=collection
    value=json.loads((source/component).read_text());mutate(value);registry.atomic_json(source/component,value)
    with pytest.raises(ValueError,match=match):install_bundle(source,seed_manifest('bad'),registry_root=root)
    assert resolve_bundle(registry_root=root).version=='a'


def test_atomic_browser_publish_failure_and_rollback_history(collection):
    source,root,browser,a=collection
    b=install_bundle(source,seed_manifest('b'),registry_root=root)
    with pytest.raises(ValueError,match='previously activated'):activate_bundle(b,registry_root=root,browser_root=browser,rollback=True)
    (browser/'versions'/'a'/'feature-space.json').write_text('{}')
    with pytest.raises(ValueError,match='browser assets'):activate_bundle(a,registry_root=root,browser_root=browser,rollback=True)
    assert json.loads((root/'current.json').read_text())['version']=='a'


def test_old_handle_survives_pointer_change_and_cache_isolated(collection):
    source,root,browser,a=collection
    write_bundle_source(source,bias=3.)
    b=install_bundle(source,seed_manifest('b'),registry_root=root)
    # Simulate already independently activated history for rollback-only change.
    registry.atomic_json(root/'activations'/'b.json',{'version':'b'})
    activate_bundle(b,registry_root=root,browser_root=browser,rollback=True)
    with bundle_scope(a):old=load_personalized_model('S4')
    with bundle_scope(b):new=load_personalized_model('S4')
    assert old['model_fingerprint']!=new['model_fingerprint']
    assert resolve_bundle(registry_root=root).version=='b'
    with bundle_scope(a):assert load_personalized_model('S4') is old
    activate_bundle(a,registry_root=root,browser_root=browser,rollback=True)
    assert resolve_bundle(registry_root=root).version=='a'


def test_pinned_coach_nested_legacy_rejected(collection):
    _,_,_,a=collection
    from types import SimpleNamespace
    @pinned_model_operation
    def route(body):raise AssertionError('Must reject before route execution')
    with patch('app.services.model_tool_scope.resolve_bundle',return_value=a):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as caught:
            route(SimpleNamespace(model_version='a',draft_state=SimpleNamespace(model_type='learnable')))
        assert caught.value.status_code==400


def eligible_source(source, version, incumbent):
    from app.services.draft_calibration import canonical_sha256
    policy=json.loads((source/'personalized_draft_choice_model.json').read_text())
    split={'manifest_sha256':'eligible-split','splits':{}}
    for index,name in enumerate(('train','validation','calibration','holdout')):
        split['splits'][name]=[{'season':'B','match_id':f'{name}-{j}','start_time':f'2021-0{index+1}-{j+1:02d}T00:00:00Z'} for j in range(10)]
    policy['split_manifest_sha256']=split['manifest_sha256']
    identity={k:policy[k] for k in ('candidate_kind','config','hero_ids','team_ids','player_vocab','parameters','base_parameters_sha256','split_manifest_sha256')}
    policy['model_fingerprint']=canonical_sha256(identity)
    registry.atomic_json(source/'personalized_draft_choice_model.json',policy)
    calib=json.loads((source/'personalized_draft_probability_calibration.json').read_text())
    calib.update(model_fingerprint=policy['model_fingerprint'],split_manifest_sha256=split['manifest_sha256'],calibration_match_ids=sorted(r['match_id'] for r in split['splits']['calibration']))
    registry.atomic_json(source/'personalized_draft_probability_calibration.json',calib)
    space=json.loads((source/'learned_hero_feature_space.json').read_text());space['source_model_fingerprint']=policy['model_fingerprint'];registry.atomic_json(source/'learned_hero_feature_space.json',space)
    names=('adequate_support','full_legal_and_vocab_coverage','familiarity_holdout_gate','policy_nll','policy_top5','ban_top5','lineup_logloss')
    return {'version':version,'source_seasons':['B'],'promotion_status':'eligible','parameter_training_cutoff':'2021-01-10T00:00:00Z','context_reference_cutoff':'2021-02-01T00:00:00Z','split_manifest':split,
        'promotion':{'eligible':True,'candidate_model_fingerprint':policy['model_fingerprint'],'incumbent_version':incumbent.version,'incumbent_model_fingerprint':json.loads(incumbent.path('personalized_draft_choice_model.json').read_text())['model_fingerprint'],'evaluation_start':'2021-04-01T00:00:00Z','checks':{name:True for name in names}}}


def test_eligible_promotion_cas_and_public_permissions(collection):
    source,root,browser,a=collection
    b=install_bundle(source,eligible_source(source,'b',a),registry_root=root)
    c=install_bundle(source,eligible_source(source,'c',a),registry_root=root)
    activate_bundle(b,registry_root=root,browser_root=browser)
    with pytest.raises(ValueError,match='incumbent'):activate_bundle(c,registry_root=root,browser_root=browser)
    assert resolve_bundle(registry_root=root).version=='b'
    target=browser/'versions'/'b'
    assert target.stat().st_mode&0o777==0o755
    assert all(p.stat().st_mode&0o777==0o644 for p in target.iterdir())
    target.chmod(0o700);(target/'metadata.json').chmod(0o600)
    activate_bundle(b,registry_root=root,browser_root=browser,rollback=True)
    assert target.stat().st_mode&0o777==0o755 and (target/'metadata.json').stat().st_mode&0o777==0o644


def test_stream_scope_and_cached_conversation_reject_version_change(collection):
    import asyncio
    from types import SimpleNamespace
    from starlette.responses import StreamingResponse
    from app.services.model_registry import current_bundle
    from app.agent.conversation import ConversationStore
    from app.agent.errors import CoachConversationError
    source,root,browser,a=collection
    b=install_bundle(source,seed_manifest('b'),registry_root=root)
    @pinned_model_operation
    def route(body):
        async def content():
            yield json.dumps({'type':'result','data':{'observed_version':current_bundle().version}})+'\n'
        return StreamingResponse(content())
    with patch('app.services.model_tool_scope.resolve_bundle',return_value=a):response=route(SimpleNamespace(model_version='a'))
    async def read():return [chunk async for chunk in response.body_iterator]
    with bundle_scope(b):chunks=asyncio.run(read())
    result=json.loads(chunks[0]);assert result['data']['observed_version']==result['data']['model_version']=='a'
    store=ConversationStore();record=store.create(store.ensure_session(None))
    with bundle_scope(a):board_a=board_fingerprint('S4',{})
    store.begin_turn(record,client_request_id='same',request_id='1',current_board=board_a)
    store.finish_turn(record,result={'model_version':'a'},turn={'board_fingerprint':board_a})
    assert store.begin_turn(record,client_request_id='same',request_id='2',current_board=board_a)=={'model_version':'a'}
    with bundle_scope(b):board_b=board_fingerprint('S4',{})
    with pytest.raises(CoachConversationError):store.begin_turn(record,client_request_id='same',request_id='3',current_board=board_b)


def production_source(source,version,incumbent):
    metadata=eligible_source(source,version,incumbent);split=metadata['split_manifest'];all_rows=[r for rows in split['splits'].values() for r in rows]
    split['mode']='production_all_data';split['splits']={'train':all_rows,'validation':[],'calibration':[],'holdout':[]}
    metadata.update(promotion_status='production_all_data',training_contract='production_all_data',retrain_identity='fixture',recipe={'epochs':30},maintained_inputs={'features':'fixture'})
    for filename in ['personalized_draft_choice_model.json','player_draft_context.json','ban_value_model.json','lineup_value_model.json']:
        value=json.loads((source/filename).read_text());target=value.setdefault('source',{}) if filename in ['ban_value_model.json','lineup_value_model.json'] else value
        target.update(training_series=all_rows,split_manifest_sha256=split['manifest_sha256'])
        if filename=='personalized_draft_choice_model.json':value.update(status='production_all_data',calibration={'status':'uncalibrated','temperature':1.})
        registry.atomic_json(source/filename,value)
    calibration=json.loads((source/'personalized_draft_probability_calibration.json').read_text());calibration.update(status='uncalibrated',method='none',temperature=1.,calibration_match_ids=[]);registry.atomic_json(source/'personalized_draft_probability_calibration.json',calibration)
    metadata['promotion']={k:v for k,v in metadata['promotion'].items() if k.startswith('incumbent_')}
    return metadata


def test_production_all_data_activation_without_quality_gates_still_has_cas(collection):
    source,root,browser,a=collection
    b=install_bundle(source,production_source(source,'production-b',a),registry_root=root)
    c=install_bundle(source,production_source(source,'production-c',a),registry_root=root)
    activate_bundle(b,registry_root=root,browser_root=browser)
    assert b.metadata()['calibration_status']=='uncalibrated'
    with pytest.raises(ValueError,match='incumbent'):activate_bundle(c,registry_root=root,browser_root=browser)
    assert resolve_bundle(registry_root=root).version=='production-b'
    activate_bundle(a,registry_root=root,browser_root=browser,rollback=True)
    assert resolve_bundle(registry_root=root).version=='a'


@pytest.mark.parametrize('filename',['lineup_value_model.json','ban_value_model.json','player_draft_context.json'])
def test_hash_consistent_nonfinite_serving_component_rejected(collection,filename):
    source,root,_,_=collection
    value=json.loads((source/filename).read_text());value['nonfinite_test']=float('nan');(source/filename).write_text(json.dumps(value))
    with pytest.raises(ValueError,match='Non-finite'):install_bundle(source,seed_manifest('bad-finite'),registry_root=root)
    assert resolve_bundle(registry_root=root).version=='a'


def test_raw_probability_resolution_reports_uncalibrated_exact_bound_weights(collection):
    from app.services.draft_calibration import resolve_calibration
    source,_,_,a=collection;production_source(source,'production',a)
    sidecar=json.loads((source/'personalized_draft_probability_calibration.json').read_text())
    value=resolve_calibration(source/'personalized_draft_probability_calibration.json',enabled_mode='eligible',policy_model_type='personalized',model_fingerprint=sidecar['model_fingerprint'],candidate_policy_id='game_availability_v1',candidate_policy_fingerprint_value=sidecar['candidate_policy_fingerprint'])
    assert value.status=='uncalibrated' and value.temperature==1. and value.method=='none'
