"""Publish browser-ready analysis files and hero art for Nginx to serve."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Hero, League
from app.services.analysis_pipeline import ANALYSIS_DIR, OUTPUT_ROOT
from app.services.draft_simulator import metadata

PUBLISHED_ROOT = ANALYSIS_DIR / "published"
DATA_ROOT = PUBLISHED_ROOT / "data"
HERO_ROOT = PUBLISHED_ROOT / "heroes"
ALLOWED_ICON_HOSTS = {"res.edata.qq.com"}
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def _write_json(path: Path, value: object) -> None:
    """Atomically replace a published file so visitors never read a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"))
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _hero_url(hero_id: int) -> str:
    return f"/assets/heroes/{hero_id}.jpg"


def _download_hero_assets(db: Session) -> dict[str, int]:
    """Fetch missing official hero art once into persistent published storage."""
    HERO_ROOT.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    failed = 0
    heroes = db.scalars(select(Hero)).all()
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        for hero in heroes:
            path = HERO_ROOT / f"{hero.hero_id}.jpg"
            if path.is_file():
                skipped += 1
                continue
            source_url = hero.hero_icon or ""
            parsed = urlparse(source_url)
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED_ICON_HOSTS:
                failed += 1
                continue
            try:
                response = client.get(source_url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if (
                    not content_type.startswith("image/")
                    or not response.content
                    or len(response.content) > MAX_IMAGE_BYTES
                ):
                    raise ValueError("invalid image response")
                with NamedTemporaryFile(dir=HERO_ROOT, delete=False) as temporary:
                    temporary.write(response.content)
                    temporary_path = Path(temporary.name)
                temporary_path.replace(path)
                downloaded += 1
            except (httpx.HTTPError, ValueError):
                failed += 1
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def _replace_pattern_icons(payload: dict) -> None:
    for row in payload.get("rows", []):
        for prefix in ("source", "target"):
            hero_id = int(row.get(f"{prefix}_hero_id") or 0)
            if hero_id and (HERO_ROOT / f"{hero_id}.jpg").is_file():
                row[f"{prefix}_hero_icon"] = _hero_url(hero_id)
    for hero in payload.get("meta_heroes", []):
        hero_id = int(hero.get("hero_id") or 0)
        if hero_id and (HERO_ROOT / f"{hero_id}.jpg").is_file():
            hero["hero_icon"] = _hero_url(hero_id)


def _replace_team_icons(payload: dict) -> None:
    for row in payload.get("rows", []):
        for suffix in ("a", "b"):
            hero_id = int(row.get(f"hero_{suffix}_id") or 0)
            if hero_id and (HERO_ROOT / f"{hero_id}.jpg").is_file():
                row[f"hero_{suffix}_icon"] = _hero_url(hero_id)


def _replace_model_icons(payload: dict) -> None:
    for hero in payload.get("heroes", []):
        hero_id = int(hero.get("hero_id") or 0)
        if hero_id and (HERO_ROOT / f"{hero_id}.jpg").is_file():
            hero["hero_icon"] = _hero_url(hero_id)


def _publish_seasons(db: Session) -> None:
    leagues = db.scalars(
        select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
    ).all()
    rows = []
    for league in leagues:
        directory = DATA_ROOT / league.league_id
        if not (directory / "patterns.json").is_file():
            continue
        rows.append(
            {
                "league_id": league.league_id,
                "league_name": league.league_name,
                "year": league.year,
                "season": league.season,
                "status": league.status,
                "team_synergy_ready": (directory / "team-synergies.json").is_file(),
            }
        )
    _write_json(DATA_ROOT / "seasons.json", rows)


def publish_league(db: Session, league_id: str) -> dict[str, object]:
    """Create all currently available static public files for one season."""
    # Import here to keep API modules independent during application startup.
    from app.api.visualization import statistics_ready, team_synergies, visualization_patterns

    league = db.scalar(select(League).where(League.league_id == league_id))
    if league is None:
        raise ValueError("League not found")

    icons = _download_hero_assets(db)
    published: list[str] = []
    directory = DATA_ROOT / league_id

    if statistics_ready(league_id):
        patterns = visualization_patterns(league_id=league_id, min_selections=2, db=db).data
        _replace_pattern_icons(patterns)
        _write_json(directory / "patterns.json", patterns)
        published.append("patterns.json")

    if (OUTPUT_ROOT / league_id / "team_synergy_stats.jsonl").is_file():
        teams = team_synergies(league_id=league_id, min_selections=2, db=db).data
        _replace_team_icons(teams)
        _write_json(directory / "team-synergies.json", teams)
        published.append("team-synergies.json")

    if (OUTPUT_ROOT / league_id / "draft_model.json").is_file():
        model = metadata(league_id)
        _replace_model_icons(model)
        _write_json(directory / "draft-model.json", model)
        published.append("draft-model.json")

    _publish_seasons(db)
    return {"files": published, "icons": icons, "directory": str(directory)}
