from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker


def create_engine_for_url(url: str = "sqlite+pysqlite:///:memory:") -> Engine:
    return create_engine(url, future=True)


def create_session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
