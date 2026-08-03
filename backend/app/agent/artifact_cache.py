"""Thread-safe, version-aware access to generated JSONL analysis artifacts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable, TypeVar

from app.services.analysis_pipeline import OUTPUT_ROOT

logger = logging.getLogger(__name__)

IndexValue = TypeVar("IndexValue")


class ArtifactFormatError(ValueError):
    """Raised when a generated artifact is not valid object-per-line JSONL."""


@dataclass(frozen=True)
class ArtifactVersion:
    modified_ns: int
    size: int
    inode: int

    @property
    def token(self) -> str:
        return f"{self.modified_ns}:{self.size}:{self.inode}"


@dataclass(frozen=True)
class ArtifactSnapshot:
    league_id: str
    filename: str
    version: ArtifactVersion
    rows: tuple[dict[str, Any], ...]
    cache_hit: bool


@dataclass
class _CacheEntry:
    version: ArtifactVersion
    rows: tuple[dict[str, Any], ...]
    indexes: dict[str, Any] = field(default_factory=dict)


class JsonlArtifactCache:
    """Load each approved season artifact once per filesystem version."""

    def __init__(self, root: Path = OUTPUT_ROOT):
        self.root = root
        self._entries: dict[Path, _CacheEntry] = {}
        self._lock = RLock()

    def load(self, league_id: str, filename: str) -> ArtifactSnapshot:
        """Return immutable row membership and reload after file replacement."""
        path = self._resolve_path(league_id, filename)
        started = perf_counter()
        with self._lock:
            version = self._version(path)
            cached = self._entries.get(path)
            if cached is not None and cached.version == version:
                logger.debug(
                    "agent_artifact_cache_hit",
                    extra={
                        "league_id": league_id,
                        "artifact_name": filename,
                        "artifact_version": version.token,
                        "row_count": len(cached.rows),
                    },
                )
                return self._snapshot(
                    league_id,
                    filename,
                    cached,
                    cache_hit=True,
                )

            rows, stable_version = self._read_stable(path, version)
            entry = _CacheEntry(version=stable_version, rows=rows)
            self._entries[path] = entry

        logger.info(
            "agent_artifact_loaded",
            extra={
                "league_id": league_id,
                "artifact_name": filename,
                "artifact_version": stable_version.token,
                "row_count": len(rows),
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        return self._snapshot(
            league_id,
            filename,
            entry,
            cache_hit=False,
        )

    def get_index(
        self,
        league_id: str,
        filename: str,
        index_name: str,
        builder: Callable[[tuple[dict[str, Any], ...]], IndexValue],
    ) -> tuple[IndexValue, ArtifactSnapshot]:
        """Build and reuse one named lookup index for an artifact version."""
        if not index_name or len(index_name) > 100:
            raise ValueError("A bounded index_name is required")
        path = self._resolve_path(league_id, filename)
        while True:
            snapshot = self.load(league_id, filename)
            with self._lock:
                entry = self._entries[path]
                if entry.version != snapshot.version:
                    continue
                if index_name not in entry.indexes:
                    entry.indexes[index_name] = builder(entry.rows)
                    logger.info(
                        "agent_artifact_index_built",
                        extra={
                            "league_id": league_id,
                            "artifact_name": filename,
                            "artifact_version": entry.version.token,
                            "index_name": index_name,
                        },
                    )
                return entry.indexes[index_name], snapshot

    def clear(self) -> None:
        """Drop cached rows and indexes, primarily for tests and maintenance."""
        with self._lock:
            self._entries.clear()

    def _resolve_path(self, league_id: str, filename: str) -> Path:
        if not league_id or not all(
            character.isalnum() or character in "-_" for character in league_id
        ):
            raise ValueError("Invalid league_id")
        candidate = Path(filename)
        if (
            not filename
            or candidate.name != filename
            or candidate.suffix != ".jsonl"
        ):
            raise ValueError("Invalid JSONL artifact filename")
        return self.root / league_id / filename

    @staticmethod
    def _version(path: Path) -> ArtifactVersion:
        stat = path.stat()
        return ArtifactVersion(
            modified_ns=stat.st_mtime_ns,
            size=stat.st_size,
            inode=stat.st_ino,
        )

    def _read_stable(
        self,
        path: Path,
        initial_version: ArtifactVersion,
    ) -> tuple[tuple[dict[str, Any], ...], ArtifactVersion]:
        version = initial_version
        for _ in range(3):
            rows = self._read_rows(path)
            current = self._version(path)
            if current == version:
                return rows, current
            version = current
        raise RuntimeError(f"Artifact changed repeatedly while loading: {path.name}")

    @staticmethod
    def _read_rows(path: Path) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ArtifactFormatError(
                        f"Invalid JSON in {path.name} at line {line_number}"
                    ) from exc
                if not isinstance(row, dict):
                    raise ArtifactFormatError(
                        f"Expected an object in {path.name} at line {line_number}"
                    )
                rows.append(row)
        return tuple(rows)

    @staticmethod
    def _snapshot(
        league_id: str,
        filename: str,
        entry: _CacheEntry,
        *,
        cache_hit: bool,
    ) -> ArtifactSnapshot:
        return ArtifactSnapshot(
            league_id=league_id,
            filename=filename,
            version=entry.version,
            rows=entry.rows,
            cache_hit=cache_hit,
        )


artifact_cache = JsonlArtifactCache()
