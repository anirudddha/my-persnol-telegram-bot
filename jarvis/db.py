"""Thin async Postgres layer. Raw SQL — five tables do not need an ORM.

On Windows with Python 3.14, two adjustments are needed:
1. Use SelectorEventLoop (psycopg cannot use ProactorEventLoop).
2. Force IPv4 resolution — many home networks resolve AAAA (IPv6) first
   but cannot actually route IPv6, causing silent connection hangs.
"""

import pathlib
import socket
import sys

import psycopg
from psycopg.rows import dict_row

from .config import DATABASE_URL


async def _connect():
    """Return a new async connection with dict rows, forcing IPv4."""
    return await psycopg.AsyncConnection.connect(
        DATABASE_URL,
        row_factory=dict_row,
        autocommit=True,
        connect_timeout=30,
        # Force IPv4 to avoid hanging on broken IPv6 networks.
        hostaddr=_resolve_ipv4(),
    )


def _resolve_ipv4() -> str | None:
    """Resolve the database hostname to an IPv4 address.

    Returns None (letting psycopg resolve normally) if the hostname
    is already an IP or cannot be extracted from the connection string.
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(DATABASE_URL)
        host = parsed.hostname
        if not host:
            return None
        # Already an IP address?
        try:
            socket.inet_pton(socket.AF_INET, host)
            return None  # Already IPv4, no need for hostaddr
        except OSError:
            pass
        # Resolve to IPv4 explicitly.
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    return None


async def open_pool() -> None:
    """
    Purpose: Validates that the database is reachable on startup.
    Called by: jarvis.main (main()) and db.py CLI runner (_main()).
    """
    conn = await _connect()
    await conn.close()


async def close_pool() -> None:
    """
    Purpose: No-op kept for API compatibility with callers.
    Called by: jarvis.main (main() finally block) and db.py CLI runner (_main()).
    """
    pass


async def fetch(sql: str, *args) -> list[dict]:
    """
    Purpose: Executes a SELECT SQL query and returns all matching rows as a list of dictionaries.
    Called by: jarvis.handler (handle_message), jarvis.main (reminder_tick), and jarvis.tools.
    Calls: AsyncConnection.connect(), conn.execute(), cur.fetchall()
    """
    async with await _connect() as conn:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchall()


async def fetchone(sql: str, *args) -> dict | None:
    """
    Purpose: Executes a SELECT SQL query and returns a single row as a dictionary (or None).
    Called by: jarvis.tools (user_tz, _recent_duplicate, tool functions).
    Calls: AsyncConnection.connect(), conn.execute(), cur.fetchone()
    """
    async with await _connect() as conn:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchone()


async def execute(sql: str, *args) -> None:
    """
    Purpose: Executes a write SQL query (INSERT, UPDATE, DELETE) against the database.
    Called by: jarvis.handler (handle_message), jarvis.main (reminder_tick), and jarvis.tools.
    Calls: AsyncConnection.connect(), conn.execute()
    """
    async with await _connect() as conn:
        await conn.execute(sql, args or None)


async def apply_schema() -> None:
    """
    Purpose: Reads schema.sql and runs all table creation statements against the database.
    Called by: db.py CLI script (_main()).
    Calls: pathlib.Path.read_text(), db.execute()
    """
    sql = (pathlib.Path(__file__).parent / "schema.sql").read_text()
    await execute(sql)


if __name__ == "__main__":
    import asyncio
    import selectors

    async def _main() -> None:
        await open_pool()
        await apply_schema()
        print("schema applied")

    if sys.platform == "win32":
        asyncio.run(
            _main(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(_main())
