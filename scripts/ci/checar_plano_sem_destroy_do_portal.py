"""Reprova um plano do Terraform que destrua recursos duraveis do portal.

    terraform plan -out=plano.tfplan
    terraform show -json plano.tfplan > plano.json
    python scripts/ci/checar_plano_sem_destroy_do_portal.py plano.json
    terraform apply plano.tfplan      # so' se a linha acima passou

O QUE ELA GUARDA. O portal e' composto por `for_each = local.portal_config`, e
`local.portal_config` sai de `var.portal`. Os valores de `var.portal` moram em
`deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars`, que hoje so' existe no PR
#454. Num checkout sem esse arquivo `var.portal` e' `null`, o `for_each` fica
vazio, e o Terraform conclui — corretamente, pela configuracao que esta' vendo —
que o portal inteiro deve ser destruido: 43 destroys, medidos parando um apply no
meio em 21/09. O `aws_security_group.portal` e' o pior deles, porque o id dele
esta' referenciado na regra de ingress do Aurora, em OUTRO repositorio e OUTRO
state (`amh-data-platform#175`): destrui-lo aqui deixa ponteiro orfao la'.

POR QUE NAO `prevent_destroy`, que era a primeira alternativa do documento. Ele
foi escrito, aplicado e revertido no mesmo dia, e o motivo esta' medido: os seis
recursos entram em `tests/portal.tftest.hcl`, que e' justamente a suite que prova
a composicao "dark" — ela APLICA com `portal = null` e o `terraform test` destroi
o que criou ao terminar. Com `prevent_destroy` o teste fica vermelho
(`Instance cannot be destroyed`, job 106564523861), e a saida seria enfraquecer o
teste que prova a propriedade permanente para manter um remendo temporario. Troca
ruim. Esta cerca olha o PLANO, que e' onde o acidente aparece, e nao mexe no grafo.

O QUE ELA NAO FAZ. Ela nao impede ninguem de destruir o portal de proposito — nao
e' para isso. Destruir o portal de verdade e' passar `--permitir-destruir`, que
exige escrever a intencao na linha de comando. O que ela impede e' o destroy que
ninguem pediu, vindo de um arquivo de variaveis ausente.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: Um indice de recurso ou de modulo: `["this"]`, `[0]`.
_INDICE = re.compile(r"\[[^\]]*\]")

#: Os recursos cuja destruicao NAO e' rotina. O criterio nao e' "tudo do portal":
#: a task definition e' SUBSTITUIDA a cada troca de imagem e o servico e' escalado
#: a zero por `portal_enabled=false` (ver `service-portal.tf:291`) — os dois sao
#: movimento normal, e cobra-los aqui faria a cerca reprovar o uso correto, que e'
#: a maneira mais rapida de ensinar o time a ignora-la.
DURAVEIS = (
    "aws_security_group.portal",
    "aws_security_group.portal_ingress",
    "aws_lb_target_group.portal",
    "aws_lb.portal",
    "aws_lb_listener.portal",
    "aws_cloudwatch_log_group.portal",
    "aws_iam_role.portal_execution",
    "aws_iam_role.portal_task",
)

#: `delete` sozinho e' destruicao. A dupla `create`+`delete` (ou `delete`+`create`)
#: e' SUBSTITUICAO, e substituir um SG ou um target group tambem invalida o id que
#: o outro state guarda — entao conta igual.
ACOES_QUE_DESTROEM = frozenset({"delete"})


def _endereco_base(endereco: str) -> str:
    """`aws_security_group.portal["this"]` -> `aws_security_group.portal`.

    Tira os indices e o prefixo de modulo. O prefixo importa: a mesma cerca serve
    se um dia o portal virar modulo, e `module.portal["dev"].aws_lb.portal[0]`
    continua sendo o `aws_lb.portal` que interessa. Por isso o indice e' removido
    com expressao regular em vez de um corte no primeiro `[` — o primeiro `[` pode
    estar no NOME DO MODULO, e cortar ali devolveria `module.portal`, que nao casa
    com nada e deixaria a cerca cega exatamente no caso que ela deveria pegar.
    """
    sem_indices = _INDICE.sub("", endereco)
    partes = sem_indices.split(".")
    return ".".join(partes[-2:]) if len(partes) >= 2 else sem_indices


def destruicoes(plano: dict) -> list[tuple[str, list[str]]]:
    achados = []
    for mudanca in plano.get("resource_changes") or []:
        acoes = list((mudanca.get("change") or {}).get("actions") or [])
        if not ACOES_QUE_DESTROEM.intersection(acoes):
            continue
        if _endereco_base(mudanca.get("address", "")) in DURAVEIS:
            achados.append((mudanca["address"], acoes))
    return achados


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plano", type=Path, help="saida de `terraform show -json <plano>`")
    ap.add_argument(
        "--permitir-destruir",
        action="store_true",
        help="destruir o portal DE PROPOSITO; escreva o porque no registro da mudanca.",
    )
    args = ap.parse_args(argv)

    try:
        plano = json.loads(args.plano.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        # Plano ilegivel nao e' "nenhum destroy": e' nao saber. Reprova.
        print(f"ERRO: nao consegui ler o plano em {args.plano}: {erro}", file=sys.stderr)
        return 2

    achados = destruicoes(plano)
    if not achados:
        print("OK: o plano nao destroi nenhum recurso duravel do portal.")
        return 0

    print(f"O plano destroi {len(achados)} recurso(s) duravel(eis) do portal:", file=sys.stderr)
    for endereco, acoes in achados:
        print(f"  - {endereco}  ({'+'.join(acoes)})", file=sys.stderr)
    if args.permitir_destruir:
        print("\n--permitir-destruir foi passado: seguindo assim mesmo.", file=sys.stderr)
        return 0
    print(
        "\nAntes de qualquer outra coisa, confira se `portal.auto.tfvars` esta' no checkout:\n"
        "  ls deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars\n"
        "Sem ele `var.portal` e' null e o Terraform planeja apagar o portal inteiro — e' de longe\n"
        "a causa mais provavel deste plano. Se a destruicao for mesmo intencional, repita com\n"
        "--permitir-destruir.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
