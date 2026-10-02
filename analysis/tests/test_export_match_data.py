import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_match_data import export_match, list_matches, write_jsonl


@pytest.mark.parametrize('players', [False, True])
@pytest.mark.parametrize('match_count', [3, 101])
def test_batched_export_matches_single_record_output_and_bounds_queries(tmp_path, players, match_count):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE matches (match_id TEXT, league_id TEXT, match_stage TEXT, start_time TEXT,
            bo INTEGER, win_camp INTEGER, camp1_team_id TEXT, camp1_team_name TEXT,
            camp2_team_id TEXT, camp2_team_name TEXT, camp1_score INTEGER, camp2_score INTEGER);
        CREATE TABLE battles (battle_id TEXT, match_id TEXT, battle_seq INTEGER, win_camp INTEGER,
            game_duration INTEGER, status INTEGER);
        CREATE TABLE battle_bps (battle_id TEXT, bp_order INTEGER, action_type INTEGER,
            camp INTEGER, hero_id INTEGER, hero_name TEXT, position INTEGER);
    ''')
    if players:
        # Legacy schema deliberately lacks all performance columns.
        conn.execute('''CREATE TABLE battle_players (battle_id TEXT, camp INTEGER, team_id TEXT,
            team_name TEXT, match_camp INTEGER, player_name TEXT, hero_id INTEGER,
            hero_name TEXT, position INTEGER, position_desc TEXT)''')
    for match in range(match_count):
        conn.execute('INSERT INTO matches VALUES (?,"s4","final","2026-10-01",5,1,"a","A","b","B",2,0)', (str(match),))
        for seq in [2, 1]:
            id = f'{match}-{seq}'
            conn.execute('INSERT INTO battles VALUES (?,?,?,1,100,2)', (id, str(match), seq))
            for order in [2, 1]:
                conn.execute('INSERT INTO battle_bps VALUES (?,?,1,1,101,"Hero",2)', (id, order))
            if players:
                conn.execute('INSERT INTO battle_players VALUES (?,1,"a","A",1,"Player",101,"Hero",2,"mid")', (id,))
    matches = list_matches(conn, 's4')
    expected = [export_match(conn, row) for row in matches]
    queries = []
    conn.set_trace_callback(queries.append)
    output = tmp_path / 'matches.jsonl'
    assert write_jsonl(conn, matches, output) == match_count
    conn.set_trace_callback(None)
    assert [json.loads(line) for line in output.read_text().splitlines()] == expected
    assert len(queries) == (2 if players else 1) + (4 if players else 2) * ((match_count + 99) // 100)
    assert [row['battle_seq'] for row in expected[0]['battles']] == [1, 2]
    assert write_jsonl(conn, [], output) == 0
    assert output.read_text() == ''
    conn.close()


def test_current_performance_and_side_swap_survive_batched_export(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Match, Battle, BattleBp, BattlePlayer
    path = tmp_path / 'source.db'
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Match(match_id='m', league_id='s4', camp1_team_id='a', camp1_team_name='A', camp2_team_id='b', camp2_team_name='B'))
        db.add(Battle(battle_id='g', match_id='m', league_id='s4', win_camp=1))
        db.add(BattleBp(battle_id='g', league_id='s4', action_type=1, hero_id=101, camp=1))
        db.add(BattlePlayer(battle_id='g', match_id='m', league_id='s4', team_id='b', team_name='B',
            player_name='Player', hero_id=101, camp=1, match_camp=2, performance_data_available=1,
            kill_num=3, death_num=2, assist_num=7, gold=1234, hurt_total_rate=0.3,
            mvp_score=12.5, participation_rate=70.0, is_mvp=1))
        db.commit()
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    matches = list_matches(conn, 's4')
    expected = export_match(conn, matches[0])
    output = tmp_path / 'export.jsonl'; write_jsonl(conn, matches, output)
    actual = json.loads(output.read_text())
    assert actual == expected
    battle = actual['battles'][0]
    assert battle['winner_team_id'] == 'b'
    assert battle['bp_actions'][0]['match_camp'] == 2
    player = battle['players'][0]
    assert player['performance_data_available'] and player['is_mvp']
    assert (player['kills'], player['deaths'], player['assists'], player['gold']) == (3, 2, 7, 1234)
    assert player['damage']['total_rate'] == 0.3
    conn.close(); engine.dispose()
