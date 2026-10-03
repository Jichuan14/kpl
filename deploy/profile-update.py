#!/usr/bin/env python3
"""Profile a real full update in an isolated checkout/database/artifact snapshot.

Run with backend/.venv/bin/python deploy/profile-update.py. Outputs stay under
analysis/outputs/memory_profiles. RSS is sampled for the whole update process
tree, including simultaneously alive children; it is not a container RAM cap.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def process_tree(root_pid: int, processes: dict[int, tuple[int, int, str]]):
    selected = {root_pid}
    while True:
        children = {pid for pid, (parent, _, _) in processes.items() if parent in selected}
        if children <= selected:
            break
        selected.update(children)
    return [{'pid': pid, 'rss_bytes': processes[pid][1], 'command': processes[pid][2]}
            for pid in sorted(selected) if pid in processes]


def sample(root_pid: int):
    output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,args='], text=True)
    processes = {}
    for line in output.splitlines():
        columns = line.strip().split(None, 3)
        if len(columns) == 4:
            pid, parent, rss = map(int, columns[:3])
            processes[pid] = (parent, rss * 1024, columns[3])
    return process_tree(root_pid, processes)


def snapshot(destination: Path):
    # Copy tracked files from the working tree, including current edits. Never
    # copy local credentials, virtual environments, Git state or live outputs.
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    paths += ['analysis/production_training_stage.py', 'analysis/memory_telemetry.py', 'deploy/profile-update.py', 'backend/app/services/pipeline_runner.py',
              'backend/app/services/pipeline_execution.py', 'backend/app/services/pipeline_worker.py']
    for relative in set(paths):
        if not relative or relative.startswith(('.env', 'backend/.env')):
            continue
        source = ROOT / relative
        if source.is_file():
            target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    shutil.copytree(ROOT / 'analysis/exports', destination / 'analysis/exports', dirs_exist_ok=True)
    inputs = ROOT / 'analysis/outputs/models/inputs'
    if inputs.exists():
        shutil.copytree(inputs, destination / 'analysis/outputs/models/inputs')
    database = destination / 'backend/data/kpl_bp.db'
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f'file:{ROOT / "backend/data/kpl_bp.db"}?mode=ro', uri=True) as source:
        with sqlite3.connect(database) as target:
            source.backup(target)
    (destination / '.kpl-profile-snapshot').write_text(str(ROOT))


def execute(args):
    workspace = args.workspace.resolve()
    if not (workspace / '.kpl-profile-snapshot').exists() or not (workspace / 'backend/data/kpl_bp.db').exists():
        raise ValueError('Execution requires an isolated snapshot with a database')
    sys.path.insert(0, str(workspace / 'backend'))
    os.environ['DATABASE_URL'] = f'sqlite:///{workspace / "backend/data/kpl_bp.db"}'
    os.environ['AUTO_MODEL_TRAINING_ENABLED'] = 'true'
    from app.database import init_db
    init_db()
    from app.services import pipeline_jobs
    from types import SimpleNamespace
    if args.offline:
        # Replay the same sync boundary against the snapshot when explicitly
        # requested; do not claim that this includes network sync costs.
        from app.services.sync import SyncService
        def cached_sync(self, **kwargs):
            return {'league_id': args.league_id, 'data_changed': False, 'mode': 'offline_snapshot'}
        SyncService.sync_league_bp = cached_sync
    pipeline_jobs._update_stage = lambda _job, stage: print(f'[full-update] {stage}', flush=True)
    job = SimpleNamespace(id='memory-profile', kind='full_update', league_id=args.league_id,
                          payload='{}', attempts=1)
    result = pipeline_jobs._perform(job)
    (args.report_dir / 'update_result.json').write_text(json.dumps(result, indent=2))


def container_sample():
    value = {'time': time.time(), 'processes': []}
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            fields = dict(line.split(':', 1) for line in path.read_text().splitlines() if ':' in line)
            command = (path.parent / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
            value['processes'].append({'pid': int(path.parent.name),
                'rss_bytes': int(fields.get('VmRSS', '0 kB').split()[0]) * 1024, 'command': command})
        except (OSError, ValueError):
            continue
    value['rss_bytes'] = sum(p['rss_bytes'] for p in value['processes'])
    for name in ('memory.current', 'memory.peak', 'memory.max', 'memory.swap.current', 'memory.swap.max', 'memory.events', 'memory.pressure'):
        try:
            value[name] = (Path('/sys/fs/cgroup') / name).read_text().strip()
        except OSError:
            pass
    return value


def profile_container(args, workspace, report):
    name = 'kpl-memory-profile-' + report.name.lower().replace('_', '-')
    command = ['docker', 'run', '--name', name, '--no-healthcheck', '--memory', '1g', '--memory-swap', '2500m',
               '--cpus', '1.5', '--pids-limit', '256', '--entrypoint', 'python',
               '--mount', f'type=bind,source={workspace},target=/app',
               '--mount', f'type=bind,source={report},target=/profile',
               '-e', 'KPL_MEMORY_LOG=/profile/stages.jsonl', '-e', 'MALLOC_ARENA_MAX=2',
               '-e', 'OMP_NUM_THREADS=1', '-e', 'OPENBLAS_NUM_THREADS=1',
               '-e', 'PYTHONUNBUFFERED=1', args.docker_image,
               '/app/deploy/profile-update.py', '--execute', '--workspace', '/app',
               '--report-dir', '/profile', '--league-id', args.league_id]
    if args.offline:
        command.append('--offline')
    started = time.monotonic(); peak = 0; rss_peak = 0; swap_peak = 0; peak_sample = None; samples = 0
    events = {}; phases = {}
    def retain_events(value):
        for line in value.get('memory.events', '').splitlines():
            key, count = line.split()
            events[key] = max(events.get(key, 0), int(count))
    with (report/'update.log').open('w') as log, (report/'samples.jsonl').open('w') as stream:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                reading = subprocess.run(['docker', 'exec', name, 'python', '/app/deploy/profile-update.py', '--sample-container'], capture_output=True, text=True)
                if reading.returncode == 0:
                    value = json.loads(reading.stdout); samples += 1
                    stream.write(json.dumps(value)+'\n'); stream.flush()
                    current_peak = int(value.get('memory.peak', value['rss_bytes']))
                    peak = max(peak, current_peak)
                    if peak_sample is None or int(value.get('memory.current', value['rss_bytes'])) > int(peak_sample.get('memory.current', peak_sample['rss_bytes'])):
                        peak_sample = value
                    rss_peak = max(rss_peak, value['rss_bytes'])
                    swap_peak = max(swap_peak, int(value.get('memory.swap.current', 0)))
                    retain_events(value)
                    phase = 'sync/analysis/export/validation'
                    for child in value['processes']:
                        if '--stage ' in child['command']:
                            phase = child['command'].split('--stage ', 1)[1].split()[0]
                    stats = phases.setdefault(phase, {'rss_bytes': 0, 'charged_bytes': 0})
                    rss = sum(child['rss_bytes'] for child in value['processes'] if '--sample-container' not in child['command'])
                    stats['rss_bytes'] = max(stats['rss_bytes'], rss)
                    stats['charged_bytes'] = max(stats['charged_bytes'], int(value.get('memory.current', 0)))
                time.sleep(args.interval)
        except BaseException:
            subprocess.run(['docker', 'stop', '-t', '5', name], stdout=subprocess.DEVNULL)
            process.wait()
            raise
        exit_code = process.wait()
    state = json.loads(subprocess.check_output(['docker', 'inspect', '--format', '{{json .State}}', name], text=True))
    state = {key: state[key] for key in ('Status', 'Running', 'OOMKilled', 'ExitCode', 'Error', 'StartedAt', 'FinishedAt')}
    # Stage records also retain kernel peaks even when the sampling interval
    # misses a spike. The observer itself adds a small Python process overhead.
    stages = report/'stages.jsonl'
    if stages.exists():
        for line in stages.read_text().splitlines():
            value = json.loads(line)
            if value.get('memory.peak', '').isdigit():
                peak = max(peak, int(value['memory.peak']))
            retain_events(value)
    architecture = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', args.docker_image], text=True).strip()
    summary = {'exit_code': exit_code, 'wall_seconds': round(time.monotonic()-started, 2),
               'container_peak_memory_bytes': peak, 'sampled_container_peak_rss_bytes': rss_peak,
               'sampled_peak_swap_bytes': swap_peak, 'peak_sample': peak_sample,
               'samples': samples, 'sample_interval_seconds': args.interval, 'container_state': state,
               'container_name': name, 'docker_image': args.docker_image, 'workspace': str(workspace),
               'cpu_architecture': architecture, 'memory_events': events, 'phase_sampled_peaks': phases,
               'league_id': args.league_id, 'network_sync': not args.offline,
               'epochs_each_stage': 30, 'threads': 1, 'ram_limit_bytes': 1073741824,
               'ram_plus_swap_limit_bytes': 2621440000,
               'limitations': ['Includes a small memory-sampling Python process.',
                               'Excludes separate API and web containers.',
                               'Local Docker CPU architecture and host swap may differ from production.']}
    (report/'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--league-id', default='20260004')
    parser.add_argument('--offline', action='store_true', help='Replay local observations; skip official network sync')
    parser.add_argument('--interval', type=float, default=.5)
    parser.add_argument('--report-dir', type=Path)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--execute', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--sample-container', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--docker-image', help='Run in Linux Docker at the production worker RAM/swap/CPU limits')
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error('interval must be positive')
    if args.sample_container:
        print(json.dumps(container_sample()))
        return
    if args.execute:
        execute(args)
        return
    report = (args.report_dir or ROOT / 'analysis/outputs/memory_profiles' / time.strftime('%Y%m%dT%H%M%S')).resolve()
    report.mkdir(parents=True, exist_ok=False)
    workspace = Path(tempfile.mkdtemp(prefix='kpl-memory-profile-'))
    snapshot(workspace)
    print(f'Isolated workspace: {workspace}\nReport: {report}', flush=True)
    if args.docker_image:
        raise SystemExit(profile_container(args, workspace, report))
    command = [sys.executable, str(workspace / 'deploy/profile-update.py'), '--execute',
               '--workspace', str(workspace), '--report-dir', str(report), '--league-id', args.league_id]
    if args.offline:
        command.append('--offline')
    env = {**os.environ, 'KPL_MEMORY_LOG': str(report / 'stages.jsonl'),
           'MALLOC_ARENA_MAX': '2', 'PYTHONUNBUFFERED': '1',
           'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    started = time.monotonic(); peak = 0; peak_processes = []; samples = 0
    sample(os.getpid())  # Check process-monitoring access before starting work.
    with (report / 'update.log').open('w') as log, (report / 'samples.jsonl').open('w') as stream:
        process = subprocess.Popen(command, cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                processes = sample(process.pid); total = sum(p['rss_bytes'] for p in processes)
                samples += 1
                stream.write(json.dumps({'time': time.time(), 'rss_bytes': total, 'processes': processes}) + '\n'); stream.flush()
                if total > peak:
                    peak, peak_processes = total, processes
                time.sleep(args.interval)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        exit_code = process.wait()
    summary = {'exit_code': exit_code, 'wall_seconds': round(time.monotonic() - started, 2),
               'sampled_process_tree_peak_rss_bytes': peak, 'peak_processes': peak_processes,
               'samples': samples, 'sample_interval_seconds': args.interval,
               'platform': sys.platform, 'workspace': str(workspace), 'league_id': args.league_id,
               'network_sync': not args.offline, 'epochs_each_stage': 30, 'threads': 1,
               'limitations': ['Sampled RSS can miss short spikes and double-count shared pages.',
                               'Excludes website and other host services; Linux cgroup memory may differ.']}
    (report / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    raise SystemExit(exit_code)


if __name__ == '__main__':
    main()
