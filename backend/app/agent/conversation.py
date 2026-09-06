"""Session-bound conversation memory for the LangGraph coach.

Checkpoint/thread identifiers are server-issued and never accepted as raw
client authority. A new user turn always starts with reset transient state.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any
from uuid import uuid4

from app.agent.errors import CoachConversationError
from app.agent.scope import normalize_gate_message

SESSION_COOKIE = "kpl_coach_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
CONVERSATION_TTL_SECONDS = 12 * 60 * 60
MAX_TURNS = 8
WHY_THAT_ONE = re.compile(
    r"why (?:that|this) one|explain (?:the )?(?:difference|result)|"
    r"compare (?:the )?alternatives|为什么(?:是)?(?:这|那)|为啥(?:选|禁)?|"
    r"解释(?:差异|结果|原因)?|比较(?:备选|差异|候选)",
    re.IGNORECASE,
)
ON_RED = re.compile(r"\bon red\b|红方|and on red", re.IGNORECASE)
ON_BLUE = re.compile(r"\bon blue\b|蓝方|and on blue", re.IGNORECASE)
OTHER_TEAM = re.compile(r"the other team|另一[支个边]?队|对面|对方", re.IGNORECASE)


def new_session_id() -> str:
    return uuid4().hex


def new_conversation_id() -> str:
    return uuid4().hex


def looks_like_id(value: str | None) -> bool:
    return bool(value) and bool(re.fullmatch(r"[A-Fa-f0-9-]{16,64}", value or ""))


def board_fingerprint(league_id: str, draft_state: dict[str, Any] | None) -> str:
    payload = {"league_id": league_id, "draft_state": draft_state or None}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _now() -> float:
    return time()


def extract_entities(tool_calls: list[dict[str, Any]], draft_state: dict[str, Any] | None) -> dict[str, Any]:
    entities: dict[str, Any] = {}
    if draft_state:
        for key in ("blue_team_id", "blue_team_name", "red_team_id", "red_team_name"):
            if draft_state.get(key):
                entities[key] = draft_state[key]
    for call in tool_calls:
        if not call.get("success"):
            continue
        payload = call.get("result") if isinstance(call.get("result"), dict) else {}
        for key in ("team_id", "team_name", "player_name", "hero_name", "side"):
            if payload.get(key) and key not in entities:
                entities[key] = payload[key]
        for row in payload.get("recommendations") or payload.get("next_action_probabilities") or []:
            if isinstance(row, dict) and row.get("hero_name") and "hero_name" not in entities:
                entities["hero_name"] = row.get("hero_name")
                entities["hero_id"] = row.get("hero_id")
                break
    return entities


def resolve_follow_up(message: str, reference: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve a short follow-up against stored entities without granting tools."""
    resolved = dict((reference or {}).get("entities") or {})
    pending = None
    if ON_RED.search(message):
        resolved["side"] = "red"
    elif ON_BLUE.search(message):
        resolved["side"] = "blue"
    if OTHER_TEAM.search(message) and not (ON_RED.search(message) or ON_BLUE.search(message)):
        blue = resolved.get("blue_team_name")
        red = resolved.get("red_team_name")
        current = resolved.get("team_name")
        if blue and red and current:
            resolved["team_name"] = red if current == blue else blue
        elif blue and red:
            pending = "other_team"
        else:
            pending = "other_team"
    return {
        "entities": resolved,
        "needs_clarification": pending,
        "refers_to_prior": bool(WHY_THAT_ONE.search(message) or ON_RED.search(message) or ON_BLUE.search(message) or OTHER_TEAM.search(message)),
    }


def scoped_gate_reference(
    record: dict[str, Any] | None,
    *,
    current_league_id: str,
    current_board: str,
) -> dict[str, Any]:
    """Small untrusted-but-scoped record for the current-turn gate."""
    if not record:
        return {}
    previous_league = record.get("league_id")
    previous_board = record.get("board_fingerprint")
    stale_season = bool(previous_league and previous_league != current_league_id)
    stale_board = bool(previous_board and previous_board != current_board)
    return {
        "previous_intent": record.get("previous_intent"),
        "intents": list(record.get("intents") or []),
        "entities": dict(record.get("entities") or {}),
        "league_id": previous_league,
        "board_fingerprint": previous_board,
        "pending_clarification": record.get("pending_clarification"),
        "stale_season": stale_season,
        "stale_board": stale_board,
        "research_fresh": not stale_season,
    }


@dataclass
class ConversationRecord:
    conversation_id: str
    session_id: str
    created_at: float
    updated_at: float
    turns: list[dict[str, Any]] = field(default_factory=list)
    pending_clarification: str | None = None
    in_flight_request_id: str | None = None
    last_client_request_id: str | None = None
    last_result: dict[str, Any] | None = None

    def to_public_ref(self) -> dict[str, Any]:
        latest = self.turns[-1] if self.turns else {}
        return {
            "conversation_id": self.conversation_id,
            "previous_intent": latest.get("intent"),
            "intents": list(latest.get("intents") or []),
            "entities": dict(latest.get("entities") or {}),
            "league_id": latest.get("league_id"),
            "board_fingerprint": latest.get("board_fingerprint"),
            "pending_clarification": self.pending_clarification,
        }


class ConversationStore:
    """In-memory conversation registry used by tests and as the process cache."""

    def __init__(self) -> None:
        self._sessions: dict[str, float] = {}
        self._conversations: dict[str, ConversationRecord] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def ensure_session(self, session_id: str | None) -> str:
        token = session_id if looks_like_id(session_id) else new_session_id()
        self._sessions[token] = _now()
        return token

    def _lock_for(self, conversation_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(conversation_id, threading.Lock())

    def create(self, session_id: str) -> ConversationRecord:
        record = ConversationRecord(
            conversation_id=new_conversation_id(),
            session_id=session_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self._conversations[record.conversation_id] = record
        return record

    def get(self, conversation_id: str | None, session_id: str) -> ConversationRecord | None:
        if not conversation_id:
            return None
        if not looks_like_id(conversation_id):
            raise CoachConversationError()
        record = self._conversations.get(conversation_id)
        if record is None or record.session_id != session_id:
            raise CoachConversationError()
        if _now() - record.updated_at > CONVERSATION_TTL_SECONDS:
            self.delete(conversation_id, session_id)
            raise CoachConversationError()
        return record

    def get_or_create(self, conversation_id: str | None, session_id: str) -> ConversationRecord:
        if conversation_id:
            return self.get(conversation_id, session_id)
        return self.create(session_id)

    def begin_turn(
        self,
        record: ConversationRecord,
        *,
        client_request_id: str | None,
        request_id: str,
    ) -> dict[str, Any] | None:
        lock = self._lock_for(record.conversation_id)
        if not lock.acquire(blocking=False):
            raise CoachConversationError(
                "conversation_busy",
                "A previous question is still running. Please wait and try again.",
            )
        try:
            if (
                client_request_id
                and client_request_id == record.last_client_request_id
                and record.last_result is not None
            ):
                return record.last_result
            if record.in_flight_request_id:
                raise CoachConversationError(
                    "conversation_busy",
                    "A previous question is still running. Please wait and try again.",
                )
            record.in_flight_request_id = request_id
            record.last_client_request_id = client_request_id
            return None
        finally:
            lock.release()

    def finish_turn(
        self,
        record: ConversationRecord,
        *,
        result: dict[str, Any],
        turn: dict[str, Any],
    ) -> None:
        record.turns.append(turn)
        record.turns = record.turns[-MAX_TURNS:]
        record.pending_clarification = turn.get("pending_clarification")
        record.updated_at = _now()
        record.in_flight_request_id = None
        record.last_result = result

    def abort_turn(self, record: ConversationRecord) -> None:
        record.in_flight_request_id = None

    def delete(self, conversation_id: str | None, session_id: str) -> None:
        if not conversation_id:
            return
        record = self._conversations.get(conversation_id)
        if record is None:
            return
        if record.session_id != session_id:
            raise CoachConversationError()
        self._conversations.pop(conversation_id, None)

    def clear_session(self, session_id: str) -> None:
        stale = [
            key
            for key, record in self._conversations.items()
            if record.session_id == session_id
        ]
        for key in stale:
            self._conversations.pop(key, None)

    def conversation_ids_for_session(self, session_id: str) -> list[str]:
        return [
            key
            for key, record in self._conversations.items()
            if record.session_id == session_id
        ]

    def close(self) -> None:
        return None


class SqliteConversationStore(ConversationStore):
    """Persist conversation metadata separately from match-data SQLite."""

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._db.commit()
        self._load()

    def _load(self) -> None:
        rows = self._db.execute(
            "SELECT conversation_id, session_id, payload, updated_at FROM conversations"
        ).fetchall()
        now = _now()
        expired: list[str] = []
        for conversation_id, session_id, payload, updated_at in rows:
            if now - float(updated_at) > CONVERSATION_TTL_SECONDS:
                expired.append(conversation_id)
                continue
            data = json.loads(payload)
            self._conversations[conversation_id] = ConversationRecord(
                conversation_id=conversation_id,
                session_id=session_id,
                created_at=float(data.get("created_at") or updated_at),
                updated_at=float(updated_at),
                turns=list(data.get("turns") or []),
                pending_clarification=data.get("pending_clarification"),
                last_client_request_id=data.get("last_client_request_id"),
                last_result=data.get("last_result"),
            )
        if expired:
            self._db.executemany(
                "DELETE FROM conversations WHERE conversation_id = ?",
                [(conversation_id,) for conversation_id in expired],
            )
            self._db.commit()

    def _persist(self, record: ConversationRecord) -> None:
        payload = {
            "created_at": record.created_at,
            "turns": record.turns,
            "pending_clarification": record.pending_clarification,
            "last_client_request_id": record.last_client_request_id,
            "last_result": record.last_result,
        }
        self._db.execute(
            """
            INSERT OR REPLACE INTO conversations
            (conversation_id, session_id, payload, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                record.conversation_id,
                record.session_id,
                json.dumps(payload, ensure_ascii=False),
                record.updated_at,
            ),
        )
        self._db.commit()

    def create(self, session_id: str) -> ConversationRecord:
        record = super().create(session_id)
        self._persist(record)
        return record

    def finish_turn(
        self,
        record: ConversationRecord,
        *,
        result: dict[str, Any],
        turn: dict[str, Any],
    ) -> None:
        super().finish_turn(record, result=result, turn=turn)
        self._persist(record)

    def delete(self, conversation_id: str | None, session_id: str) -> None:
        super().delete(conversation_id, session_id)
        if conversation_id:
            self._db.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
            self._db.commit()

    def clear_session(self, session_id: str) -> None:
        super().clear_session(session_id)
        self._db.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()


def filter_raw_history(turns: list[Any]) -> list[dict[str, str]]:
    """Compatibility path for old clients: local safety filter, no LLM reclassify."""
    from app.agent.scope import direct_deny_reason

    accepted: list[dict[str, str]] = []
    for turn in list(turns or [])[-4:]:
        question = normalize_gate_message(getattr(turn, "user", "") or "")
        answer = normalize_gate_message(getattr(turn, "assistant", "") or "")
        if not question or not answer:
            continue
        if len(question) > 1_000 or len(answer) > 1_000:
            continue
        if direct_deny_reason(question) or direct_deny_reason(answer):
            continue
        accepted.append({"question": question, "answer": answer})
    return accepted


def build_checkpointer(persistent: bool = False, path: str | Path | None = None):
    """Return a LangGraph checkpointer. Tests use the in-memory saver."""
    from langgraph.checkpoint.memory import InMemorySaver

    if not persistent:
        return InMemorySaver()
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        raise RuntimeError(
            "Persistent coach conversations require langgraph-checkpoint-sqlite."
        ) from None
    checkpoint_path = Path(path or "coach_checkpoints.sqlite")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    return SqliteSaver(connection)
