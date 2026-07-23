from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    league_name: Mapped[str] = mapped_column(String(128), default="")
    league_type: Mapped[str] = mapped_column(String(32), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=0)
    start_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    end_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    league_id: Mapped[str] = mapped_column(String(32), index=True)
    camp1_team_id: Mapped[str] = mapped_column(String(32), default="")
    camp1_team_name: Mapped[str] = mapped_column(String(64), default="")
    camp1_score: Mapped[int] = mapped_column(Integer, default=0)
    camp2_team_id: Mapped[str] = mapped_column(String(32), default="")
    camp2_team_name: Mapped[str] = mapped_column(String(64), default="")
    camp2_score: Mapped[int] = mapped_column(Integer, default=0)
    bo: Mapped[int] = mapped_column(Integer, default=0)
    win_camp: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=0)
    match_stage: Mapped[str] = mapped_column(String(64), default="")
    start_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Battle(Base):
    __tablename__ = "battles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    match_id: Mapped[str] = mapped_column(String(32), index=True)
    league_id: Mapped[str] = mapped_column(String(32), index=True)
    battle_seq: Mapped[int] = mapped_column(Integer, default=0)
    win_camp: Mapped[int] = mapped_column(Integer, default=0)
    game_duration: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class BattleBp(Base):
    """One ban or pick action in a battle. action_type: 0=ban, 1=pick."""

    __tablename__ = "battle_bps"
    __table_args__ = (
        UniqueConstraint(
            "battle_id", "bp_order", "action_type", "hero_id", "camp",
            name="uk_battle_bp_action",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[str] = mapped_column(String(64), index=True)
    league_id: Mapped[str] = mapped_column(String(32), index=True)
    camp: Mapped[int] = mapped_column(Integer, default=0)
    action_type: Mapped[int] = mapped_column(Integer)  # 0 ban, 1 pick
    hero_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    hero_name: Mapped[str] = mapped_column(String(100), default="")
    hero_icon: Mapped[str] = mapped_column(String(500), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    bp_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Team(Base):
    __tablename__ = "teams"

    team_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    team_name: Mapped[str] = mapped_column(String(64), default="")
    team_icon: Mapped[str] = mapped_column(String(500), default="")


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("player_name", "team_id", name="uk_player_team"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_name: Mapped[str] = mapped_column(String(64), default="")
    team_id: Mapped[str] = mapped_column(String(32), default="")
    team_name: Mapped[str] = mapped_column(String(64), default="")
    player_icon: Mapped[str] = mapped_column(String(500), default="")


class BattlePlayer(Base):
    __tablename__ = "battle_players"
    __table_args__ = (
        UniqueConstraint(
            "battle_id",
            "player_name",
            "hero_id",
            "camp",
            name="uk_battle_player_hero_camp",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[str] = mapped_column(String(64), index=True)
    match_id: Mapped[str] = mapped_column(String(32), index=True)
    league_id: Mapped[str] = mapped_column(String(32), index=True)
    team_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    team_name: Mapped[str] = mapped_column(String(64), default="")
    player_name: Mapped[str] = mapped_column(String(64), default="")
    player_icon: Mapped[str] = mapped_column(String(500), default="")
    hero_id: Mapped[int] = mapped_column(Integer, default=0)
    hero_name: Mapped[str] = mapped_column(String(100), default="")
    camp: Mapped[int] = mapped_column(Integer, default=0)
    match_camp: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)
    position_desc: Mapped[str] = mapped_column(String(32), default="")


class Hero(Base):
    __tablename__ = "heroes"

    hero_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hero_name: Mapped[str] = mapped_column(String(100), default="")
    hero_icon: Mapped[str] = mapped_column(String(500), default="")


class HeroPosition(Base):
    """An observed lane/role a hero can occupy in professional match data."""

    __tablename__ = "hero_positions"
    __table_args__ = (
        UniqueConstraint("hero_id", "position", name="uk_hero_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hero_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)
    position_desc: Mapped[str] = mapped_column(String(32), default="")
    observed_pick_count: Mapped[int] = mapped_column(Integer, default=0)


class HeroBpStats(Base):
    """Precomputed per-league hero BP aggregates for the frontend."""

    __tablename__ = "hero_bp_stats"
    __table_args__ = (
        UniqueConstraint("league_id", "hero_id", name="uk_league_hero_bp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(String(32), index=True)
    hero_id: Mapped[int] = mapped_column(Integer, index=True)
    hero_name: Mapped[str] = mapped_column(String(100), default="")
    hero_icon: Mapped[str] = mapped_column(String(500), default="")
    battle_count: Mapped[int] = mapped_column(Integer, default=0)
    ban_count: Mapped[int] = mapped_column(Integer, default=0)
    pick_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    ban_rate: Mapped[float] = mapped_column(Float, default=0.0)
    pick_rate: Mapped[float] = mapped_column(Float, default=0.0)
    presence_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
