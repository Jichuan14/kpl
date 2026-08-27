from collections.abc import Generator

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# ``create_all`` creates missing tables but deliberately does not add columns to
# an existing table. These additive SQLite migrations keep local databases made
# by older versions usable without deleting or rewriting any rows.
BATTLE_PLAYER_PERFORMANCE_COLUMNS = {
    "performance_data_available": "INTEGER NOT NULL DEFAULT 0",
    "kill_num": "INTEGER NOT NULL DEFAULT 0",
    "death_num": "INTEGER NOT NULL DEFAULT 0",
    "assist_num": "INTEGER NOT NULL DEFAULT 0",
    "gold": "INTEGER NOT NULL DEFAULT 0",
    "hurt_total": "BIGINT NOT NULL DEFAULT 0",
    "hurt_to_hero_total": "BIGINT NOT NULL DEFAULT 0",
    "be_hurt_total": "BIGINT NOT NULL DEFAULT 0",
    "be_hurt_by_hero_total": "BIGINT NOT NULL DEFAULT 0",
    "kda": "FLOAT NOT NULL DEFAULT 0",
    "mvp_score": "FLOAT NOT NULL DEFAULT 0",
    "is_mvp": "INTEGER NOT NULL DEFAULT 0",
    "is_lose_mvp": "INTEGER NOT NULL DEFAULT 0",
    "participation_rate": "FLOAT NOT NULL DEFAULT 0",
    "hurt_total_rate": "FLOAT NOT NULL DEFAULT 0",
    "be_hurt_total_rate": "FLOAT NOT NULL DEFAULT 0",
    "hurt_to_hero_total_rate": "FLOAT NOT NULL DEFAULT 0",
    "be_hurt_by_hero_total_rate": "FLOAT NOT NULL DEFAULT 0",
}


def ensure_schema_compatibility(target_engine: Engine = engine) -> list[str]:
    """Apply safe, additive compatibility migrations and return added columns."""
    if target_engine.dialect.name != "sqlite":
        return []
    tables = {
        "battle_players": BATTLE_PLAYER_PERFORMANCE_COLUMNS,
        "live_match_winner_predictions": {
            "best_of": "INTEGER",
            "team_a_score": "INTEGER",
            "team_b_score": "INTEGER",
        },
    }
    added: list[str] = []
    with target_engine.begin() as connection:
        for table, columns in tables.items():
            if not inspect(target_engine).has_table(table):
                continue
            existing = {
                column["name"]
                for column in inspect(target_engine).get_columns(table)
            }
            for name, declaration in columns.items():
                if name in existing:
                    continue
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}'
                )
                added.append(name if table == "battle_players" else f"{table}.{name}")
    return added


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility(engine)
