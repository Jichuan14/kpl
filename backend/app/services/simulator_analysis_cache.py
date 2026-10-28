"""Process-local sharing of public simulator calculations, never request metadata.

Completed values are copied for every caller. Inflight work is not evicted; the
existing public request limiter remains responsible for admission/concurrency.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Callable

ALGORITHM_VERSION = "simulator-analysis-v1"


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_signature(path: Path) -> tuple | None:
    """Cheap identity, including atomic replacements and preserved mtimes."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class FileDigests:
    def __init__(self, max_entries: int = 256):
        self.max_entries = max_entries
        self._values: OrderedDict[tuple, str] = OrderedDict()
        self._lock = Lock()

    def fingerprint(self, path: Path) -> tuple[tuple | None, str | None]:
        # Retry a publication racing the read. Never memoize mixed generations.
        while True:
            signature = file_signature(path)
            if signature is None:
                return None, None
            key = (str(path), signature)
            with self._lock:
                cached = self._values.get(key)
                if cached is not None:
                    self._values.move_to_end(key)
                    return signature, cached
            try:
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
            except FileNotFoundError:
                continue
            if signature != file_signature(path):
                continue
            value = digest.hexdigest()
            with self._lock:
                self._values[key] = value
                self._values.move_to_end(key)
                while len(self._values) > self.max_entries:
                    self._values.popitem(last=False)
            return signature, value


_file_digests = FileDigests()


@dataclass(frozen=True)
class Dependencies:
    identity: Any
    generation: str
    content: str


def analysis_dependencies(league_id: str, model_type: str, *, recommendation: bool) -> Dependencies:
    # Import here to keep file_signature usable by the artifact loaders.
    from app.services import draft_simulator as draft, lineup_value, ban_recommender
    from app.services.model_registry import current_bundle, component_path

    handle = current_bundle()
    if handle is not None:
        # Handles have already verified these immutable component digests. No
        # megabyte hashing or mutable active-pointer lookup on warm requests.
        components = handle.manifest["components"]
        identity = {"version": handle.version, "metadata": handle.metadata()}
        return Dependencies(identity, canonical_digest([str(handle.root), identity, components]),
                            canonical_digest(components))

    paths = {
        "catalog": draft.model_path(league_id),
        "hero_roles": component_path("herolist.json", draft.OFFICIAL_HEROLIST_PATH),
        "team_tendencies": component_path("team_action_tendencies.jsonl", draft.OUTPUT_ROOT / league_id / "team_action_tendencies.jsonl"),
    }
    settings = {}
    if model_type == "personalized":
        paths.update(policy=draft.personalized_model_path(league_id),
                     context=draft.personalized_context_path(league_id),
                     calibration=draft.personalized_calibration_path(league_id))
    elif model_type in {"sequence", "learnable"}:
        paths.update(policy=draft.sequence_model_path(league_id) if model_type == "sequence" else draft.learnable_model_path(league_id),
                     draft_features=draft.DRAFT_FEATURES_PATH,
                     specialty_features=draft.LEGACY_SPECIALTY_FEATURES_PATH)
        if model_type == "sequence":
            paths["calibration"] = draft.sequence_calibration_path(league_id)
            settings["calibration_mode"] = os.getenv("DRAFT_SEQUENCE_CALIBRATION", "off").strip().lower()
    if recommendation:
        paths.update(lineup_value=lineup_value.lineup_value_model_path(league_id),
                     tactical_roles=component_path("hero_tactical_roles.json", lineup_value.TACTICAL_ROLES_PATH),
                     ban_value=ban_recommender.ban_value_model_path(league_id))
    fingerprints = {name: _file_digests.fingerprint(path) for name, path in paths.items()}
    generation = {name: [str(paths[name]), signature, digest]
                  for name, (signature, digest) in fingerprints.items()}
    content = {name: digest for name, (_, digest) in fingerprints.items()}
    return Dependencies("legacy", canonical_digest([generation, settings]), canonical_digest([content, settings]))


def default_recommendation_seed(league_id: str, model_type: str, state: dict, dependencies: Dependencies) -> int:
    # Excludes paths, timestamps, version labels, top_k and risk_mode: identical
    # content/state continues the same sampling stream across restarts/copies.
    return int(canonical_digest(["recommendation-seed-v1", league_id, model_type, state, dependencies.content])[:16], 16)


def analysis_key(endpoint: str, league_id: str, model_type: str, state: dict,
                 dependencies: Dependencies, **settings: Any) -> str:
    return canonical_digest([ALGORITHM_VERSION, endpoint, league_id, model_type, state,
                             dependencies.identity, dependencies.generation, settings])


class SimulatorAnalysisCache:
    def __init__(self, max_entries: int = 128, ttl_seconds: float = 60, clock: Callable = monotonic):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._flights: dict[str, Future] = {}

    def clear(self) -> None:
        """Drop completed entries, preserving active flights and their followers."""
        with self._lock:
            self._values.clear()

    def get_or_compute(self, key: str, compute: Callable, *, is_current: Callable[[], bool]) -> Any:
        with self._lock:
            now = self._clock()
            for expired in [k for k, (expires, _) in self._values.items() if expires <= now]:
                del self._values[expired]
            cached = self._values.get(key)
            if cached is not None:
                self._values.move_to_end(key)
            future = self._flights.get(key)
            leader = cached is None and future is None
            if leader:
                future = Future()
                self._flights[key] = future
        if cached is not None:
            return deepcopy(cached[1])
        if not leader:
            return deepcopy(future.result())
        try:
            result = deepcopy(compute())
            current = is_current()
            with self._lock:
                if current:
                    self._values[key] = (self._clock() + self.ttl_seconds, result)
                    self._values.move_to_end(key)
                    while len(self._values) > self.max_entries:
                        self._values.popitem(last=False)
                del self._flights[key]
            future.set_result(result)
        except BaseException as error:
            with self._lock:
                self._flights.pop(key, None)
            future.set_exception(error)
            raise
        return deepcopy(result)


simulator_analysis_cache = SimulatorAnalysisCache()
