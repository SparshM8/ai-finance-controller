"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import settings


class Base(DeclarativeBase):
    pass


def _connect_args() -> dict:
    if settings.database_url.startswith("sqlite"):
        # SQLite files live inside data/; create the directory if needed.
        if settings.database_url.startswith("sqlite:///"):
            raw = settings.database_url.replace("sqlite:///", "", 1)
            if raw != ":memory:":
                Path(raw).parent.mkdir(parents=True, exist_ok=True)
        return {"check_same_thread": False}
    return {}


engine = create_engine(settings.database_url, connect_args=_connect_args())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables and seed the demo admin user."""
    from backend.app import models  # noqa: F401  (register models on Base)
    from backend.app.security import hash_password

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        if db.query(models.User).filter_by(email="admin@example.com").first() is None:
            db.add(models.User(
                name="Demo Admin",
                email="admin@example.com",
                password_hash=hash_password("admin123"),
                role="admin",
            ))
            db.commit()