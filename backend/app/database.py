from collections.abc import Generator

from sqlalchemy import Connection, Engine, MetaData, create_engine, inspect, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# ``create_all`` creates missing tables but deliberately does not add columns to
# an existing table. Compatibility migrations preserve existing local data.
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


def _allow_multiple_board_codes(connection: Connection) -> None:
    """Remove the legacy per-browser unique constraint without losing posts/IDs."""
    if not inspect(connection).has_table("event_board_codes"):
        return
    # PRAGMA sees implicit UNIQUE indexes reliably, including older inline DDL.
    unique_columns = []
    for index in connection.exec_driver_sql("PRAGMA index_list(event_board_codes)").all():
        if index[2]:
            quoted = connection.dialect.identifier_preparer.quote_identifier(index[1])
            columns = connection.exec_driver_sql(f"PRAGMA index_info({quoted})").all()
            unique_columns.append([column[2] for column in columns])
    if ["visitor_hash"] not in unique_columns:
        return

    from app.models import EventBoardCode

    # SQLite's implicit UNIQUE index cannot be dropped. Rebuild just this table
    # inside the caller's write transaction, retaining its complete records.
    original = EventBoardCode.__table__
    replacement = original.to_metadata(MetaData(), name="event_board_codes_multiple")
    previous_sequence = connection.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name = 'event_board_codes'"
    ).scalar() if inspect(connection).has_table("sqlite_sequence") else None
    replacement.create(connection)
    columns = [column.name for column in original.columns]
    connection.execute(replacement.insert().from_select(columns, select(*original.columns)))
    connection.exec_driver_sql("DROP TABLE event_board_codes")
    connection.exec_driver_sql("ALTER TABLE event_board_codes_multiple RENAME TO event_board_codes")
    if previous_sequence is not None:
        result = connection.exec_driver_sql(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'event_board_codes'",
            (previous_sequence,),
        )
        if not result.rowcount:
            connection.exec_driver_sql(
                "INSERT INTO sqlite_sequence(name, seq) VALUES ('event_board_codes', ?)",
                (previous_sequence,),
            )


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
        "pipeline_jobs": {"execution_token": "VARCHAR(36)"},
    }
    added: list[str] = []
    with target_engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        for table, columns in tables.items():
            if not inspect(connection).has_table(table):
                continue
            existing = {
                column["name"]
                for column in inspect(connection).get_columns(table)
            }
            for name, declaration in columns.items():
                if name in existing:
                    continue
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}'
                )
                added.append(name if table == "battle_players" else f"{table}.{name}")
        _allow_multiple_board_codes(connection)
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
