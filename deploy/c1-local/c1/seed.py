"""Passo `seed`: o login do BFF (como o `portal_bff_amh` de dev) e duas memberships staff revisadas.

* `staff-c1-no-grupo`: grupo `atendimento-humano`, o que a DMN `escalation_routing` escolhe para
  `motivo_categoria=solicitacao_humano` (a escalacao que o passo `issuer` abre);
* `staff-c1-outro-grupo`: grupo `enfermagem-triagem`, o negativo (N3: so o grupo da DMN ve o caso).
A membership e dado de identidade revisado (migracao 0012); o harness grava como `maezo_app`, o dono.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import asyncpg

from maezo.portal.api.records import MembershipRecord

from .common import ADMIN, TENANT, admin_dsn, now, password, read_text, save_state, step, tls_context, write

ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_C1LocalPool"
BFF_LOGIN = "portal_bff_amh"
PRINCIPALS = {
    "staff-c1-no-grupo": ("00000000-0000-4000-8000-0000000c1001", "atendimento-humano"),
    "staff-c1-outro-grupo": ("00000000-0000-4000-8000-0000000c1002", "enfermagem-triagem"),
    # O grupo LITERAL de `UT_SupervisorAssume` (SLA de resolucao vencido): passo `supervisor`.
    "staff-c1-supervisor": ("00000000-0000-4000-8000-0000000c1003", "supervisao-atendimento"),
}


def record(principal: str, subject: str, group: str) -> MembershipRecord:
    return MembershipRecord(
        tenant=TENANT, issuer=ISSUER, subject=subject, principal_ref=principal, revision=1, audience="staff",
        memberships=({"membership_ref": f"{principal}-m1", "roles": ("atendente",), "groups": (group,)},),
        subject_bindings=(), reviewed_until=now() + timedelta(days=7),
    )


async def main_async() -> None:
    if not (ADMIN / f"{BFF_LOGIN}-password").exists():
        write(ADMIN / f"{BFF_LOGIN}-password", password(), 0o400)
    secret = read_text(ADMIN / f"{BFF_LOGIN}-password")
    su = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
    try:
        exists = await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", BFF_LOGIN)
        await su.execute(f"{'ALTER' if exists else 'CREATE'} ROLE {BFF_LOGIN} LOGIN PASSWORD '{secret}'")
        await su.execute(f"ALTER ROLE {BFF_LOGIN} SET search_path = {TENANT}")
    finally:
        await su.close()
    app = await asyncpg.connect(admin_dsn(user="maezo_app", password_file="maezo-app-password"), ssl=tls_context())
    try:
        await app.execute(f"GRANT USAGE ON SCHEMA {TENANT} TO {BFF_LOGIN}")
        await app.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TENANT}.portal_login_transactions, "
            f"{TENANT}.portal_code_claims, {TENANT}.portal_sessions TO {BFF_LOGIN}"
        )
        await app.execute(f"GRANT SELECT ON {TENANT}.portal_memberships TO {BFF_LOGIN}")
        for principal, (subject, group) in PRINCIPALS.items():
            payload = record(principal, subject, group).model_dump_json()
            await app.execute(
                f"INSERT INTO {TENANT}.portal_memberships VALUES($1,$2,$3,$4,$5) "
                "ON CONFLICT (tenant, issuer, subject) DO UPDATE SET payload=EXCLUDED.payload",
                TENANT, ISSUER, subject, principal, payload,
            )
        count = await app.fetchval(f"SELECT count(*) FROM {TENANT}.portal_memberships")
    finally:
        await app.close()
    save_state("seed", dict(issuer=ISSUER, principals=PRINCIPALS))
    step("seed", count == len(PRINCIPALS), f"{count} memberships staff (atendimento-humano / enfermagem-triagem / supervisao-atendimento); login {BFF_LOGIN}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
