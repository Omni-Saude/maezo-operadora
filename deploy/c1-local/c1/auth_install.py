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

import asyncpg
import httpx

from maezo.tools.dev_syn_fixture import core

from .common import NATIVE_SCHEMA, OWNER_LOGIN, TENANT, admin_dsn, save_state, step, tls_context
from .issuer import REST, _deploy


async def _requalify(definition: dict) -> dict:
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
    try:
        return await core.requalify(owner, NATIVE_SCHEMA, TENANT, definition)
    finally:
        await owner.close()


def main() -> None:
    with httpx.Client(base_url=REST, timeout=30, trust_env=False) as client:
        deployment = _deploy(client)
        definition = core.deployed_definition(client, TENANT)
    qualified = asyncio.run(_requalify(definition))
    save_state("auth", dict(definition=qualified))
    step("auth-install", True,
         f"deploy {deployment.get('id', '?')[:8]}; qualificacao AUTH -> {qualified['definition_id']} "
         f"(deployment {qualified['deployment_id'][:8]}, sha256 {qualified['definition_digest'][:12]})")


if __name__ == "__main__":
    main()
