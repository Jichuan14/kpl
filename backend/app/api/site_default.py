"""Uncached public read of the management-owned default season."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import ApiResponse
from app.services.site_settings import resolve_default_league

router = APIRouter(prefix='/api/site-default', tags=['site settings'])


@router.get('')
def site_default(response: Response, db: Session = Depends(get_db)) -> ApiResponse:
    response.headers['Cache-Control'] = 'no-store'
    return ApiResponse(data=resolve_default_league(db))
