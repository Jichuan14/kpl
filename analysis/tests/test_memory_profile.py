"""Checks for whole-update accounting and isolation from the live workspace."""
import importlib.util
from pathlib import Path
import sqlite3
from unittest.mock import patch


path = Path(__file__).resolve().parents[2] / 'deploy/profile-update.py'
spec = importlib.util.spec_from_file_location('memory_profile', path)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def test_tree_accounts_for_concurrent_descendants_and_excludes_other_services():
    processes = {10: (1, 100, 'worker'), 20: (10, 400, 'trainer'),
                 30: (20, 300, 'exporter'), 40: (10, 50, 'beat'),
                 50: (1, 200, 'unrelated service')}
    result = profile.process_tree(10, processes)
    assert {row['pid'] for row in result} == {10, 20, 30, 40}
    assert sum(row['rss_bytes'] for row in result) == 850


def test_snapshot_owns_database_and_artifacts_without_credentials_or_active_pointer(tmp_path):
    root = tmp_path/'live'; target = tmp_path/'snapshot'
    database = root/'backend/data/kpl_bp.db'; database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE evidence(value TEXT)')
        connection.execute("INSERT INTO evidence VALUES ('original')")
    (root/'README.md').write_text('Current working-tree edit')
    (root/'.env.production').write_text('PRIVATE_CONFIGURATION')
    (root/'backend/.env').write_text('PRIVATE_CONFIGURATION')
    exports = root/'analysis/exports/20260004'; exports.mkdir(parents=True)
    (exports/'matches.jsonl').write_text('{}\n')
    registry = root/'analysis/outputs/models'; (registry/'inputs').mkdir(parents=True)
    (registry/'current.json').write_text('{"version":"live"}')
    (registry/'inputs/herolist.json').write_text('[]')
    with patch.object(profile, 'ROOT', root), patch.object(profile.subprocess, 'check_output', return_value=b'README.md\0.env.production\0backend/.env\0'):
        profile.snapshot(target)
    assert (target/'README.md').read_text() == 'Current working-tree edit'
    assert not (target/'.env.production').exists()
    assert not (target/'backend/.env').exists()
    assert not (target/'analysis/outputs/models/current.json').exists()
    assert (target/'analysis/outputs/models/inputs/herolist.json').exists()
    assert (target/'.kpl-profile-snapshot').exists()
    with sqlite3.connect(target/'backend/data/kpl_bp.db') as connection:
        connection.execute("UPDATE evidence SET value='benchmark'")
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT value FROM evidence').fetchone()[0] == 'original'
    assert (registry/'current.json').read_text() == '{"version":"live"}'
