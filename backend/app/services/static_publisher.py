"""Publish browser-ready aggregate analysis files for Nginx to serve."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import League
from app.services.analysis_pipeline import ANALYSIS_DIR, OUTPUT_ROOT
from app.services.draft_simulator import metadata

PUBLISHED_ROOT = ANALYSIS_DIR / "published"
DATA_ROOT = PUBLISHED_ROOT / "data"


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
    # Nginx runs as an unprivileged user and serves this bind-mounted file.
    path.chmod(0o644)


def _remove_icon_urls(value: object) -> None:
    """Keep public analysis payloads free of third-party image URLs."""
    if isinstance(value, dict):
        for key in list(value):
            if key.endswith("_icon") or key.endswith("_icons"):
                value.pop(key)
            else:
                _remove_icon_urls(value[key])
    elif isinstance(value, list):
        for item in value:
            _remove_icon_urls(item)


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

    published: list[str] = []
    directory = DATA_ROOT / league_id

    if statistics_ready(league_id):
        patterns = visualization_patterns(league_id=league_id, min_selections=2, db=db).data
        _remove_icon_urls(patterns)
        _write_json(directory / "patterns.json", patterns)
        published.append("patterns.json")

    if (OUTPUT_ROOT / league_id / "team_synergy_stats.jsonl").is_file():
        teams = team_synergies(league_id=league_id, min_selections=2, db=db).data
        _remove_icon_urls(teams)
        _write_json(directory / "team-synergies.json", teams)
        published.append("team-synergies.json")

    if (OUTPUT_ROOT / league_id / "draft_model.json").is_file():
        model = metadata(league_id)
        _remove_icon_urls(model)
        _write_json(directory / "draft-model.json", model)
        published.append("draft-model.json")

    _publish_seasons(db)
    return {"files": published, "directory": str(directory)}
