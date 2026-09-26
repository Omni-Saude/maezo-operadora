"""Passo `rotate` (servico `issuer`): rotacao da designacao r1 -> r2 pelo instalador `rows` e o emissor sob a r2.

1. `next-designation` (mesmas chaves; o `case_issuer` com as 4 projecoes, `staff_current_task.v1`
   incluida, e `source_namespace` da politica `@d2`) -> `sign-designation` com a raiz de TESTE;
2. `tools.staff_ops.rows.verify_designation` (os verificadores do runtime) aceita a r2 e RECUSA a
   mesma r2 com prova de outra raiz;
3. `rows.install_designation` como o dono do schema nativo: r1->r2 (`rotacionada`), de novo (`igual`),
   e a r1 depois da r2 (recusada, revisao menor); o historico guarda as duas revisoes;
4. o emissor (`python -m maezo.gateway.staff_cases`) roda com a composicao da r2.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from tools.staff_materials import approver
from tools.staff_materials.generate import next_designation
from tools.staff_ops import rows as rows_tool
from tools.staff_ops.common import OpsError

from maezo.gateway.external_cases.models import timestamp
from maezo.portal.engine.profile import strict_loads

from . import issuer
from .common import (
    APPROVER_OUT,
    ENGINE_NAME,
    ENVIRONMENT,
    INCARNATION,
    MATERIALS,
    NATIVE_SCHEMA,
    OWNER_LOGIN,
    ROOT,
    TENANT,
    TEST_ROOT,
    admin_dsn,
    earlier,
    jcs,
    save_state,
    step,
    tls_context,
    write,
)

ROTATION = ROOT / "rotation"
SCOPE = dict(tenant=TENANT, environment=ENVIRONMENT, engine_name=ENGINE_NAME, database_incarnation=INCARNATION)


def _rows(designation: bytes, proof: bytes, revision: int) -> dict[str, Any]:
    return dict(
        scope=SCOPE,
        designation_revision=revision,
        designation=designation,
        designation_sha256=hashlib.sha256(designation).hexdigest(),
        proof=proof,
        root=(APPROVER_OUT / "installation-root.der").read_bytes(),
    )


async def _install(rows: dict[str, Any]) -> dict[str, str]:
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
    try:
        async with owner.transaction():
            await owner.execute(f"SET LOCAL search_path = {NATIVE_SCHEMA}")
            return await rows_tool.install_designation(owner, rows)
    finally:
        await owner.close()


async def _history() -> tuple[list[int], int]:
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
    try:
        events = await owner.fetch(
            f"SELECT designation_revision FROM {NATIVE_SCHEMA}.mzo_staff_case_designation_event "
            "WHERE tenant=$1 ORDER BY 1", TENANT
        )
        current = await owner.fetchval(
            f"SELECT designation_revision FROM {NATIVE_SCHEMA}.mzo_staff_case_designation_current WHERE tenant=$1",
            TENANT,
        )
        return [int(e["designation_revision"]) for e in events], int(current)
    finally:
        await owner.close()


def main() -> None:
    r1 = (MATERIALS / "portal" / "designation.json").read_bytes()
    r1_proof = (APPROVER_OUT / "installation-proof.json").read_bytes()
    previous = strict_loads(r1)
    draft = next_designation(previous, not_before=earlier(minutes=1), valid_until=previous["valid_until"])
    r2 = jcs(draft)
    root = approver.load_root(TEST_ROOT / "installation-root-key.pem")
    _, digest, _ = approver.review_designation(r2)
    expires = timestamp(draft["valid_until"]) - timedelta(minutes=1)
    r2_proof = approver.sign_designation(r2, root, confirm_digest=digest, expires_at=expires)
    write(ROTATION / "designation.json", r2, 0o444)
    write(ROTATION / "installation-proof.json", r2_proof, 0o444)
    issuer_entry = next(e for e in draft["entries"] if e["role"] == "case_issuer")
    checks: dict[str, str] = {}

    rows_r2 = _rows(r2, r2_proof, 2)
    rows_tool.verify_designation(rows_r2)
    other = approver.sign_designation(
        r2, _other_root(), confirm_digest=digest, expires_at=expires
    )
    try:
        rows_tool.verify_designation(_rows(r2, other, 2))
        checks["assinatura_invalida"] = "ACEITA (erro)"
    except OpsError:
        checks["assinatura_invalida"] = "recusada"

    first = asyncio.run(_install(rows_r2))
    again = asyncio.run(_install(rows_r2))
    try:
        asyncio.run(_install(_rows(r1, r1_proof, 1)))
        checks["r1_depois_da_r2"] = "ACEITA (erro)"
    except OpsError:
        checks["r1_depois_da_r2"] = "recusada"
    events, current = asyncio.run(_history())

    run = ROOT / "issuer-run-r2"
    completed = issuer.run_issuer(
        issuer.composition(
            run, designation=ROTATION / "designation.json", proof=ROTATION / "installation-proof.json",
            designation_digest=digest,
        )
    )
    line = (completed.stdout.strip().splitlines() or [completed.stderr.strip()[-300:]])[-1]
    save_state("rotation", dict(designation_digest=digest, revision=2))
    ok = (
        first["designation_current"] == "rotacionada r1->r2"
        and again["designation_event"] == again["designation_current"] == "igual"
        and set(checks.values()) == {"recusada"}
        and events == [1, 2]
        and current == 2
        and "staff_current_task.v1" in issuer_entry["projections"]
        and completed.returncode == 0
    )
    step(
        "rotate",
        ok,
        f"r2 {digest[:12]} case_issuer={issuer_entry['projections']} ns={issuer_entry['source_namespace']}; "
        f"1a={first['designation_current']} 2a={again['designation_current']} {checks}; "
        f"historico={events} corrente=r{current}; emissor(r2) rc={completed.returncode} {line}",
    )


def _other_root() -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate()


if __name__ == "__main__":
    main()
