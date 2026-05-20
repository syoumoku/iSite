from __future__ import annotations

import os
from functools import lru_cache

from isite2.repositories.memory import InMemoryScanRunRepository
from isite2.repositories.sqlalchemy import (
    PostGISScanRunRepository,
    create_postgis_first_repository,
)

DEFAULT_POSTGIS_URL = "postgresql+psycopg://isite:isite@127.0.0.1:5432/isite2"
DEFAULT_SQLITE_FALLBACK_URL = "sqlite+pysqlite:///outputs/isite2_dev.db"


@lru_cache(maxsize=1)
def get_default_repository() -> PostGISScanRunRepository:
    database_url = os.getenv("DATABASE_URL") or os.getenv(
        "ISITE2_DATABASE_URL",
        DEFAULT_POSTGIS_URL,
    )
    if os.getenv("ISITE2_APP_MODE", "").strip() == "public_view":
        return create_postgis_first_repository(database_url, fallback_url=None)
    fallback_url = os.getenv("ISITE2_SQLITE_FALLBACK_URL", DEFAULT_SQLITE_FALLBACK_URL)
    return create_postgis_first_repository(database_url, fallback_url=fallback_url)


__all__ = [
    "DEFAULT_POSTGIS_URL",
    "DEFAULT_SQLITE_FALLBACK_URL",
    "InMemoryScanRunRepository",
    "PostGISScanRunRepository",
    "get_default_repository",
]
