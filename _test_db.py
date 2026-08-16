"""Quick test: connect to Neon with loop_factory approach."""
import asyncio
import os
import pathlib
import selectors
import sys

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


async def main():
    loop = asyncio.get_running_loop()
    print(f"Event loop: {type(loop).__name__}")

    print("Connecting...")
    conn = await psycopg.AsyncConnection.connect(
        DATABASE_URL, row_factory=dict_row, autocommit=True, connect_timeout=30
    )
    print("Connected!")

    schema_sql = (pathlib.Path(__file__).parent / "jarvis" / "schema.sql").read_text()
    await conn.execute(schema_sql)
    print("Schema applied!")

    await conn.close()
    print("Done!")


if sys.platform == "win32":
    asyncio.run(
        main(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
else:
    asyncio.run(main())
