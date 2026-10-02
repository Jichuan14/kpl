"""Database-backed site preferences, independent of published analysis."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import League, SiteSettings


def resolve_default_league(db: Session) -> dict:
    settings = db.get(SiteSettings, 1)
    saved = settings.default_league_id if settings else None
    league = db.scalar(select(League).where(League.league_id == saved)) if saved else None
    if league is None:
        league = db.scalar(select(League).order_by(League.year.desc(), League.season.desc(), League.start_time.desc(), League.league_id.desc()))
    return {'default_league_id': league.league_id if league else None, 'saved': bool(saved and league and league.league_id == saved)}


def save_default_league(db: Session, league_id: str) -> dict:
    if not db.scalar(select(League.id).where(League.league_id == league_id)):
        raise ValueError('League not found')
    # SQLite upsert avoids a race between the first two administrators saving.
    from sqlalchemy.dialects.sqlite import insert
    statement = insert(SiteSettings).values(id=1, default_league_id=league_id)
    db.execute(statement.on_conflict_do_update(index_elements=['id'], set_={'default_league_id': league_id}))
    db.commit()
    return {'default_league_id': league_id, 'saved': True}
