"""Immutable, content-verified production bundles with one atomic active pointer.

A handle is resolved once per operation and carried through nested tool calls.
Season facts never use this registry. A missing/invalid pinned version is an error.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import copy
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory, NamedTemporaryFile
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_ROOT = ROOT / 'analysis/outputs/models'
BROWSER_ROOT = ROOT / 'analysis/published/data/models'
COMPONENTS = (
    'personalized_draft_choice_model.json',
    'personalized_draft_probability_calibration.json', 'player_draft_context.json',
    'draft_model.json', 'learned_hero_feature_space.json', 'ban_value_model.json',
    'lineup_value_model.json', 'counter_pick_stats.jsonl', 'meta_hero_stats.jsonl',
    'pick_synergy_stats.jsonl', 'team_synergy_stats.jsonl',
    'team_action_tendencies.jsonl', 'hero_tactical_roles.json', 'herolist.json',
    'hero_ability_mechanics.json', 'hero_draft_feature_vectors.json',
    'ban_response_stats.jsonl', 'counter_ban_stats.jsonl', 'season_teams.jsonl',
    'team_opening_sequences.jsonl', 'team_combo_performance.jsonl',
    'player_hero_pools.jsonl', 'team_recent_trends.jsonl',
)
_HANDLE_CACHE: dict[Path, tuple[tuple, 'BundleHandle']] = {}
_PINNED: ContextVar['BundleHandle | None'] = ContextVar('model_bundle', default=None)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', dir=path.parent, encoding='utf-8', delete=False) as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write('\n'); output.flush(); os.fsync(output.fileno())
        temporary = Path(output.name)
    temporary.replace(path)


def version_id(value: str) -> str:
    if not value or len(value) > 128 or not all(c.isalnum() or c in '-_' for c in value):
        raise ValueError('Invalid model version')
    return value


@dataclass(frozen=True)
class BundleHandle:
    version: str
    root: Path
    manifest: dict[str, Any]

    def path(self, filename: str) -> Path:
        if filename not in self.manifest['components']:
            raise ValueError(f'Bundle {self.version} has no component {filename}')
        return self.root / filename

    def metadata(self) -> dict:
        return {'model_version': self.version, 'model_scope': 'rolling_historical_reference',
                'source_seasons': self.manifest['source_seasons'],
                'parameter_training_cutoff': self.manifest.get('parameter_training_cutoff'),
                'context_reference_cutoff': self.manifest.get('context_reference_cutoff'),
                'promotion_status': self.manifest['promotion_status'],
                'limitations': self.manifest.get('limitations', []),
                'calibration_status': 'uncalibrated' if self.manifest.get('training_contract')=='production_all_data' else 'historical_artifact',
                'training_contract':self.manifest.get('training_contract','historical_evaluation')}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _event_time(value: str):
    from datetime import timedelta
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.astimezone(timezone.utc)


def validate_series_splits(manifest: dict) -> None:
    names=('train','validation','calibration','holdout')
    if set(manifest.get('splits',{}))!=set(names): raise ValueError('Missing series windows')
    seen=set(); previous=None
    for name in names:
        rows=manifest['splits'][name]
        if len(rows)<10: raise ValueError('Each window needs ten independent series')
        times=[_event_time(r['start_time']) for r in rows]
        if previous is not None and min(times)<=previous: raise ValueError('Overlapping chronology/equal-time groups')
        previous=max(times)
        for row in rows:
            key=(str(row['season']),str(row['match_id']))
            if key in seen: raise ValueError('Series split overlap')
            seen.add(key)


def validate_bundle(root: Path) -> dict:
    manifest = _json(root / 'manifest.json')
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported bundle manifest')
    version_id(manifest['version'])
    if not manifest.get('source_seasons'):
        raise ValueError('Bundle source coverage is missing')
    if set(manifest.get('components', {})) != set(COMPONENTS):
        raise ValueError('Incomplete production bundle')
    for filename, expected in manifest['components'].items():
        path = root / filename
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError(f'Bundle hash mismatch: {filename}')
    policy = _json(root / COMPONENTS[0]); base = policy['base_artifact']
    calibration = _json(root / COMPONENTS[1]); space = _json(root / COMPONENTS[4])
    context = _json(root / COMPONENTS[2]); catalog = _json(root / COMPONENTS[3])
    if policy.get('model_type') != 'sequence_familiarity_residual_choice':
        raise ValueError('Bundle requires the production personalized policy')
    fingerprint = policy.get('model_fingerprint')
    # Calibration fingerprints use the exported composite artifact contract.
    bound = calibration.get('model_fingerprint')
    if bound != fingerprint or not fingerprint:
        raise ValueError('Calibration is not bound to this policy')
    if calibration.get('candidate_policy_id', calibration.get('candidate_policy')) != 'game_availability_v1':
        raise ValueError('Unsupported candidate policy')
    all_data=manifest.get('training_contract')=='production_all_data' or manifest['promotion_status']=='production_all_data'
    if calibration.get('status') != ('uncalibrated' if all_data else 'experimental' if manifest['promotion_status']=='experimental' else 'eligible'):
        raise ValueError('Calibration is not eligible')
    heroes = set(map(int, base['hero_ids']))
    if space.get('source_model_fingerprint') != fingerprint or set(int(r['hero_id']) for r in space['rows']) != heroes:
        raise ValueError('Feature map vocabulary/fingerprint mismatch')
    if set(map(int, catalog.get('hero_names', {}))) != heroes:
        raise ValueError('Catalog vocabulary mismatch')
    import numpy as np
    from app.services.draft_calibration import canonical_sha256, candidate_policy_fingerprint
    from app.services.sequence_model_runtime import prepare_sequence_parameters
    from app.services.personalized_model_runtime import prepare_personalized_parameters
    from app.services.lineup_value import LineupValueModel, ALLY_RULES, COUNTER_RULES
    from app.services.ban_recommender import BanValueModel
    try:
        matrix = np.asarray(policy.get('hero_feature_matrix', []), dtype=np.float32)
    except (TypeError,ValueError) as error:
        raise ValueError('Feature matrix is malformed') from error
    if matrix.shape != (len(heroes),len(base['feature_names'])) or not np.isfinite(matrix).all():
        raise ValueError('Feature matrix schema/vocabulary mismatch')
    if canonical_sha256(base['parameters']) != base.get('parameters_sha256') or policy.get('base_parameters_sha256') != base.get('parameters_sha256'):
        raise ValueError('Base parameter fingerprint mismatch')
    identity = {k:policy[k] for k in ('candidate_kind','config','hero_ids','team_ids','player_vocab','parameters','base_parameters_sha256','split_manifest_sha256')}
    if canonical_sha256(identity) != fingerprint or policy['hero_ids'] != base['hero_ids'] or policy['team_ids'] != base['team_ids']:
        raise ValueError('Composite parameter/vocabulary fingerprint mismatch')
    prepare_sequence_parameters(base,matrix); prepare_personalized_parameters(policy)
    if set(policy['parameters'])!={'pick_head.weight','pick_head.bias','ban_head.weight','ban_head.bias'} or policy.get('config',{}).get('feature_width')!=9:
        raise ValueError('Familiarity feature/parameter schema mismatch')
    for name,value in policy['parameters'].items():
        if np.asarray(value).shape != ((1,9) if name.endswith('weight') else (1,)):
            raise ValueError('Familiarity parameter shape mismatch')
    features = _json(root / 'hero_draft_feature_vectors.json')
    feature_rows = {int(r['hero_id']):r for r in features['rows']}
    if [*features['feature_names'],'feature_known'] != base['feature_names']:
        raise ValueError('Pinned feature names differ from the policy')
    expected_matrix = np.asarray([[*feature_rows[h]['vector'],float(feature_rows[h].get('feature_known',True))] for h in base['hero_ids']],dtype=np.float32)
    if not np.array_equal(matrix,expected_matrix):
        raise ValueError('Pinned features differ from the policy matrix')
    if set(map(int, context.get('hero_ids', []))) != heroes or context.get('context_contract_version') != policy.get('context_contract_version'):
        raise ValueError('Context vocabulary/contract mismatch')
    for pair in context.get('pairs',{}).values():
        if np.asarray(pair.get('familiarity',[])).shape!=(2,5,len(heroes)) or np.asarray(pair.get('hero_role_prior',[])).shape!=(len(heroes),5) or np.asarray(pair.get('coverage',[])).shape!=(2,):
            raise ValueError('Player context shape/vocabulary mismatch')
    if any(not catalog.get('hero_positions', {}).get(str(hero)) for hero in heroes) or set(catalog.get('role_ids',[])) != {6,2,5,7,4}:
        raise ValueError('Catalog lacks legal lane coverage')
    expected_policy = candidate_policy_fingerprint('game_availability_v1',availability_config={
        'role_ids':catalog['role_ids'],'global_bp_previous_game_pick_exclusion':True,'ban_uses_opponent_open_roles':False})
    if calibration.get('candidate_policy_fingerprint') != expected_policy or calibration.get('context_contract_version') != policy.get('context_contract_version'):
        raise ValueError('Calibration candidate/context contract mismatch')
    if calibration.get('split_manifest_sha256') != policy.get('split_manifest_sha256') or not math.isfinite(float(calibration.get('temperature',0))) or float(calibration.get('temperature',0))<=0:
        raise ValueError('Calibration lineage/temperature mismatch')
    def finite_values(value):
        if isinstance(value,dict):
            return all(finite_values(item) for item in value.values())
        if isinstance(value,list):
            return all(finite_values(item) for item in value)
        return not isinstance(value,(int,float)) or math.isfinite(value)
    for filename in ('lineup_value_model.json','ban_value_model.json','player_draft_context.json'):
        if not finite_values(_json(root/filename)):
            raise ValueError(f'Non-finite serving inputs: {filename}')
    tactical = _json(root/'hero_tactical_roles.json')
    lineup = _json(root/'lineup_value_model.json'); ban = _json(root/'ban_value_model.json')
    LineupValueModel(lineup,tactical); BanValueModel(ban)
    regression=lineup.get('model',{})
    width=len(regression.get('feature_names',[]))
    if width!=7 or any(np.asarray(regression.get(name,[])).shape!=(width,) for name in ('means','scales','coefficients')) or any(float(v)<=0 for v in regression.get('scales',[])):
        raise ValueError('Lineup regression shape/scale mismatch')
    mechanics = lineup.get('mechanics_metadata',{})
    if mechanics.get('ally_rules') != json.loads(json.dumps(ALLY_RULES)) or mechanics.get('counter_rules') != json.loads(json.dumps(COUNTER_RULES)):
        raise ValueError('Lineup mechanics rule contract mismatch')
    if not heroes.issubset(set(map(int,lineup.get('raw_mechanics',{})))):
        raise ValueError('Lineup mechanics lack policy vocabulary coverage')
    for name in COMPONENTS:
        if name.endswith('.jsonl'):
            for line in (root/name).read_text().splitlines():
                if line.strip() and not isinstance(json.loads(line),dict):
                    raise ValueError('Reference artifact must contain object rows')
    if all_data:
        split=manifest.get('split_manifest') or {}
        if split.get('mode')!='production_all_data' or not split.get('splits',{}).get('train') or any(split['splits'].get(name) for name in ('validation','calibration','holdout')):
            raise ValueError('All-data contract must train every series without reserved windows')
        ids=split['splits']['train']
        if len({(str(r['season']),str(r['match_id'])) for r in ids})!=len(ids):
            raise ValueError('Duplicate all-data series')
        if calibration.get('method')!='none' or calibration.get('temperature')!=1. or calibration.get('calibration_match_ids')!=[] or policy.get('calibration',{}).get('status')!='uncalibrated':
            raise ValueError('All-data fit requires explicit uncalibrated temperature 1')
        if policy['split_manifest_sha256']!=split.get('manifest_sha256'):
            raise ValueError('All-data policy lineage mismatch')
        for value in (policy,context,ban.get('source',{}),lineup.get('source',{})):
            if value.get('training_series')!=ids or value.get('split_manifest_sha256')!=split['manifest_sha256']:
                raise ValueError('All-data components trained on different series')
        if not manifest.get('retrain_identity') or not manifest.get('recipe') or not manifest.get('maintained_inputs'):
            raise ValueError('All-data reproducibility identity is missing')
    if manifest['promotion_status'] == 'eligible':
        split = manifest.get('split_manifest') or {}
        validate_series_splits(split)
        if min(len(v) for v in split['splits'].values())<10 or split.get('manifest_sha256') != policy['split_manifest_sha256']:
            raise ValueError('Candidate lacks exact adequately supported splits')
        if calibration.get('calibration_match_ids') != sorted(str(r['match_id']) for r in split['splits']['calibration']):
            raise ValueError('Calibration series differ from the pinned split')
        report=manifest.get('promotion',{})
        required={'adequate_support','full_legal_and_vocab_coverage','familiarity_holdout_gate','policy_nll','policy_top5','ban_top5','lineup_logloss'}
        if report.get('candidate_model_fingerprint') != fingerprint or set(report.get('checks',{}))!=required:
            raise ValueError('Promotion report candidate/criteria mismatch')
        if not report.get('eligible') or not all(report.get('checks',{}).values()) or not report.get('checks'):
            raise ValueError('Incomplete or failing promotion checks')
    if manifest['promotion_status'] not in {'seed', 'eligible', 'experimental', 'production_all_data'}:
        raise ValueError('Invalid promotion status')
    return manifest


def resolve_bundle(version: str | None = None, *, registry_root: Path | None = None) -> BundleHandle:
    registry = registry_root or REGISTRY_ROOT
    if version in (None, 'active'):
        version = _json(registry / 'current.json')['version']
    version_id(version)
    root = registry / 'versions' / version
    if not root.is_dir():
        raise FileNotFoundError(f'Model version {version} is unavailable')
    signature=tuple((name,(root/name).stat().st_mtime_ns,(root/name).stat().st_size,(root/name).stat().st_ino) for name in ('manifest.json',*COMPONENTS))
    cached=_HANDLE_CACHE.get(root)
    if cached and cached[0]==signature:
        return cached[1]
    manifest = validate_bundle(root)
    if manifest['version'] != version:
        raise ValueError('Bundle directory and version differ')
    handle=BundleHandle(version,root,copy.deepcopy(manifest))
    _HANDLE_CACHE[root]=(signature,handle)
    return handle


@contextmanager
def bundle_scope(handle: BundleHandle | None):
    token = _PINNED.set(handle)
    try:
        yield handle
    finally:
        _PINNED.reset(token)


def current_bundle() -> BundleHandle | None:
    return _PINNED.get()


def component_path(filename: str, legacy: Path) -> Path:
    handle = current_bundle()
    return handle.path(filename) if handle else legacy


def model_output_root(league_id: str, legacy_root: Path) -> Path:
    handle = current_bundle()
    return handle.root if handle else legacy_root / league_id


def install_bundle(source: Path, manifest: dict, *, registry_root: Path | None = None) -> BundleHandle:
    registry = registry_root or REGISTRY_ROOT
    version = version_id(manifest['version']); destination = registry / 'versions' / version
    if destination.exists():
        raise ValueError('Immutable model version already exists')
    registry.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=registry, prefix='.stage-') as temporary:
        stage = Path(temporary)
        for name in COMPONENTS:
            shutil.copyfile(source / name, stage / name)
        manifest = {**manifest, 'schema_version': 1,
                    'components': {name: digest(stage / name) for name in COMPONENTS}}
        atomic_json(stage / 'manifest.json', manifest)
        validate_bundle(stage)
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(destination)
    return resolve_bundle(version, registry_root=registry)


def _activate_bundle_locked(handle: BundleHandle, *, registry_root: Path | None = None,
                    browser_root: Path | None = None, rollback: bool = False) -> dict:
    registry = registry_root or REGISTRY_ROOT; browser = browser_root or BROWSER_ROOT
    verified = validate_bundle(handle.root)
    if verified != handle.manifest:
        raise ValueError('Handle manifest changed')
    if handle.root.resolve() != (registry/'versions'/handle.version).resolve():
        raise ValueError('Activation handle belongs to another registry')
    if rollback and not (registry/'activations'/f'{handle.version}.json').is_file():
        raise ValueError('Rollback requires a previously activated version')
    if handle.manifest['promotion_status'] == 'experimental':
        raise ValueError('Experimental bundles cannot be activated')
    incumbent = resolve_bundle(registry_root=registry) if (registry/'current.json').is_file() else None
    if handle.manifest['promotion_status']=='seed' and incumbent and not rollback:
        raise ValueError('Seed activation is only allowed for an empty registry')
    if handle.manifest['promotion_status'] != 'seed' and not rollback:
        report = handle.manifest.get('promotion', {})
        if handle.manifest['promotion_status']!='production_all_data' and not report.get('eligible'):
            raise ValueError('Candidate lacks a passing promotion report')
        current = incumbent
        if report.get('incumbent_version') != (current.version if current else None) or (current and report.get('incumbent_model_fingerprint')!=_json(current.path('personalized_draft_choice_model.json'))['model_fingerprint']):
            raise ValueError('Stale promotion: incumbent version changed')
        if current and handle.manifest['promotion_status']!='production_all_data':
            cutoff = max(_event_time(str(current.manifest[k])) for k in ('parameter_training_cutoff', 'context_reference_cutoff'))
            if _event_time(str(report.get('evaluation_start') or '')) <= cutoff:
                raise ValueError('Evaluation overlaps incumbent training/reference history')
    target = browser / 'versions' / handle.version
    if not target.exists():
        browser.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=browser, prefix='.stage-') as temporary:
            stage = Path(temporary)
            shutil.copyfile(handle.path('learned_hero_feature_space.json'), stage / 'feature-space.json')
            shutil.copyfile(handle.path('draft_model.json'), stage / 'draft-model.json')
            atomic_json(stage / 'metadata.json', handle.metadata())
            for name in ('feature-space.json', 'draft-model.json', 'metadata.json'):
                _json(stage / name)
            stage.chmod(0o755)
            for asset in stage.iterdir():
                asset.chmod(0o644)
            target.parent.mkdir(parents=True, exist_ok=True); stage.rename(target)
    for filename, component in [('feature-space.json','learned_hero_feature_space.json'),('draft-model.json','draft_model.json')]:
        if not (target/filename).is_file() or digest(target/filename) != digest(handle.path(component)):
            raise ValueError('Published immutable browser assets differ from bundle')
    if _json(target/'metadata.json') != handle.metadata():
        raise ValueError('Published immutable browser metadata differs from bundle')
    # Repair permissions on an already verified public version as well.
    target.chmod(0o755)
    for filename in ('feature-space.json', 'draft-model.json', 'metadata.json'):
        (target / filename).chmod(0o644)
    # Assets and server components are complete before this sole activation point.
    previous = _json(registry / 'current.json').get('version') if (registry / 'current.json').is_file() else None
    pointer = {'version': handle.version, 'previous_version': previous,
               'activated_at': datetime.now(timezone.utc).isoformat()}
    atomic_json(registry / 'current.json', pointer)
    atomic_json(registry / 'activations' / f'{handle.version}.json', pointer)
    return pointer


def activate_bundle(handle: BundleHandle, *, registry_root: Path | None = None,
                    browser_root: Path | None = None, rollback: bool = False) -> dict:
    """Serialize promotions across workers and compare the evaluated incumbent."""
    import fcntl
    registry = registry_root or REGISTRY_ROOT
    registry.mkdir(parents=True, exist_ok=True)
    with (registry/'.activation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _activate_bundle_locked(handle,registry_root=registry,browser_root=browser_root,rollback=rollback)
