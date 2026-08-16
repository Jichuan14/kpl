"""Task 3: deterministic, read-only retrieval over the local patch index.

This module turns the SQLite/FTS5 read model built in :mod:`patch_index` into
the evidence-only response defined in ``app.agent.tools.patches``.  It does
not call an LLM and it is intentionally not registered as a Coach tool yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import sqlite3

from app.agent.tools.patches import (
    PatchEvidenceCard,
    PatchSearchResponse,
    SearchPatchNotesArguments,
)
from app.knowledge.patch_index import DEFAULT_INDEX_PATH


MAX_CANDIDATES = 80
MAX_EXCERPT_LENGTH = 1_200
SCHEMA_VERSION = "2"


class PatchIndexUnavailableError(RuntimeError):
    """Raised when patch retrieval is requested before a usable index exists."""


@dataclass(frozen=True)
class _CandidateChunk:
    """One internal FTS match, kept separate from the public tool contract."""

    announcement_id: str
    title: str
    published_at: date
    source_url: str
    source_hash: str
    heading_path: tuple[str, ...]
    hero_names: tuple[str, ...]
    equipment_names: tuple[str, ...]
    text: str
    fts_score: float


class PatchRetriever:
    """Retrieve source-attributed game-patch evidence using fixed FTS ranking."""

    def __init__(self, *, index_path: Path = DEFAULT_INDEX_PATH) -> None:
        self.index_path = index_path

    def search(self, arguments: SearchPatchNotesArguments) -> PatchSearchResponse:
        """Return at most ``arguments.limit`` official patch-note excerpts.

        The only ranking signal is SQLite FTS5's deterministic ``bm25`` score,
        followed by a stable date and ID tie-break.  Dates and an explicit hero
        are filters, never LLM-inferred facts.
        """
        fts_query = self._fts_query(arguments)
        with self._open_index() as db:
            index_version = self._index_version(db)
            if not fts_query:
                return self._response(
                    index_version=index_version,
                    results=(),
                    warnings=(
                        "The query did not contain searchable keywords after "
                        "normalization.",
                    ),
                )
            candidates = self._find_candidates(db, arguments, fts_query)

        if arguments.hero_name:
            candidates = tuple(
                candidate
                for candidate in candidates
                if arguments.hero_name in candidate.hero_names
            )

        cards = tuple(
            self._card(candidate)
            for candidate in candidates[: arguments.limit]
        )
        warnings: tuple[str, ...] = ()
        if not cards:
            warnings = (
                "No matching official patch-note evidence was found in the "
                "local index.",
            )
        return self._response(
            index_version=index_version,
            results=cards,
            warnings=warnings,
        )

    def _open_index(self) -> sqlite3.Connection:
        path = self.index_path.resolve()
        if not path.is_file():
            raise PatchIndexUnavailableError(
                f"Patch index not found: {path}. Build it before retrieving."
            )
        try:
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro",
                uri=True,
            )
        except sqlite3.Error as exc:
            raise PatchIndexUnavailableError(
                f"Could not open patch index: {path}"
            ) from exc
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _index_version(db: sqlite3.Connection) -> str:
        try:
            rows = db.execute(
                "SELECT key, value FROM index_metadata "
                "WHERE key IN ('schema_version', 'index_version')"
            ).fetchall()
        except sqlite3.Error as exc:
            raise PatchIndexUnavailableError("Patch index has no readable metadata") from exc
        metadata = {str(row["key"]): str(row["value"]) for row in rows}
        if metadata.get("schema_version") != SCHEMA_VERSION:
            raise PatchIndexUnavailableError("Patch index schema version is unsupported")
        version = metadata.get("index_version", "").strip()
        if not version:
            raise PatchIndexUnavailableError("Patch index has no index version")
        return version

    def _find_candidates(
        self,
        db: sqlite3.Connection,
        arguments: SearchPatchNotesArguments,
        fts_query: str,
    ) -> tuple[_CandidateChunk, ...]:
        filters = [
            "patch_chunks_fts MATCH ?",
            "c.heading_path NOT LIKE '%\"Source boundary\"%'",
        ]
        parameters: list[object] = [fts_query]
        if "装备" in arguments.query:
            filters.append("c.equipment_names <> '[]'")
        if arguments.from_date is not None:
            filters.append("date(c.published_at) >= ?")
            parameters.append(arguments.from_date.isoformat())
        if arguments.to_date is not None:
            filters.append("date(c.published_at) <= ?")
            parameters.append(arguments.to_date.isoformat())
        where_clause = " AND ".join(filters)
        query = f"""
            SELECT
                c.announcement_id,
                c.title,
                c.published_at,
                c.source_url,
                c.source_hash,
                c.heading_path,
                c.hero_names,
                c.equipment_names,
                c.text,
                bm25(patch_chunks_fts, 1.0, 0.5, 0.75, 0.75, 1.25) AS fts_score
            FROM patch_chunks_fts
            JOIN patch_chunks AS c ON c.chunk_id = patch_chunks_fts.chunk_id
            WHERE {where_clause}
            ORDER BY fts_score ASC, c.published_at DESC, c.chunk_id ASC
            LIMIT ?
        """
        parameters.append(MAX_CANDIDATES)
        try:
            rows = db.execute(query, parameters).fetchall()
        except sqlite3.Error as exc:
            raise PatchIndexUnavailableError("Patch index search failed") from exc
        return tuple(self._candidate(row) for row in rows)

    @staticmethod
    def _candidate(row: sqlite3.Row) -> _CandidateChunk:
        try:
            published_at = date.fromisoformat(str(row["published_at"])[:10])
            heading_path = tuple(json.loads(str(row["heading_path"])))
            hero_names = tuple(json.loads(str(row["hero_names"])))
            equipment_names = tuple(json.loads(str(row["equipment_names"])))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PatchIndexUnavailableError("Patch index contains invalid chunk data") from exc
        if not heading_path:
            raise PatchIndexUnavailableError("Patch index contains incomplete evidence")
        return _CandidateChunk(
            announcement_id=str(row["announcement_id"]),
            title=str(row["title"]),
            published_at=published_at,
            source_url=str(row["source_url"]),
            source_hash=str(row["source_hash"]),
            heading_path=heading_path,
            hero_names=hero_names,
            equipment_names=equipment_names,
            text=str(row["text"]),
            fts_score=float(row["fts_score"]),
        )

    @staticmethod
    def _card(candidate: _CandidateChunk) -> PatchEvidenceCard:
        return PatchEvidenceCard(
            announcement_id=candidate.announcement_id,
            title=candidate.title,
            published_at=candidate.published_at,
            entity_type=PatchRetriever._entity_type(candidate),
            hero_names=list(candidate.hero_names),
            equipment_names=list(candidate.equipment_names),
            heading_path=list(candidate.heading_path),
            excerpt=PatchRetriever._excerpt(candidate.text),
            source_url=candidate.source_url,
            source_hash=candidate.source_hash,
        )

    @staticmethod
    def _entity_type(candidate: _CandidateChunk) -> str:
        if candidate.equipment_names:
            return "equipment"
        if candidate.hero_names:
            return "hero"
        return "system"

    @staticmethod
    def _excerpt(text: str) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= MAX_EXCERPT_LENGTH:
            return normalized
        return f"{normalized[: MAX_EXCERPT_LENGTH - 1].rstrip()}…"

    @staticmethod
    def _response(
        *,
        index_version: str,
        results: tuple[PatchEvidenceCard, ...],
        warnings: tuple[str, ...],
    ) -> PatchSearchResponse:
        return PatchSearchResponse(
            source_type="tencent_patch_notes",
            index_version=index_version,
            result_count=len(results),
            results=list(results),
            warnings=list(warnings),
        )

    @classmethod
    def _fts_query(cls, arguments: SearchPatchNotesArguments) -> str:
        """Build an FTS expression from plain-text terms only.

        FTS5's ``unicode61`` tokenizer does not segment Chinese sentences.  For
        a Chinese run, overlapping two-character terms preserve practical
        matching for prompts such as ``刘备最近有什么改动`` without accepting raw
        FTS syntax from the caller.
        """
        terms: list[str] = []
        if arguments.hero_name:
            terms.extend(cls._search_terms(arguments.hero_name))
        terms.extend(cls._search_terms(arguments.query))
        unique_terms = list(dict.fromkeys(terms))[:24]
        return " OR ".join(f'"{term}"*' for term in unique_terms)

    @staticmethod
    def _search_terms(text: str) -> tuple[str, ...]:
        terms: list[str] = []
        for chinese_run in re.findall(r"[\u4e00-\u9fff]+", text):
            if len(chinese_run) <= 4:
                terms.append(chinese_run)
            if len(chinese_run) >= 2:
                terms.extend(
                    chinese_run[position : position + 2]
                    for position in range(len(chinese_run) - 1)
                )
        terms.extend(
            word.casefold()
            for word in re.findall(r"[A-Za-z0-9_]{2,}", text)
        )
        return tuple(terms)
