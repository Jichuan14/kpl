"""Shared whole-series rolling chronology and reproducible example weights."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import json
import math
from collections import Counter
from pathlib import Path
from sequence_training.splits import file_sha256, canonical_sha256, _take_date_grouped_tail, validate_split_manifest


class DeferredTraining(ValueError):
    """The corpus has insufficient independent chronological support."""


def event_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    # Official exports are naive China time; aware inputs retain their instant.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.astimezone(timezone.utc)


def resolved_complete_series(match: dict) -> bool:
    """Verify final score by actual team identity, allowing side swaps and peak games."""
    try:
        teams = {int(t['match_camp']): str(t['team_id']) for t in match['teams']}
        if set(teams) != {1, 2} or len(set(teams.values())) != 2:
            return False
        score = {teams[c]: int(match['score'][f'camp{c}']) for c in (1, 2)}
        threshold = int(match.get('bo') or 5) // 2 + 1
        winner = str(match.get('match_winner_team_id') or '')
        if min(score.values()) < 0 or winner not in score or score[winner] != threshold or score[teams[1] if winner == teams[2] else teams[2]] >= threshold:
            return False
        battles = match['battles']; total = sum(score.values())
        if len(battles) != total or len({str(b['battle_id']) for b in battles}) != total or {int(b['battle_seq']) for b in battles} != set(range(1, total + 1)):
            return False
        wins = Counter()
        for battle in battles:
            camps = {int(c): str(t['team_id']) for c, t in battle['camp_teams'].items()}
            if set(camps) != {1, 2} or set(camps.values()) != set(teams.values()):
                return False
            if any(teams.get(int(t['match_camp'])) != str(t['team_id']) for t in battle['camp_teams'].values()):
                return False
            win_camp = int(battle['win_camp'])
            if win_camp not in camps or str(battle['winner_team_id']) != camps[win_camp]:
                return False
            players = battle.get('players', [])
            if any(str(p['team_id']) != camps.get(int(p['camp'])) or (p.get('match_camp') is not None and teams.get(int(p['match_camp'])) != str(p['team_id'])) for p in players):
                return False
            if any(len([p for p in players if int(p['camp']) == c]) != 5 for c in (1, 2)):
                return False
            wins[camps[win_camp]] += 1
        return all(wins[t] == count for t, count in score.items())
    except (KeyError, ValueError, TypeError):
        return False


def build_rolling_manifest(exports: Path, *, cutoff: str | None = None,
                           validation_series: int = 10, calibration_series: int = 10,
                           holdout_series: int = 10, weighting: dict | None = None, production_all_data: bool = False) -> dict:
    weighting = weighting or {'mode': 'season_recency', 'decay': .65, 'winning_pick_multiplier': 1.0}
    if min(validation_series, calibration_series, holdout_series) < 10:
        raise ValueError('Each evaluation window requires at least ten complete series')
    rows = []; files = {}; exclusions = []; seen_series = set()
    for decisions in sorted(exports.glob('*/bp_decisions.jsonl')):
        season = decisions.parent.name; matches = decisions.with_name('matches.jsonl')
        if not matches.is_file():
            continue
        games = {}
        for line in decisions.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                if not row.get('is_peak_battle'):
                    games.setdefault((str(row['match_id']), str(row['battle_id'])), []).append(row)
        eligible = set()
        for line in matches.read_text().splitlines():
            if not line.strip(): continue
            match = json.loads(line); time = str(match.get('start_time') or '')
            if not time or (cutoff and event_time(time) > event_time(cutoff)): continue
            match_id = str(match['match_id'])
            key = (season, match_id)
            if key in seen_series:
                raise ValueError('Duplicate exported series')
            seen_series.add(key)
            resolved = resolved_complete_series(match)
            expected = [b for b in match.get('battles', []) if not b.get('is_peak_battle') and int(b.get('battle_seq') or 0) != 7]
            actual_ids={str(b["battle_id"]) for b in match.get("battles",[])}
            no_orphans=all(battle_id in actual_ids for (series_id,battle_id) in games if series_id==match_id)
            complete = resolved and no_orphans and bool(expected) and all(
                len(games.get((match_id,str(b["battle_id"])), [])) == 20 and
                {int(r.get('bp_order') or 0) for r in games.get((match_id,str(b['battle_id'])), [])} == set(range(1,21))
                and all(not r.get('quality_flags') and r.get('selected_hero_id') and int(r['selected_hero_id']) in r.get('legal_hero_ids', []) for r in games.get((match_id,str(b['battle_id'])), []))
                for b in expected)
            if complete:
                eligible.add(match_id); rows.append({'season':season,'match_id':match_id,'start_time':event_time(time).isoformat()})
            else:
                exclusions.append({'season':season,'match_id':match_id,'reason':'incomplete_series'})
        if eligible:
            files[season] = {'matches':str(matches.resolve()), 'decisions':str(decisions.resolve()),
                             'matches_sha256':file_sha256(matches),'decisions_sha256':file_sha256(decisions)}
    if weighting.get('maximum_age_days') is not None:
        newest = event_time(cutoff) if cutoff else max((event_time(r['start_time']) for r in rows), default=None)
        days = float(weighting['maximum_age_days'])
        if days <= 0: raise ValueError('Maximum source age must be positive')
        rows = [r for r in rows if (newest-event_time(r['start_time'])).total_seconds() <= days*86400]
        files = {s:v for s,v in files.items() if any(r['season']==s for r in rows)}
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Cross-season match IDs must be globally unique for sequence/context adapters')
    rows.sort(key=lambda r:(event_time(r['start_time']),r['season'],r['match_id']))
    if production_all_data:
        if not rows: raise DeferredTraining('No complete eligible series are available')
        train, validation, calibration, holdout = rows, [], [], []
    else:
        try:
            earlier, holdout = _take_date_grouped_tail(rows, holdout_series)
            earlier, calibration = _take_date_grouped_tail(earlier, calibration_series)
            train, validation = _take_date_grouped_tail(earlier, validation_series)
            if len(train)<10: raise ValueError('Training block needs at least ten series')
        except ValueError as error:
            raise DeferredTraining(str(error)) from error
    seasons = sorted(files, key=lambda s:min(event_time(r['start_time']) for r in rows if r['season']==s))
    manifest={'schema_version':1,'mode':'production_all_data' if production_all_data else 'rolling','target_season':seasons[-1],
              'source_seasons':seasons,'source_files':files,'splits':dict(train=train,validation=validation,calibration=calibration,holdout=holdout),
              'weighting':weighting,'winning_pick_multiplier':1.0,'cutoff':cutoff or rows[-1]['start_time'],
              'timestamp_convention':'UTC; naive official timestamps are Asia/Shanghai',
              'boundary_rule':'equal_start_times_move_to_later_window','excluded_series':exclusions,
              'feature_vintage_limitation':'Pinned current local hero features; historical feature vintages are unavailable.'}
    manifest['counts']={k:len(v) for k,v in manifest['splits'].items()}
    manifest['series_weights']={f"{r['season']}:{r['match_id']}":series_weight(r,seasons,manifest['cutoff'],weighting) for r in rows}
    weights=[manifest['series_weights'][f"{r['season']}:{r['match_id']}"] for r in train]
    manifest['weight_summary']={'series_count':len(weights),'total_weight':sum(weights),'effective_sample_size':sum(weights)**2/sum(w*w for w in weights)}
    validate_split_manifest(manifest)
    manifest['manifest_sha256']=canonical_sha256(manifest)
    return manifest


def series_weight(row: dict, seasons: list[str], cutoff: str, config: dict) -> float:
    mode=config.get('mode','season_recency')
    if mode=='season_recency':
        decay=float(config.get('decay',.65))
        if not 0<decay<=1: raise ValueError('Season decay must be in (0, 1]')
        return decay**(len(seasons)-1-seasons.index(row['season']))
    age=max(0.,(event_time(cutoff)-event_time(row['start_time'])).total_seconds()/86400)
    if mode=='date_half_life':
        days=float(config['half_life_days'])
        if days<=0: raise ValueError('Half life must be positive')
        return math.exp(-math.log(2)*age/days)
    if mode=='recent_window':
        days=float(config['window_days']); floor=float(config.get('floor',.05))
        if days<=0 or not 0<floor<=1: raise ValueError('Window and floor must be positive')
        return 1. if age<=days else floor
    raise ValueError('Unknown rolling weighting mode')


def split_keys(manifest: dict, name: str) -> set[tuple[str,str]]:
    return {(str(r['season']),str(r['match_id'])) for r in manifest['splits'][name]}


def standard_battle_keys(manifest: dict) -> set[tuple[str,str,str]]:
    """All consumers share the same pinned non-peak battle identity contract."""
    series=set().union(*(split_keys(manifest,name) for name in manifest['splits']))
    keys=set()
    for season,source in manifest['source_files'].items():
        for line in Path(source['matches']).read_text().splitlines():
            if not line.strip():continue
            match=json.loads(line)
            if (season,str(match['match_id'])) not in series:continue
            keys.update((season,str(match['match_id']),str(b['battle_id'])) for b in match['battles'] if not b.get('is_peak_battle') and int(b.get('battle_seq') or 0)!=7)
    return keys


def validate_sources(manifest: dict) -> None:
    validate_split_manifest(manifest)
    for source in manifest['source_files'].values():
        for name in ('matches','decisions'):
            if file_sha256(Path(source[name])) != source[name+'_sha256']:
                raise ValueError('Rolling source changed after manifest creation')


def exported_battles(manifest: dict, history_module, *, splits: tuple[str,...]=('train',)):
    """Lineup labels and roster identities come from the same pinned exports."""
    validate_sources(manifest)
    keys=set().union(*(split_keys(manifest,name) for name in splits))
    battles=[]; team_names={}; hero_names={}
    for season,source in manifest['source_files'].items():
        for line in Path(source['matches']).read_text().splitlines():
            if not line.strip():continue
            match=json.loads(line)
            if (season,str(match['match_id'])) not in keys:continue
            for battle in match['battles']:
                if battle.get('is_peak_battle') or int(battle['battle_seq'])==7:continue
                players={camp:[p for p in battle.get('players',[]) if int(p['camp'])==camp] for camp in (1,2)}
                if any(len({int(p['hero_id']) for p in players[c]})!=5 for c in (1,2)):
                    raise ValueError('Pinned lineup series has incomplete 5v5 roster')
                teams={c:players[c][0] for c in (1,2)}
                for group in players.values():
                    for p in group:
                        team_names[str(p['team_id'])]=str(p['team_name']);hero_names[int(p['hero_id'])]=str(p['hero_name'])
                battles.append(history_module.Battle(league_id=season,match_id=str(match['match_id']),battle_id=str(battle['battle_id']),
                    start_time=event_time(match['start_time']).isoformat(),battle_seq=int(battle['battle_seq']),
                    team_a_id=str(teams[1]['team_id']),team_a_name=str(teams[1]['team_name']),
                    team_b_id=str(teams[2]['team_id']),team_b_name=str(teams[2]['team_name']),
                    heroes_a=tuple(sorted(int(p['hero_id']) for p in players[1])),heroes_b=tuple(sorted(int(p['hero_id']) for p in players[2])),
                    team_a_won=int(battle['win_camp'])==1))
    battles.sort(key=lambda b:(b.start_time,b.match_id,b.battle_seq,b.battle_id))
    return battles,team_names,hero_names


def frozen_context_date(manifest: dict):
    from datetime import timezone, timedelta
    return min(event_time(r['start_time']).astimezone(timezone(timedelta(hours=8))).date() for r in manifest['splits']['validation'])


def training_context_games(manifest: dict):
    from player_context.history import load_historical_games
    validate_sources(manifest)
    ids={str(r['match_id']) for r in manifest['splits']['train']}
    return [game for game in load_historical_games([Path(source['matches']) for source in manifest['source_files'].values()]) if game.match_id in ids and game.battle_seq!=7]
