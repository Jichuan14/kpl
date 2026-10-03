#!/usr/bin/env python3
"""Compare real HTTP full updates using isolated API/worker containers.

Uses the same dependency image and frozen initial database/exports for both
architectures. API mode overlays current code; worker/sqlite modes preserve
the saved worker version. No live data or credentials are mounted. Requires Docker.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def request(port, path, body=None):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def memory(name):
    names = ['memory.current', 'memory.peak', 'memory.swap.current', 'memory.events']
    raw = docker('exec', name, 'cat', *['/sys/fs/cgroup/' + n for n in names]).splitlines()
    return {'current': int(raw[0]), 'peak': int(raw[1]), 'swap': int(raw[2]),
            'events': dict((k, int(v)) for k, v in (line.split() for line in raw[3:]))}


def run(args):
    report = args.report.resolve(); report.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='kpl-queue-' + args.mode + '-'))
    shutil.copytree(args.baseline, work, dirs_exist_ok=True)
    if args.mode == 'api':
        paths = docker_source_files()
        for relative in paths:
            source = ROOT / relative
            target = work / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
            elif target.is_file():
                target.unlink()
    with sqlite3.connect(work / 'backend/data/kpl_bp.db') as db:
        db.execute('DELETE FROM pipeline_jobs'); db.commit()
    tag = 'kpl-queue-' + args.mode + '-' + str(int(time.time()))
    network = tag; containers = {}; port = args.port
    docker('network', 'create', network)
    env = ['DATABASE_URL=sqlite:////app/backend/data/kpl_bp.db', 'AUTO_MODEL_TRAINING_ENABLED=true',
           'MALLOC_ARENA_MAX=2', 'OMP_NUM_THREADS=1', 'OPENBLAS_NUM_THREADS=1',
           'PYTHONUNBUFFERED=1', 'KPL_MEMORY_LOG=/profile/stages.jsonl']
    def start(component, command):
        name = tag + '-' + component; containers[component] = name
        options = ['run', '-d', '--name', name, '--network', network, '--no-healthcheck',
                   '--memory', '1g', '--memory-swap', '2500m', '--cpus', '1.5', '--pids-limit', '256',
                   '--mount', f'type=bind,source={work},target=/app',
                   '--mount', f'type=bind,source={report},target=/profile', '--workdir', '/app/backend']
        if component == 'api': options += ['-p', f'127.0.0.1:{port}:8000']
        for value in env: options += ['-e', value]
        docker(*options, '--entrypoint', command[0], args.image, *command[1:])
    samples = []; health = []; job = None
    started = None
    try:
        start('api', ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1'])
        for _ in range(60):
            try:
                request(port, '/health'); break
            except Exception: time.sleep(1)
        else: raise RuntimeError('API did not start')
        if args.mode != 'api':
            start('worker', ['python', '-m', 'app.services.pipeline_worker'])
        time.sleep(12)
        def reading(phase):
            with concurrent.futures.ThreadPoolExecutor() as pool:
                values = dict(zip(containers, pool.map(memory, containers.values())))
            value = {'elapsed': None if started is None else time.monotonic() - started, 'phase': phase,
                     'components': values, 'total': sum(v['current'] for v in values.values())}
            samples.append(value)
            with (report / 'samples.jsonl').open('a') as stream: stream.write(json.dumps(value) + '\n')
            return value
        for _ in range(4): reading('idle'); time.sleep(1)
        started = time.monotonic()
        job = request(port, '/api/jobs/full-update', {'league_id': '20260004'})['data']
        (report / 'submission.json').write_text(json.dumps(job, indent=2))
        first_running = None
        while time.monotonic() - started < 2400:
            value = reading('update')
            before = time.monotonic()
            try:
                request(port, '/health'); health.append({'seconds': time.monotonic() - before, 'ok': True})
                job = request(port, job['status_url'])['data']
            except Exception as exc:
                health.append({'seconds': time.monotonic() - before, 'ok': False, 'error': str(exc)})
                raise
            value['job_stage'] = job['stage']
            if job['status'] == 'running' and first_running is None: first_running = time.monotonic() - started
            if job['status'] in ('completed', 'failed'): break
            time.sleep(args.interval)
        elapsed = time.monotonic() - started
        summary = {'mode': args.mode, 'image': args.image, 'workspace': str(work), 'containers': containers,
                   'status': job['status'], 'elapsed_seconds': elapsed, 'start_latency_seconds': first_running,
                   'idle_total_bytes': sum(s['total'] for s in samples if s['phase'] == 'idle') / 4,
                   'peak_sampled_total_bytes': max(s['total'] for s in samples),
                   'components': {k: {'idle_bytes': sum(s['components'][k]['current'] for s in samples if s['phase'] == 'idle') / 4,
                       'peak_bytes': max(s['components'][k]['peak'] for s in samples),
                       'peak_sampled_swap_bytes': max(s['components'][k]['swap'] for s in samples),
                       'events': samples[-1]['components'][k]['events']} for k in containers},
                   'health_requests': len(health), 'health_failures': sum(not h['ok'] for h in health),
                   'health_max_seconds': max(h['seconds'] for h in health), 'job': job}
        (report / 'summary.json').write_text(json.dumps(summary, indent=2)); print(json.dumps(summary, indent=2), flush=True)
    finally:
        for key, name in containers.items():
            logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True)
            (report / (key + '.log')).write_text(logs.stdout + logs.stderr)
            subprocess.run(['docker', 'stop', '-t', '15', name], capture_output=True)
        (report / 'workspace.txt').write_text(str(work))


def docker_source_files():
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    return [p for p in paths if p and not p.startswith(('.env', 'backend/.env'))] + ['backend/app/services/pipeline_worker.py', 'backend/app/services/pipeline_runner.py', 'backend/app/services/pipeline_execution.py']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--mode', choices=['sqlite', 'worker', 'api'], required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--image', default='kpl-memory-profile:local')
    parser.add_argument('--port', type=int, default=18381)
    parser.add_argument('--interval', type=float, default=2)
    run(parser.parse_args())
