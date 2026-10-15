"""Anonymous code sharing for the single homepage board."""
from datetime import datetime, timezone
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EventBoardCode, EventBoardReport
from app.schemas import ApiResponse
from app.services.event_board import BoardConfig, InvalidBoardCode, load_board_config, sanitize_board_code
from app.services.public_requests import limit_public_writes, public_visitor

router = APIRouter(prefix="/api/event-board", tags=["event-board"])
INITIAL_CODE_LIMIT = 10
MORE_CODE_LIMIT = 15


class CodeInput(BaseModel):
    model_config = {"extra": "forbid"}
    code: str = Field(min_length=1, max_length=500)


def failure(status: int, code: str, message: str):
    raise HTTPException(status, detail={"code": code, "message": message})


def active_board() -> BoardConfig:
    config = load_board_config()
    if not config.is_active():
        failure(409, "board_closed", "This code board is closed.")
    return config


def serialize(row: EventBoardCode) -> dict:
    return {"id": row.id, "code": row.code,
            "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat()}


@router.get("")
def board(response: Response, before: int | None = Query(default=None, ge=1),
          db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    config = load_board_config()
    if not config.is_active():
        return ApiResponse(data={"active": False})
    query = select(EventBoardCode).where(EventBoardCode.active_digest.is_not(None), EventBoardCode.hidden.is_(False))
    if before is not None:
        query = query.where(EventBoardCode.id < before)
    limit = INITIAL_CODE_LIMIT if before is None else MORE_CODE_LIMIT
    rows = db.scalars(query.order_by(EventBoardCode.id.desc()).limit(limit + 1)).all()
    visible = rows[:limit]
    return ApiResponse(data={
        "active": True, "config": config.model_dump(mode="json", exclude={"enabled", "starts_at"}),
        "codes": [serialize(row) for row in visible],
        "next_cursor": visible[-1].id if len(rows) > limit else None,
    })


@router.post("/codes", dependencies=[Depends(limit_public_writes)])
def submit_code(body: CodeInput, config: BoardConfig = Depends(active_board),
              db: Session = Depends(get_db), visitor_hash: str = Depends(public_visitor)):
    try:
        code, identity = sanitize_board_code(body.code, config)
    except InvalidBoardCode as exc:
        failure(422, exc.code, str(exc))
    digest = sha256(identity.encode()).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    statement = insert(EventBoardCode).values(visitor_hash=visitor_hash, code=code, active_digest=digest,
                                              hidden=False, created_at=now, updated_at=now)
    # Token uniqueness handles concurrent duplicates without updating an existing post.
    db.execute(statement.on_conflict_do_nothing(index_elements=["active_digest"]))
    posted = db.scalar(select(EventBoardCode).where(EventBoardCode.active_digest == digest))
    if posted.hidden or posted.visitor_hash != visitor_hash:
        db.rollback()
        failure(409, "duplicate_code", "This code is already on the board.")
    # A retry of this browser's same code acknowledges its original unchanged post.
    data = serialize(posted)
    db.commit()
    return ApiResponse(data=data)


@router.post("/codes/{code_id}/report", dependencies=[Depends(limit_public_writes), Depends(active_board)])
def report_invalid_code(code_id: int = Path(ge=1), db: Session = Depends(get_db),
                        visitor_hash: str = Depends(public_visitor)):
    # Write first: SQLite serializes reporters before reading the count. INSERT
    # ... SELECT also prevents orphan reports when a stale row was already deleted.
    visible = (EventBoardCode.id == code_id, EventBoardCode.active_digest.is_not(None),
               EventBoardCode.hidden.is_(False))
    statement = insert(EventBoardReport).from_select(
        ["code_id", "visitor_hash", "created_at"],
        select(EventBoardCode.id, literal(visitor_hash), literal(datetime.now(timezone.utc).replace(tzinfo=None)))
        .where(*visible),
    ).on_conflict_do_nothing(index_elements=["code_id", "visitor_hash"])
    db.execute(statement)
    if db.scalar(select(EventBoardCode.id).where(*visible)) is None:
        db.rollback()
        return ApiResponse(data={"reported": False, "deleted": False})
    count = db.scalar(select(func.count()).select_from(EventBoardReport).where(EventBoardReport.code_id == code_id))
    deleted = count >= 3
    if deleted:
        db.execute(delete(EventBoardCode).where(EventBoardCode.id == code_id))
        db.execute(delete(EventBoardReport).where(EventBoardReport.code_id == code_id))
    db.commit()
    return ApiResponse(data={"reported": True, "deleted": deleted})
