"""Privacy-preserving aggregate visitor analytics for the management screen."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import hmac

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import VisitorDailyPage, VisitorDailyVisitor
from app.schemas import ApiResponse, VisitorTrackRequest

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
widget_bearer = HTTPBearer(auto_error=False)


def _visitor_metrics(db: Session, since: date | None = None) -> dict[str, int]:
    visitor_query = select(func.count(func.distinct(VisitorDailyVisitor.visitor_hash)))
    page_query = select(func.coalesce(func.sum(VisitorDailyPage.page_views), 0))
    if since:
        visitor_query = visitor_query.where(VisitorDailyVisitor.day >= since)
        page_query = page_query.where(VisitorDailyPage.day >= since)
    return {
        "unique_visitors": int(db.scalar(visitor_query) or 0),
        "page_views": int(db.scalar(page_query) or 0),
    }


def require_widget_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(widget_bearer),
) -> None:
    """Authorize only the locally configured, read-only SwiftBar widget."""
    configured_token = get_settings().analytics_widget_token
    if configured_token is None:
        # Do not expose configuration details to callers.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Visitor widget is unavailable",
        )

    supplied_token = credentials.credentials if credentials else ""
    expected_token = configured_token.get_secret_value()
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied_token, expected_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Widget authorization required",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/visits")
def track_visit(body: VisitorTrackRequest, db: Session = Depends(get_db)) -> ApiResponse:
    """Record one public page view without saving IP addresses or raw IDs."""
    if body.page_path.startswith("/management"):
        raise HTTPException(status_code=400, detail="Management routes are not tracked")

    today = datetime.now(timezone.utc).date()
    visitor_hash = sha256(str(body.visitor_id).encode()).hexdigest()
    db.execute(
        insert(VisitorDailyVisitor)
        .values(day=today, visitor_hash=visitor_hash)
        .on_conflict_do_nothing(index_elements=["day", "visitor_hash"])
    )
    db.execute(
        insert(VisitorDailyPage)
        .values(day=today, page_path=body.page_path, page_views=1)
        .on_conflict_do_update(
            index_elements=["day", "page_path"],
            set_={"page_views": VisitorDailyPage.page_views + 1},
        )
    )
    db.commit()
    return ApiResponse(message="visit recorded", data={"recorded": True})


@router.get("/summary")
def visitor_summary(db: Session = Depends(get_db)) -> ApiResponse:
    """Return the aggregate visitor counts used by the private dashboard."""
    today = datetime.now(timezone.utc).date()
    return ApiResponse(
        message="visitor analytics retrieved",
        data={
            "today": _visitor_metrics(db, today),
            "last_7_days": _visitor_metrics(db, today - timedelta(days=6)),
            "last_30_days": _visitor_metrics(db, today - timedelta(days=29)),
            "all_time": _visitor_metrics(db),
        },
    )


@router.get("/widget")
def widget_visitor_summary(
    response: Response,
    _: None = Depends(require_widget_token),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Return the minimum aggregate needed by the authenticated Mac widget."""
    response.headers["Cache-Control"] = "no-store"
    today = datetime.now(timezone.utc).date()
    return ApiResponse(
        message="visitor widget metrics retrieved",
        data={"today": _visitor_metrics(db, today)},
    )
