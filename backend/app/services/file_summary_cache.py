"""Bounded summaries invalidated by file replacement or modification."""
from collections import OrderedDict
from pathlib import Path
from threading import Lock

_cache = OrderedDict()
_lock = Lock()
MAX_ENTRIES = 256


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def file_summary(path: Path, loader, *, variant="default"):
    key = (path.absolute(), variant)
    signature = _signature(path)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == signature:
            _cache.move_to_end(key)
            return cached[1]
    value = loader(path)
    # Never label an old read as belonging to a newly published generation.
    if _signature(path) == signature:
        with _lock:
            _cache[key] = (signature, value)
            _cache.move_to_end(key)
            while len(_cache) > MAX_ENTRIES:
                _cache.popitem(last=False)
    return value
