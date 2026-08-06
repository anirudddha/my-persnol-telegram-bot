"""Thin async Postgres layer. Raw SQL — five tables do not need an ORM."""

import pathlib

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import DATABASE_URL

pool = AsyncConnectionPool(
    DATABASE_URL, open=False, kwargs={"row_factory": dict_row}, min_size=1, max_size=4
)


async def open_pool() -> None:
    await pool.open(wait=True)


async def close_pool() -> None:
    await pool.close()


async def fetch(sql: str, *args) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchall()


async def fetchone(sql: str, *args) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchone()


async def execute(sql: str, *args) -> None:
    async with pool.connection() as conn:
        await conn.execute(sql, args or None)


async def apply_schema() -> None:
    """Run schema.sql. Idempotent — every statement is `if not exists`."""
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
