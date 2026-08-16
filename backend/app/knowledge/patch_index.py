"""Task 2 skeleton: build a local SQLite FTS index from Tencent patch documents.

The input corpus is under ``knowledge/`` at the repository root. The output is
a rebuildable read model at ``backend/data/kpl_patch_index.db``. This module
must not call an LLM, access the public internet, or answer a user question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS_ROOT = REPOSITORY_ROOT / "knowledge"
DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "kpl_patch_index.db"
INDEX_SCHEMA_VERSION = "2"
EQUIPMENT_CATEGORY_HEADINGS = frozenset(
    {
        "装备调整", "装备平衡调整", "装备改动", "三级装备", "二级装备",
        "一级装备", "攻击", "防御", "法术", "游走", "移动", "打野",
    }
)


@dataclass(frozen=True)
class PatchDocumentRecord:
    """One provenance record from metadata/tencent-patch-index.json."""

    announcement_id: str
    title: str
    published_at: str
    source_url: str
    source_hash: str
    document_path: Path


@dataclass(frozen=True)
class PatchChunk:
    """One source-attributed section ready for future FTS retrieval."""

    chunk_id: str
    announcement_id: str
    title: str
    published_at: str
    source_url: str
    source_hash: str
    heading_path: tuple[str, ...]
    hero_names: tuple[str, ...]
    equipment_names: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class PatchIndexBuildResult:
    """Summary returned after a successful, atomic index rebuild."""

    index_version: str
    document_count: int
    chunk_count: int
    index_path: Path


class PatchIndexBuilder:
    """Turn the local, versioned patch corpus into a SQLite FTS5 read model."""

    def __init__(
        self,
        *,
        corpus_root: Path = DEFAULT_CORPUS_ROOT,
        index_path: Path = DEFAULT_INDEX_PATH,
    ) -> None:
        self.corpus_root = corpus_root
        self.index_path = index_path
        self._hero_names: tuple[str, ...] | None = None

    def build(self) -> PatchIndexBuildResult:
        """Rebuild the entire search index without touching source files."""
        documents = self._load_documents()
        chunks = tuple(
            chunk
            for document in documents
            for chunk in self._chunk_document(document)
        )
        return self._write_index(documents, chunks)

    def _load_documents(self) -> tuple[PatchDocumentRecord, ...]:
        """Load corpus metadata and reject paths outside the source corpus."""
        corpus_root = self.corpus_root.resolve()
        metadata_path = corpus_root / "metadata" / "tencent-patch-index.json"
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Patch corpus index not found: {metadata_path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid patch corpus metadata: {metadata_path}") from exc

        entries = payload.get("documents")
        if not isinstance(entries, list):
            raise ValueError("Patch corpus metadata requires a documents list")

        documents: list[PatchDocumentRecord] = []
        seen_ids: set[str] = set()
        for position, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                raise ValueError(f"Patch corpus document {position} must be an object")
            announcement_id = self._required_text(entry, "announcement_id", position)
            if announcement_id in seen_ids:
                raise ValueError(f"Duplicate patch announcement ID: {announcement_id}")
            seen_ids.add(announcement_id)
            relative_path = self._required_text(
                entry,
                "normalized_document",
                position,
            )
            document_path = self._corpus_path(corpus_root, relative_path)
            if not document_path.is_file():
                raise FileNotFoundError(
                    f"Patch document does not exist: {relative_path}"
                )
            published_at = self._required_text(entry, "published_at", position)
            self._validate_published_at(published_at, announcement_id)
            documents.append(
                PatchDocumentRecord(
                    announcement_id=announcement_id,
                    title=self._required_text(entry, "title", position),
                    published_at=published_at,
                    source_url=self._required_text(entry, "source_url", position),
                    source_hash=self._required_text(
                        entry,
                        "raw_payload_sha256",
                        position,
                    ),
                    document_path=document_path,
                )
            )
        return tuple(documents)

    def _chunk_document(self, document: PatchDocumentRecord) -> tuple[PatchChunk, ...]:
        """Split source Markdown at headings while retaining parent provenance."""
        source = document.document_path.read_text(encoding="utf-8")
        body = self._without_front_matter(source)
        heading_path: list[str] = []
        text_lines: list[str] = []
        chunks: list[PatchChunk] = []

        def flush() -> None:
            text = "\n".join(text_lines).strip()
            text_lines.clear()
            if not text or not heading_path:
                return
            ordinal = len(chunks)
            stable_material = "\n".join(
                [document.announcement_id, *heading_path, text]
            )
            suffix = hashlib.sha256(
                stable_material.encode("utf-8")
            ).hexdigest()[:12]
            chunks.append(
                PatchChunk(
                    chunk_id=f"{document.announcement_id}:{ordinal:04d}:{suffix}",
                    announcement_id=document.announcement_id,
                    title=document.title,
                    published_at=document.published_at,
                    source_url=document.source_url,
                    source_hash=document.source_hash,
                    heading_path=tuple(heading_path),
                    hero_names=self._detect_hero_names(heading_path),
                    equipment_names=self._detect_equipment_names(heading_path),
                    text=text,
                )
            )

        for line in body.splitlines():
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match is None:
                text_lines.append(line)
                continue
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_path[:] = heading_path[: level - 1]
            heading_path.append(title)
        flush()
        return tuple(chunks)

    def _write_index(
        self,
        documents: tuple[PatchDocumentRecord, ...],
        chunks: tuple[PatchChunk, ...],
    ) -> PatchIndexBuildResult:
        """Write ordinary tables and FTS5 data, then atomically replace the index."""
        index_version = self._index_version(documents, chunks)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.index_path.with_name(
            f".{self.index_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with sqlite3.connect(temporary_path) as db:
                db.executescript(
                    """
                    PRAGMA journal_mode=DELETE;
                    PRAGMA foreign_keys=ON;

                    CREATE TABLE patch_documents (
                        announcement_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        published_at TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        source_hash TEXT NOT NULL,
                        document_path TEXT NOT NULL
                    );

                    CREATE TABLE patch_chunks (
                        chunk_id TEXT PRIMARY KEY,
                        announcement_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        published_at TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        source_hash TEXT NOT NULL,
                        heading_path TEXT NOT NULL,
                        hero_names TEXT NOT NULL,
                        equipment_names TEXT NOT NULL,
                        text TEXT NOT NULL,
                        FOREIGN KEY (announcement_id)
                            REFERENCES patch_documents (announcement_id)
                    );

                    CREATE VIRTUAL TABLE patch_chunks_fts USING fts5(
                        chunk_id UNINDEXED,
                        text,
                        title,
                        hero_names,
                        equipment_names,
                        heading_path,
                        tokenize='unicode61'
                    );

                    CREATE TABLE index_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
                db.executemany(
                    """
                    INSERT INTO patch_documents (
                        announcement_id, title, published_at, source_url,
                        source_hash, document_path
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            document.announcement_id,
                            document.title,
                            document.published_at,
                            document.source_url,
                            document.source_hash,
                            str(document.document_path),
                        )
                        for document in documents
                    ],
                )
                chunk_rows = [
                    (
                        chunk.chunk_id,
                        chunk.announcement_id,
                        chunk.title,
                        chunk.published_at,
                        chunk.source_url,
                        chunk.source_hash,
                        json.dumps(chunk.heading_path, ensure_ascii=False),
                        json.dumps(chunk.hero_names, ensure_ascii=False),
                        json.dumps(chunk.equipment_names, ensure_ascii=False),
                        chunk.text,
                    )
                    for chunk in chunks
                ]
                db.executemany(
                    """
                    INSERT INTO patch_chunks (
                        chunk_id, announcement_id, title, published_at, source_url,
                        source_hash, heading_path, hero_names, equipment_names, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chunk_rows,
                )
                db.executemany(
                    """
                    INSERT INTO patch_chunks_fts (
                        chunk_id, text, title, hero_names, equipment_names, heading_path
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk.chunk_id,
                            chunk.text,
                            chunk.title,
                            " ".join(chunk.hero_names),
                            " ".join(chunk.equipment_names),
                            " > ".join(chunk.heading_path),
                        )
                        for chunk in chunks
                    ],
                )
                db.executemany(
                    "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
                    [
                        ("schema_version", INDEX_SCHEMA_VERSION),
                        ("index_version", index_version),
                        ("document_count", str(len(documents))),
                        ("chunk_count", str(len(chunks))),
                    ],
                )
            os.replace(temporary_path, self.index_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return PatchIndexBuildResult(
            index_version=index_version,
            document_count=len(documents),
            chunk_count=len(chunks),
            index_path=self.index_path,
        )

    @staticmethod
    def _required_text(entry: dict[str, object], key: str, position: int) -> str:
        value = entry.get(key)
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError(f"Patch corpus document {position} requires {key}")
        return text

    @staticmethod
    def _validate_published_at(value: str, announcement_id: str) -> None:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Invalid published_at for announcement {announcement_id}: {value}"
            ) from exc

    @staticmethod
    def _corpus_path(corpus_root: Path, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ValueError("Patch document path is outside the corpus")
        resolved = (corpus_root / candidate).resolve()
        try:
            resolved.relative_to(corpus_root)
        except ValueError as exc:
            raise ValueError("Patch document path is outside the corpus") from exc
        return resolved

    @staticmethod
    def _without_front_matter(source: str) -> str:
        lines = source.splitlines()
        if not lines or lines[0].strip() != "---":
            return source
        for position, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                return "\n".join(lines[position + 1 :])
        raise ValueError("Patch document has unterminated front matter")

    def _detect_hero_names(self, heading_path: list[str]) -> tuple[str, ...]:
        if self._hero_names is None:
            self._hero_names = self._load_hero_names()
        heading_text = " ".join(heading_path)
        return tuple(
            hero_name
            for hero_name in self._hero_names
            if (
                any(part.strip() == hero_name for part in heading_path)
                if len(hero_name) == 1
                else hero_name in heading_text
            )
        )

    @staticmethod
    def _detect_equipment_names(heading_path: list[str]) -> tuple[str, ...]:
        """Derive a named item from an official equipment-heading hierarchy."""
        marker_positions = [
            position
            for position, heading in enumerate(heading_path)
            if "装备" in heading
        ]
        if not marker_positions:
            return ()
        candidates = [
            heading.strip()
            for heading in heading_path[marker_positions[-1] + 1 :]
            if heading.strip() not in EQUIPMENT_CATEGORY_HEADINGS
        ]
        return tuple(dict.fromkeys(candidates[-1:]))

    def _load_hero_names(self) -> tuple[str, ...]:
        catalogue = self.corpus_root / "sources" / "official" / "herolist.json"
        if not catalogue.is_file():
            return ()
        try:
            payload = json.loads(catalogue.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid hero catalogue: {catalogue}") from exc
        if not isinstance(payload, list):
            raise ValueError(f"Hero catalogue must be a list: {catalogue}")
        names = {
            str(row.get("cname", "")).strip()
            for row in payload
            if isinstance(row, dict) and str(row.get("cname", "")).strip()
        }
        return tuple(sorted(names, key=lambda name: (-len(name), name)))

    @staticmethod
    def _index_version(
        documents: tuple[PatchDocumentRecord, ...],
        chunks: tuple[PatchChunk, ...],
    ) -> str:
        material = [f"schema:{INDEX_SCHEMA_VERSION}"]
        material.extend(
            f"document:{document.announcement_id}:{document.source_hash}"
            for document in documents
        )
        material.extend(
            f"chunk:{chunk.chunk_id}:{chunk.source_hash}" for chunk in chunks
        )
        return hashlib.sha256("\n".join(material).encode("utf-8")).hexdigest()
