"""Small, optional memory records for pipeline subprocesses (no ML imports)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import sys
import time


def record(event: str, stage: str) -> None:
    destination = os.environ.get('KPL_MEMORY_LOG')
    if not destination:
        return
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    value = {'event': event, 'stage': stage, 'pid': os.getpid(), 'time': time.time(),
             'process_peak_rss_bytes': int(peak if sys.platform == 'darwin' else peak * 1024)}
    # These are actual container memory/swap/pressure values when cgroup v2 is
    # namespaced into a Linux container. RSS alone omits charged file caches.
    for name in ('memory.current', 'memory.peak', 'memory.max', 'memory.swap.current', 'memory.swap.max', 'memory.events', 'memory.pressure'):
        path = Path('/sys/fs/cgroup') / name
        try:
            value[name] = path.read_text().strip()
        except OSError:
            pass
    try:
        fields = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        for name in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
            value['host_' + name + '_bytes'] = int(fields[name].split()[0]) * 1024
        for name in ('memory', 'io'):
            value['host_' + name + '_pressure'] = (Path('/proc/pressure') / name).read_text().strip()
    except (OSError, KeyError, ValueError):
        pass
    try:
        with Path(destination).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value) + '\n')
    except OSError as error:
        print(f'Memory telemetry unavailable: {error}', file=sys.stderr)
