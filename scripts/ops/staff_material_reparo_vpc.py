# Roda DENTRO da VPC, na task `maezo-operadora-dev-staff-syn` (imagem de operacao, task role que le o
# segredo do dono do schema nativo por GetSecretValue). Disparo: `scripts/ops/staff_vpc_run.sh`.
# Runbook: docs/runbooks/renovar-material-staff-dev.md, passo 6. So metadados na saida.
#
# MODO=medir (padrao): ledger do emissor por politica, qualificacao AUTH, geracao de atribuicao,
#   designacao corrente e as 3 ultimas admissoes.
# MODO=aplicar: depois da rotacao da designacao (rows), destrava o emissor e renova a qualificacao AUTH:
#   * publicacao PENDENTE numa politica que nao e a da designacao corrente e que o engine NUNCA viu
#     (sem recibo e sem source_event) -> pending_request=NULL. O emissor so semeia a politica nova
#     sem pendencia nas outras (`case_issuer_sources._seed`), e a rodada da politica antiga nunca mais
#     acontece (a designacao dela foi substituida). Se o engine viu a publicacao, NAO mexe (recusa).
#   * qualificacao AUTH sintetica (`mzo_auth_installation`) vencida antes de RENOVAR_ATE -> valid_until
#     = RENOVAR_ATE (o fim da designacao nova). Nenhuma ferramenta renova esse campo (rows so
#     requalifica o digest do codigo; a fixture SYN so insere).
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime

import asyncpg
import boto3
from tools.staff_install.installer import parse_credential, tls_context

from maezo.gateway.staff_cases.case_issuer import policy_ref_for
from maezo.portal.engine.profile import canonicalize

MODO = os.environ.get("MODO", "medir")
ATE = os.environ.get("RENOVAR_ATE", "")
FORMATO = "%Y-%m-%dT%H:%M:%S.%fZ"  # o instante do perfil (models.instant): AAAA-MM-DDTHH:MM:SS.ffffffZ


def instante(valor: str) -> datetime:
    """ISO-8601 UTC no formato exato do perfil; outra forma = recusa (nunca comparar como string)."""
    momento = datetime.strptime(valor, FORMATO).replace(tzinfo=UTC)
    if momento.strftime(FORMATO) != valor:
        raise SystemExit(f"instante fora do formato {FORMATO}")
    return momento


if MODO not in ("medir", "aplicar"):
    raise SystemExit("MODO: medir ou aplicar")
if MODO == "aplicar":
    if not ATE:
        raise SystemExit("MODO=aplicar exige RENOVAR_ATE")
    if instante(ATE) <= datetime.now(UTC):
        raise SystemExit("RENOVAR_ATE no passado")
SEGREDO = os.environ["STAFF_OWNER_SECRET_ARN"]


async def main() -> None:
    senha = parse_credential(
        boto3.client("secretsmanager", region_name="sa-east-1").get_secret_value(SecretId=SEGREDO)[
            "SecretString"
        ],
        "maezo_native_schema_owner",
    )
    c = await asyncpg.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user="maezo_native_schema_owner",
        password=senha,
        database=os.environ["DB_NAME"],
        ssl=tls_context(None),
        timeout=15,
    )
    out: dict = {"modo": MODO}
    try:
        async with c.transaction():
            await c.execute("SET LOCAL search_path = maezo_native")
            atual = await c.fetchval("SELECT designation_revision FROM mzo_staff_case_designation_current")
            politica = policy_ref_for(atual)
            out["designacao_corrente"], out["politica_corrente"] = atual, politica
            ledger = []
            for r in await c.fetch(
                "SELECT policy_ref, revision, pending_request FROM mzo_staff_case_issuer_ledger "
                "ORDER BY policy_ref FOR UPDATE"
            ):
                item = {
                    "politica": r["policy_ref"],
                    "revision": r["revision"],
                    "pendente": r["pending_request"] is not None,
                }
                if r["pending_request"] is not None:
                    pid = json.loads(bytes(r["pending_request"])).get("publication_id")
                    viu = await c.fetchval(
                        "SELECT (SELECT count(*) FROM mzo_staff_case_publication_receipt "
                        "WHERE publication_id=$1)"
                        " + (SELECT count(*) FROM mzo_staff_case_source_event WHERE publication_id=$1)",
                        pid,
                    )
                    item.update(
                        publicacao=pid,
                        engine_viu=bool(viu),
                        sha256=hashlib.sha256(bytes(r["pending_request"])).hexdigest(),
                    )
                    if MODO == "aplicar" and r["policy_ref"] != politica and not viu:
                        item["acao"] = await c.execute(
                            "UPDATE mzo_staff_case_issuer_ledger SET pending_request=NULL, "
                            "revision=revision+1, updated_at=now() WHERE policy_ref=$1 AND revision=$2 "
                            "AND pending_request IS NOT NULL",
                            r["policy_ref"],
                            r["revision"],
                        )
                ledger.append(item)
            out["ledger"] = ledger
            q = await c.fetchrow(
                "SELECT qualification_ FROM mzo_auth_installation WHERE tenant_='amh' FOR UPDATE"
            )
            if q is not None:
                qual = json.loads(q["qualification_"])
                out["auth_valid_until"] = qual["valid_until"]
                if MODO == "aplicar" and instante(qual["valid_until"]) < instante(ATE):
                    out["auth_valid_until_antes"] = qual["valid_until"]
                    qual["valid_until"] = ATE
                    out["auth_acao"] = await c.execute(
                        "UPDATE mzo_auth_installation SET qualification_=$1 WHERE tenant_='amh'",
                        canonicalize(qual).decode(),
                    )
                    out["auth_valid_until"] = ATE
            out["geracao_atribuicao"] = [
                dict(r)
                for r in await c.fetch(
                    "SELECT source_revision_, state_, valid_until_ FROM mzo_human_assignment_generation "
                    "WHERE tenant_='amh' ORDER BY source_revision_ DESC LIMIT 2"
                )
            ]
            out["admissoes"] = [
                dict(r)
                for r in await c.fetch(
                    "SELECT revision_, revoked_ FROM mzo_portal_read_admission "
                    "ORDER BY revision_ DESC LIMIT 3"
                )
            ]
    finally:
        await c.close()
    print("REPARO " + json.dumps(out, default=str))


asyncio.run(main())
