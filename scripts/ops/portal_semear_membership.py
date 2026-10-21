# Semeia (idempotente) linhas de `amh.portal_memberships`: as pessoas do portal de DEV (staff).
#
# Roda DENTRO da VPC na task `maezo-operadora-dev-bootstrap-db` (credencial mestre `amh_admin` por `secrets`; a
# imagem e a do app, entao o proprio `MembershipRecord` valida o registro). Disparo: `scripts/ops/run-db-task.ps1`
# ou, no Git Bash, a variante por boto3 do runbook `docs/runbooks/portal-dev-provisionamento.md` §5.
# O override carrega so' CODIGO e variaveis NAO secretas (o `sub` do Cognito e' um identificador, nao um segredo).
#
# Mesmo caminho de `plantao.teste`/`atendimento.teste` (`portal-dev-provisionamento.md` §3.8): uma linha por
# pessoa em `amh.portal_memberships(tenant, issuer, subject, principal_ref, payload)`, `payload` =
# `MembershipRecord` em JSON validado por `model_validate_json` (modo JSON, `strict=True`), `revision=1`,
# `audience=staff`, `reviewed_until=2026-12-31T23:59:59Z`. O job T1.5 publica a membership em ate' ~5 min.
#
# Variaveis:
#   MODO     ler (padrao: so mostra o que faria) | semear (grava)
#   PESSOAS  JSON: [{"subject": "<sub do Cognito>", "groups": ["g"], "roles": ["r"]}, ...]
#   TENANT   padrao amh
#   ISSUER   padrao: o pool humano do dev (`portal.auto.tfvars`)
#   REVIEWED_UNTIL  padrao 2026-12-31T23:59:59Z
#
# Idempotencia: o mesmo conteudo e' `igual` (nada e' escrito); grupos/papeis diferentes sobem `revision`
# (mantendo `principal_ref` e `membership_ref`, o contrato do `purge`/revogacao); uma linha nova ganha refs derivados
# do `sub` (mesmo `sub` => mesmos refs). Nunca apaga linha e nunca imprime o `sub`.
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from typing import Any

ISSUER_PADRAO = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_9oKv7gHOJ"
REVIEWED_PADRAO = "2026-12-31T23:59:59Z"
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")


def _hex(*partes: str) -> str:
    return hashlib.sha256("\x1f".join(partes).encode()).hexdigest()[:32]


def montar_registro(
    *,
    tenant: str,
    issuer: str,
    subject: str,
    groups: list[str],
    roles: list[str],
    reviewed_until: str,
    anterior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """O `payload` (dict) de uma pessoa. Puro: sem banco. Com `anterior` mantem refs e sobe a revisao."""
    if not subject or not groups or not roles:
        raise SystemExit("cada pessoa precisa de subject, groups e roles nao vazios")
    for valor in (tenant, *groups, *roles):
        if not _REF.fullmatch(valor):
            raise SystemExit(f"ref invalida: {valor!r}")
    principal = "prn_" + _hex("maezo-portal-principal/v1", tenant, issuer, subject)
    if anterior is not None and anterior["principal_ref"] != principal:
        principal = anterior["principal_ref"]
    membership = (
        anterior["memberships"][0]["membership_ref"]
        if anterior is not None and anterior.get("memberships")
        else "mbr_" + _hex("maezo-portal-membership/v1", principal)
    )
    return {
        "tenant": tenant,
        "issuer": issuer,
        "subject": subject,
        "principal_ref": principal,
        "revision": 1 if anterior is None else int(anterior["revision"]) + 1,
        "audience": "staff",
        "memberships": [
            {"membership_ref": membership, "roles": sorted(set(roles)), "groups": sorted(set(groups))}
        ],
        "subject_bindings": [],
        "reviewed_until": reviewed_until,
        "revoked": False,
    }


def igual(antigo: dict[str, Any], novo: dict[str, Any]) -> bool:
    """Mesmo conteudo (a `revision` do novo e' a seguinte; nao entra na comparacao)."""
    campos = ("tenant", "issuer", "subject", "principal_ref", "audience", "memberships", "subject_bindings")
    return (
        all(antigo[c] == novo[c] for c in campos)
        and antigo["reviewed_until"] == novo["reviewed_until"]
        and not antigo["revoked"]
    )


async def main() -> None:
    import asyncpg

    modo = os.environ.get("MODO", "ler")
    if modo not in ("ler", "semear"):
        raise SystemExit("MODO: ler ou semear")
    pessoas = json.loads(os.environ["PESSOAS"])
    tenant = os.environ.get("TENANT", "amh")
    issuer = os.environ.get("ISSUER", ISSUER_PADRAO)
    revisado = os.environ.get("REVIEWED_UNTIL", REVIEWED_PADRAO)

    from maezo.portal.api.records import MembershipRecord

    conexao = await asyncpg.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["ADMIN_USER"],
        password=os.environ["ADMIN_PASSWORD"],
        database=os.environ.get("DB_NAME", "maezo"),
    )
    try:
        for pessoa in pessoas:
            async with conexao.transaction():
                linha = await conexao.fetchrow(
                    "SELECT payload FROM amh.portal_memberships WHERE tenant=$1 AND issuer=$2 AND subject=$3 FOR UPDATE",
                    tenant,
                    issuer,
                    pessoa["subject"],
                )
                antigo = json.loads(linha["payload"]) if linha else None
                novo = montar_registro(
                    tenant=tenant,
                    issuer=issuer,
                    subject=pessoa["subject"],
                    groups=pessoa["groups"],
                    roles=pessoa["roles"],
                    reviewed_until=revisado,
                    anterior=antigo,
                )
                if antigo is not None and igual(antigo, novo):
                    print(f"{novo['principal_ref']}: igual (revision {antigo['revision']})")
                    continue
                bruto = json.dumps(novo, sort_keys=True, separators=(",", ":"))
                MembershipRecord.model_validate_json(bruto)  # o MESMO modelo do store (strict, modo JSON)
                acao = (
                    "inserida"
                    if antigo is None
                    else f"atualizada revision {antigo['revision']}->{novo['revision']}"
                )
                if modo == "ler":
                    print(
                        f"{novo['principal_ref']}: ({acao}) grupos={novo['memberships'][0]['groups']} "
                        f"papeis={novo['memberships'][0]['roles']} [MODO=ler: nada gravado]"
                    )
                    continue
                await conexao.execute(
                    "INSERT INTO amh.portal_memberships(tenant,issuer,subject,principal_ref,payload) "
                    "VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant,issuer,subject) DO UPDATE SET payload=EXCLUDED.payload",
                    tenant,
                    issuer,
                    pessoa["subject"],
                    novo["principal_ref"],
                    bruto,
                )
                lido = await conexao.fetchval(
                    "SELECT payload FROM amh.portal_memberships WHERE tenant=$1 AND issuer=$2 AND subject=$3",
                    tenant,
                    issuer,
                    pessoa["subject"],
                )
                reg = MembershipRecord.model_validate_json(lido)  # releitura validada, como o store
                print(
                    f"{reg.principal_ref}: {acao}; releitura valida (grupos={list(reg.memberships[0].groups)}, "
                    f"papeis={list(reg.memberships[0].roles)}, revoked={reg.revoked})"
                )
    finally:
        await conexao.close()


if __name__ in ("__main__", "vpc"):
    asyncio.run(main())
