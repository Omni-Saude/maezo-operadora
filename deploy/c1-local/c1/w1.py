"""Passo `w1`: contorno LOCAL do F1, so para medir o que vem depois dele.

F1: `StaffCaseStore.designation()` le a designacao com `FOR UPDATE OF c`, o que exige UPDATE em
`mzo_staff_case_designation_current`; o pin do mesmo `StaffCaseStore` exige que o login do engine NAO
tenha UPDATE de tabela ali (`requireWrites`, `installed`), e a T1.4 concede so SELECT. Resultado medido:
`permission denied` e o engine nao sobe. O contorno concede UPDATE de UMA coluna (o
`has_table_privilege` do pin nao ve grant de coluna). NAO e correcao: a decisao (lock sem FOR UPDATE,
ou grant de coluna no DDL) e do backend.
"""

from __future__ import annotations

import asyncio

import asyncpg

from .common import OWNER_LOGIN, admin_dsn, step, tls_context


async def main_async() -> None:
    owner = await asyncpg.connect(admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context())
    try:
        await owner.execute(
            "GRANT UPDATE (designation_revision) ON maezo_native.mzo_staff_case_designation_current TO cibseven_app"
        )
    finally:
        await owner.close()
    step("w1", True, "contorno local do F1 aplicado (UPDATE de coluna em designation_current para cibseven_app)")


def main() -> None:
    asyncio.run(main_async())
