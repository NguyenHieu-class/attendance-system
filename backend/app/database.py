from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from app.models import access_log, admin, attendance_log, device, door, face_profile, nfc_card, nfc_enrollment, user  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_schema()


def migrate_schema() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        _add_column_if_missing(inspector, conn, "door_settings", "liveness_enabled", "BOOLEAN DEFAULT 1")
        _add_column_if_missing(inspector, conn, "door_settings", "liveness_threshold", "FLOAT DEFAULT 0.80")
        _add_column_if_missing(inspector, conn, "door_settings", "liveness_fail_closed", "BOOLEAN DEFAULT 1")
        _add_column_if_missing(inspector, conn, "access_logs", "liveness_score", "FLOAT")
        _add_column_if_missing(inspector, conn, "access_logs", "spoof_result", "VARCHAR(32)")


def _add_column_if_missing(inspector, conn, table_name: str, column_name: str, column_type: str) -> None:
    if table_name not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in existing:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
