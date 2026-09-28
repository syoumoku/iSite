from __future__ import annotations

import argparse
import os
import re

import psycopg
from sqlalchemy.engine import make_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the independent iSite2 operational database when it is absent."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("ISITE2_OPS_DATABASE_URL"),
        help="Target SQLAlchemy PostgreSQL URL. Defaults to ISITE2_OPS_DATABASE_URL.",
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("ISITE2_OPS_DATABASE_URL is required")
    url = make_url(args.database_url)
    database = url.database or ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise SystemExit("operational database name is not a safe PostgreSQL identifier")
    admin_url = url.set(database="postgres", drivername="postgresql+psycopg")
    connection_info = admin_url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    with psycopg.connect(connection_info, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if exists is None:
            connection.execute(f'CREATE DATABASE "{database}"')
            print(f"Created operational database {database}")
        else:
            print(f"Operational database {database} already exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
