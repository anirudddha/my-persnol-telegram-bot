"""Thin async Postgres layer. Raw SQL — five tables do not need an ORM."""

import pathlib

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import DATABASE_URL

pool = AsyncConnectionPool(
    DATABASE_URL, open=False, kwargs={"row_factory": dict_row}, min_size=1, max_size=4
)


async def open_pool() -> None:
    """
    Purpose: Opens the PostgreSQL connection pool on application startup.
    Called by: jarvis.main (main()) and db.py CLI runner (_main()).
    Calls: pool.open()
    """
    await pool.open(wait=True)


async def close_pool() -> None:
    """
    Purpose: Closes the PostgreSQL connection pool gracefully on application shutdown.
    Called by: jarvis.main (main() finally block) and db.py CLI runner (_main()).
    Calls: pool.close()
    """
    await pool.close()


async def fetch(sql: str, *args) -> list[dict]:
    """
    Purpose: Executes a SELECT SQL query and returns all matching rows as a list of dictionaries.
    Called by: jarvis.handler (handle_message), jarvis.main (reminder_tick), and jarvis.tools.
    Calls: pool.connection(), conn.execute(), cur.fetchall()
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchall()


async def fetchone(sql: str, *args) -> dict | None:
    """
    Purpose: Executes a SELECT SQL query and returns a single row as a dictionary (or None).
    Called by: jarvis.tools (user_tz, _recent_duplicate, tool functions).
    Calls: pool.connection(), conn.execute(), cur.fetchone()
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchone()


async def execute(sql: str, *args) -> None:
    """
    Purpose: Executes a write SQL query (INSERT, UPDATE, DELETE) against the database.
    Called by: jarvis.handler (handle_message), jarvis.main (reminder_tick), and jarvis.tools.
    Calls: pool.connection(), conn.execute()
    """
    async with pool.connection() as conn:
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

    async def _main() -> None:
        await open_pool()
        try:
            await apply_schema()
            print("schema applied")
        finally:
            await close_pool()

    asyncio.run(_main())
