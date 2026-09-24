"""Diagnostico: `python -m c1.sql <login> "<sql>"` roda uma consulta como o login, pelo TLS do banco local.

Logins: postgres, maezo_app, cibseven_app (search_path do engine, `maezo_native,cibseven`) e os 4
logins nativos. So para inspecionar o banco descartavel do C1.
"""

from __future__ import annotations

import asyncio
import sys

import asyncpg

from .common import admin_dsn, tls_context

FILES = {"postgres": "postgres-password", "maezo_app": "maezo-app-password", "cibseven_app": "cibseven-password"}


async def run(login: str, sql: str) -> None:
    connection = await asyncpg.connect(
        admin_dsn(user=login, password_file=FILES.get(login, f"{login}-password")), ssl=tls_context(), timeout=10
    )
    try:
        if login == "cibseven_app":
            await connection.execute("SET search_path = maezo_native, cibseven")
        async with connection.transaction():
            if sql.lstrip().lower().startswith(("select", "with")):
                for row in await connection.fetch(sql):
                    print(dict(row))
            else:
                print(await connection.execute(sql))
    except Exception as failure:  # noqa: BLE001 - diagnostico
        print(f"{type(failure).__name__}: {failure}")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], sys.argv[2]))
