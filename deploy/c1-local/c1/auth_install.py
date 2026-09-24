"""Passo `auth-install` (servico `issuer`, REST do engine em localhost): D8 com a definicao DEPLOYADA.

O `engine-config` grava a linha `MZO_AUTH_INSTALLATION` antes de o engine existir, entao a
qualificacao nao tem como nomear uma definicao real. Este passo roda logo depois do `engine-up`
(o `AuthRuntime` le a instalacao a cada invocacao, nao no boot):

1. deploy (tenant `amh`, filtro de duplicata) de SP-OP-AUTH-001, SP-OP-ESCALATION-001 e da DMN
   `escalation_routing`, os mesmos bytes que o passo `issuer` usa;
2. `definition` da qualificacao = `definition_id`, `deployment_id` e SHA-256 dos BYTES deployados
   (o que `AuthRuntime.Invocation.definition` confere);
3. UPDATE da `qualification_` pelo dono nativo, mesma revisao.

Continua sintetico (sem produtor no repo): `profile_digest`, `source_freeze_contract_digest`,
`cutover_ref`, `review_receipt_ref`, `runtime_qualification_ref`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import asyncpg
import httpx

from .common import OWNER_LOGIN, TENANT, admin_dsn, save_state, step, tls_context
from .issuer import REST, _deploy


def _definition(client: httpx.Client) -> dict:
    found = client.get(
        "/process-definition",
        params={"key": "SP-OP-AUTH-001", "tenantIdIn": TENANT, "latestVersion": "true"},
    ).json()
    if len(found) != 1:
        raise RuntimeError(f"SP-OP-AUTH-001 deployada {len(found)} vezes no tenant {TENANT}")
    definition = found[0]
    resources = client.get(f"/deployment/{definition['deploymentId']}/resources").json()
    resource = [r for r in resources if r["name"] == definition["resource"]]
    data = client.get(f"/deployment/{definition['deploymentId']}/resources/{resource[0]['id']}/data")
    data.raise_for_status()
    return dict(id=definition["id"], deployment=definition["deploymentId"], digest=hashlib.sha256(data.content).hexdigest())


async def _requalify(definition: dict) -> dict:
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
    try:
        async with owner.transaction():
            await owner.execute("SET LOCAL search_path = maezo_native")
            raw = await owner.fetchval("SELECT qualification_ FROM mzo_auth_installation WHERE tenant_=$1 FOR UPDATE", TENANT)
            qualification = json.loads(raw)
            qualification["definition"].update(
                definition_id=definition["id"], deployment_id=definition["deployment"],
                definition_digest=definition["digest"],
            )
            from maezo.portal.engine.profile import canonicalize

            await owner.execute(
                "UPDATE mzo_auth_installation SET qualification_=$2 WHERE tenant_=$1",
                TENANT, canonicalize(qualification).decode(),
            )
            return qualification["definition"]
    finally:
        await owner.close()


def main() -> None:
    with httpx.Client(base_url=REST, timeout=30, trust_env=False) as client:
        deployment = _deploy(client)
        definition = _definition(client)
    qualified = asyncio.run(_requalify(definition))
    save_state("auth", dict(definition=qualified))
    step("auth-install", True,
         f"deploy {deployment.get('id', '?')[:8]}; qualificacao AUTH -> {qualified['definition_id']} "
         f"(deployment {qualified['deployment_id'][:8]}, sha256 {qualified['definition_digest'][:12]})")


if __name__ == "__main__":
    main()
