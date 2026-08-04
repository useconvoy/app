"""Connection handling for projection reads/writes.

RLS context is set on every connection use — a query without tenant context is
a bug even in a dedicated stack (CLAUDE.md rule 10). `tenant_connection` is the
only sanctioned way to get a connection: it opens a transaction and sets
`app.tenant_id` locally to it, so the setting can never leak across pool users.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool


class ProjectionsDB:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8) -> None:
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30.0)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def tenant_connection(self, tenant_id: str) -> AsyncGenerator[AsyncConnection[DictRow]]:
        """A pooled connection with RLS tenant context set, inside a transaction."""
        if not tenant_id:
            raise ValueError("tenant_id is required for every projection query")
        async with self._pool.connection() as conn, conn.transaction():
            # set_config(..., is_local=true): scoped to this transaction only.
            await conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            yield conn
