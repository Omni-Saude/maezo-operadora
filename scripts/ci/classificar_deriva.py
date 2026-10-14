"""Classifica um plano do Terraform do dev em RUIDO CONHECIDO ou DERIVA REAL.

    terraform plan -lock=false -detailed-exitcode -out=tfplan
    terraform show -json tfplan > plano.json
    python scripts/ci/classificar_deriva.py plano.json --markdown resumo.md

POR QUE EXISTE. O dev foi aplicado muitas vezes a partir de maquinas e arquivos de
variaveis avulsos; `dev-vivo.auto.tfvars` passou a registrar no git o que esta' vivo, e
um plano feito do git ficou proximo do estado real. O que sobra e' ruido do provider
(o ECS normaliza a task definition e o provider 6.59 a ve diferente do que registrou),
seus seguidores (servico, schedule e policy que apontam para a task definition
recriada) e a assinatura SNS que espera confirmacao por e-mail. Este script separa esse
ruido conhecido do que importa: qualquer outra diferenca entre o git e a AWS e' DERIVA.

CODIGOS DE SAIDA.
    0  nada alem de ruido conhecido (inclui plano vazio)
    1  ha deriva real
    2  plano ilegivel ou com formato inesperado (nao saber nao e' estar limpo)

O QUE ELE IMPRIME. So' ENDERECOS DE RECURSO e TIPOS DE ACAO. Nenhum valor de atributo,
nenhum nome de atributo, nenhum trecho de container_definitions: o resumo vai para o
sumario do workflow e para uma issue, e plano de infraestrutura carrega segredo com
facilidade (variaveis de ambiente, ARNs, digests). Os valores so' sao LIDOS aqui para
comparar `before` com `after`; nunca sao escritos.

COMO ACRESCENTAR RUIDO. Ver "Verificacao de deriva do dev" em `docs/runbooks/aws-ecs.md`.
Em resumo: uma regra nova aqui, com a justificativa em comentario, um teste que prova
que a regra NAO engole uma mudanca real, e a linha da regra na tabela do runbook.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Um indice de recurso ou de modulo: `["this"]`, `[0]`.
_INDICE = re.compile(r"\[[^\]]*\]")

TIPO_TASK_DEFINITION = "aws_ecs_task_definition"
TIPO_SERVICO = "aws_ecs_service"

#: Caminhos (indices de lista viram `*`) que o provider deixa "known after apply" quando
#: SUBSTITUI uma task definition e que, medidos no dev, nao carregam mudanca nenhuma:
#: os identificadores calculados a cada registro, `enable_fault_injection` (false vivo,
#: nulo no codigo) e `volume[].configure_at_launch` (false vivo, desconhecido no plano).
#: Qualquer OUTRO caminho desconhecido (um ARN de role que sera' recriada, por exemplo)
#: e' mudanca real e reprova.
_DESCONHECIDOS_DA_TASK_DEFINITION = frozenset(
    {
        "arn",
        "arn_without_revision",
        "id",
        "revision",
        "enable_fault_injection",
        "volume.*.configure_at_launch",
    }
)

#: Atributos de texto que o ECS devolve como "" onde o codigo nao declara nada (nulo).
#: "" e nulo sao a mesma coisa; um valor NAO vazio e' mudanca real.
_VAZIO_EQUIVALE_A_NULO = ("ipc_mode", "pid_mode", "task_role_arn")

#: Listas que o ECS devolve como `[]` onde o codigo nao declara nada. `[]` e ausente sao
#: iguais; lista NAO vazia e' mudanca real.
_LISTAS_DO_CONTAINER_VAZIA_EQUIVALE_A_AUSENTE = (
    "mountPoints",
    "portMappings",
    "systemControls",
    "volumesFrom",
)

#: Recursos que SEGUEM uma task definition recriada: so' o atributo que carrega o ARN da
#: task definition pode ficar desconhecido, e nada conhecido pode ter mudado. So' valem
#: se o mesmo plano recria alguma task definition por ruido (ver `classificar`).
_SEGUIDORES = {
    TIPO_SERVICO: frozenset({"task_definition"}),
    "aws_scheduler_schedule.staff_job": frozenset({"target.*.ecs_parameters.*.task_definition_arn"}),
    "aws_iam_role_policy.staff_job_scheduler": frozenset({"policy"}),
}

#: Assinaturas SNS que ficam pendentes de confirmacao por e-mail: o Terraform ve a
#: assinatura "nao criada" ate alguem clicar no link. Endereco sem indices.
ASSINATURAS_PENDENTES = frozenset({"aws_sns_topic_subscription.staff_alerts_email"})

_SUBSTITUICAO = (("delete", "create"), ("create", "delete"))


@dataclass(frozen=True)
class Mudanca:
    endereco: str
    acoes: tuple[str, ...]
    motivo: str = ""

    @property
    def acao(self) -> str:
        return "+".join(self.acoes)


@dataclass
class Classificacao:
    ruido: list[Mudanca] = field(default_factory=list)
    deriva: list[Mudanca] = field(default_factory=list)

    @property
    def limpo(self) -> bool:
        return not self.deriva


class PlanoInvalido(ValueError):  # noqa: N818 - nome em portugues, como o resto do repo
    """O JSON nao tem a forma de um `terraform show -json`."""


def endereco_base(endereco: str) -> str:
    """`aws_sns_topic_subscription.x["a"]` -> `aws_sns_topic_subscription.x`.

    Remove indices por regex (o primeiro `[` pode estar no nome de um modulo) e fica
    com os dois ultimos segmentos, `tipo.nome`.
    """
    partes = _INDICE.sub("", endereco).split(".")
    return ".".join(partes[-2:]) if len(partes) >= 2 else partes[0]


# --------------------------------------------------------------------------------------
# Comparacao de antes/depois respeitando o que ainda e' desconhecido
# --------------------------------------------------------------------------------------


def _comparar(
    antes: Any, depois: Any, desconhecido: Any, caminho: str, diferencas: list[str], desconhecidos: list[str]
) -> None:
    """Percorre `before`/`after` junto com `after_unknown`.

    Folha desconhecida entra em `desconhecidos` (o valor so' existira' no apply); folha
    conhecida e diferente entra em `diferencas`. So' os CAMINHOS sao guardados, nunca valores.
    """
    if desconhecido is True:
        desconhecidos.append(caminho)
        return
    if isinstance(depois, dict) or isinstance(antes, dict):
        d = depois if isinstance(depois, dict) else {}
        a = antes if isinstance(antes, dict) else {}
        u = desconhecido if isinstance(desconhecido, dict) else {}
        if type(antes) is not type(depois) and antes is not None and depois is not None:
            diferencas.append(caminho)
            return
        for chave in set(a) | set(d) | set(u):
            _comparar(
                a.get(chave),
                d.get(chave),
                u.get(chave, False),
                f"{caminho}.{chave}" if caminho else chave,
                diferencas,
                desconhecidos,
            )
        return
    if isinstance(depois, list) or isinstance(antes, list):
        lista_d = depois if isinstance(depois, list) else []
        lista_a = antes if isinstance(antes, list) else []
        lista_u = desconhecido if isinstance(desconhecido, list) else []
        if len(lista_a) != len(lista_d) or (
            antes is not None and depois is not None and type(antes) is not type(depois)
        ):
            diferencas.append(caminho)
            return
        for i in range(len(lista_d)):
            _comparar(
                lista_a[i],
                lista_d[i],
                lista_u[i] if i < len(lista_u) else False,
                f"{caminho}.*",
                diferencas,
                desconhecidos,
            )
        return
    if antes != depois:
        diferencas.append(caminho)


# --------------------------------------------------------------------------------------
# Normalizacao de task definition
# --------------------------------------------------------------------------------------


def _lf(valor: Any) -> Any:
    """CRLF -> LF em strings (e em listas de strings, como `command`)."""
    if isinstance(valor, str):
        return valor.replace("\r\n", "\n")
    if isinstance(valor, list):
        return [_lf(v) for v in valor]
    return valor


def normalizar_container(container: dict[str, Any]) -> dict[str, Any]:
    """Aplica as normalizacoes do ECS que o provider 6.59 enxerga como diferenca.

    1. `portMappings[].hostPort`: o ECS preenche com a porta do container (awsvpc), o
       codigo nao declara.
    2. `mountPoints`, `portMappings`, `systemControls`, `volumesFrom` como `[]`: o ECS
       devolve vazio onde o codigo nao declara nada. Lista NAO vazia fica.
    3. `linuxParameters.capabilities.add`: idem, `[]` e ausente sao iguais; `add` NAO
       vazio e' mudanca real e fica.
    4. `command`: CRLF no codigo (arquivo salvo no Windows) vs LF no que esta' vivo.
    5. `environment`: o provider devolve em ordem alfabetica, o codigo na ordem em que
       foi escrito. E' um conjunto nome->valor; so' a ORDEM e' ignorada.
    """
    saida = dict(container)

    mapeamentos = saida.get("portMappings")
    if isinstance(mapeamentos, list):
        saida["portMappings"] = [
            {k: v for k, v in pm.items() if k != "hostPort"} if isinstance(pm, dict) else pm
            for pm in mapeamentos
        ]

    for chave in _LISTAS_DO_CONTAINER_VAZIA_EQUIVALE_A_AUSENTE:
        if saida.get(chave) == []:
            del saida[chave]

    linux = saida.get("linuxParameters")
    if isinstance(linux, dict):
        linux = dict(linux)
        caps = linux.get("capabilities")
        if isinstance(caps, dict):
            caps = dict(caps)
            if caps.get("add") == []:
                del caps["add"]
            if caps:
                linux["capabilities"] = caps
            else:
                del linux["capabilities"]
        if linux:
            saida["linuxParameters"] = linux
        else:
            del saida["linuxParameters"]

    if "command" in saida:
        saida["command"] = _lf(saida["command"])

    ambiente = saida.get("environment")
    if isinstance(ambiente, list) and all(isinstance(e, dict) for e in ambiente):
        saida["environment"] = sorted(ambiente, key=lambda e: str(e.get("name")))

    return saida


def _containers(texto: Any) -> Any:
    """Decodifica `container_definitions` (string JSON) e normaliza cada container."""
    if not isinstance(texto, str):
        return texto
    try:
        lista = json.loads(texto)
    except json.JSONDecodeError:
        return texto  # nao decodifica: compara como texto, e vai divergir
    if not isinstance(lista, list):
        return lista
    return [normalizar_container(c) if isinstance(c, dict) else c for c in lista]


def _normalizar_task_definition(atributos: dict[str, Any]) -> dict[str, Any]:
    saida = dict(atributos)
    if "container_definitions" in saida:
        saida["container_definitions"] = _containers(saida["container_definitions"])
    for chave in _VAZIO_EQUIVALE_A_NULO:
        if saida.get(chave) == "":
            saida[chave] = None
    return saida


def _equivalente_task_definition(
    antes: dict[str, Any], depois: dict[str, Any], desconhecido: dict[str, Any]
) -> bool:
    """True se so' difere pelas normalizacoes conhecidas."""
    diferencas: list[str] = []
    desconhecidos: list[str] = []
    _comparar(
        _normalizar_task_definition(antes),
        _normalizar_task_definition(depois),
        desconhecido,
        "",
        diferencas,
        desconhecidos,
    )
    if any(c not in _DESCONHECIDOS_DA_TASK_DEFINITION for c in desconhecidos):
        return False
    # Os caminhos desconhecidos aceitos nao entram na comparacao (o `_comparar` ja os
    # separou); `enable_fault_injection` e `volume[].configure_at_launch` sao exatamente
    # eles, entao nao sobra diferenca conhecida a ignorar.
    return not diferencas


def _segue_task_definition(endereco_base_: str, tipo: str, change: dict[str, Any]) -> bool:
    """Update de recurso que so' passa a apontar para a task definition recriada."""
    permitidos = _SEGUIDORES.get(endereco_base_) or _SEGUIDORES.get(tipo)
    if not permitidos:
        return False
    diferencas: list[str] = []
    desconhecidos: list[str] = []
    _comparar(
        change.get("before") or {},
        change.get("after") or {},
        change.get("after_unknown") or {},
        "",
        diferencas,
        desconhecidos,
    )
    return bool(desconhecidos) and not diferencas and all(c in permitidos for c in desconhecidos)


# --------------------------------------------------------------------------------------
# Classificacao
# --------------------------------------------------------------------------------------


def _eh_ruido_independente(mudanca: dict[str, Any]) -> tuple[bool, str]:
    change = mudanca.get("change") or {}
    acoes = tuple(change.get("actions") or ())
    tipo = mudanca.get("type") or ""
    base = endereco_base(mudanca.get("address", ""))

    if base in ASSINATURAS_PENDENTES and acoes == ("create",):
        return True, "assinatura SNS pendente de confirmacao por e-mail"

    if (
        tipo == TIPO_TASK_DEFINITION
        and acoes in _SUBSTITUICAO
        and _equivalente_task_definition(
            change.get("before") or {}, change.get("after") or {}, change.get("after_unknown") or {}
        )
    ):
        return True, "task definition recriada so' por normalizacao do ECS"
    return False, ""


def classificar(plano: dict[str, Any]) -> Classificacao:
    mudancas = plano.get("resource_changes")
    if mudancas is None:
        # `terraform show -json` de plano vazio omite a chave; um objeto que nem
        # `format_version` tem nao e' plano.
        if "format_version" not in plano:
            raise PlanoInvalido("sem `format_version` nem `resource_changes`: nao parece um plano")
        mudancas = []
    if not isinstance(mudancas, list):
        raise PlanoInvalido("`resource_changes` nao e' uma lista")

    resultado = Classificacao()
    pendentes: list[dict[str, Any]] = []
    for item in mudancas:
        if not isinstance(item, dict):
            raise PlanoInvalido("item de `resource_changes` nao e' um objeto")
        acoes = tuple((item.get("change") or {}).get("actions") or ())
        if not acoes or set(acoes) <= {"no-op", "read"}:
            continue
        ruido, motivo = _eh_ruido_independente(item)
        if ruido:
            resultado.ruido.append(Mudanca(item.get("address", "?"), acoes, motivo))
        else:
            pendentes.append(item)

    # Seguidores so' contam como ruido se o plano recria alguma task definition por ruido:
    # um `task_definition` desconhecido sem isso nao tem explicacao conhecida.
    ha_task_definition_ruidosa = any(m.motivo.startswith("task definition") for m in resultado.ruido)
    for item in pendentes:
        change = item.get("change") or {}
        acoes = tuple(change.get("actions") or ())
        base = endereco_base(item.get("address", ""))
        if (
            ha_task_definition_ruidosa
            and acoes == ("update",)
            and _segue_task_definition(base, item.get("type") or "", change)
        ):
            resultado.ruido.append(
                Mudanca(item.get("address", "?"), acoes, "segue a task definition recriada por normalizacao")
            )
        else:
            resultado.deriva.append(Mudanca(item.get("address", "?"), acoes))
    return resultado


# --------------------------------------------------------------------------------------
# Saida
# --------------------------------------------------------------------------------------


def _tabela(mudancas: list[Mudanca], com_motivo: bool) -> list[str]:
    cabecalho = "| Recurso | Ação | Motivo |" if com_motivo else "| Recurso | Ação |"
    separador = "|---|---|---|" if com_motivo else "|---|---|"
    linhas = [cabecalho, separador]
    for m in sorted(mudancas, key=lambda x: x.endereco):
        celula = f"| `{m.endereco}` | {m.acao} |"
        linhas.append(celula + (f" {m.motivo} |" if com_motivo else ""))
    return linhas


def resumo_markdown(resultado: Classificacao) -> str:
    if resultado.limpo:
        titulo = "## Deriva do dev: nenhuma"
        corpo = ["O Terraform no git e o ambiente vivo coincidem, afora o ruído conhecido."]
    else:
        titulo = f"## Deriva do dev: {len(resultado.deriva)} mudança(s) fora do ruído conhecido"
        corpo = [
            "O plano feito do git contra a AWS propõe mudanças que NÃO são ruído conhecido.",
            "Ou alguém mexeu na AWS sem passar pelo git, ou o git mudou sem apply.",
            "",
            *_tabela(resultado.deriva, com_motivo=False),
        ]
    saida = [titulo, "", *corpo]
    if resultado.ruido:
        saida += [
            "",
            f"<details><summary>Ruído conhecido ignorado ({len(resultado.ruido)})</summary>",
            "",
            *_tabela(resultado.ruido, com_motivo=True),
            "",
            "</details>",
        ]
    saida += [
        "",
        "_Só endereços e tipos de ação são mostrados; nenhum valor. Como ler e como "
        'acrescentar ruído: `docs/runbooks/aws-ecs.md`, seção "Verificação de deriva do dev"._',
    ]
    return "\n".join(saida) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plano", type=Path, help="saida de `terraform show -json <plano>`")
    ap.add_argument("--markdown", type=Path, help="onde gravar o resumo em Markdown")
    args = ap.parse_args(argv)

    try:
        plano = json.loads(args.plano.read_text(encoding="utf-8"))
        if not isinstance(plano, dict):
            raise PlanoInvalido("a raiz do JSON nao e' um objeto")
        resultado = classificar(plano)
    except (OSError, json.JSONDecodeError, PlanoInvalido) as erro:
        # So' o tipo do erro: nunca o texto, que poderia citar conteudo do plano.
        print(
            f"ERRO: plano ilegivel ({type(erro).__name__}); nao da' para afirmar que esta' limpo.",
            file=sys.stderr,
        )
        return 2

    markdown = resumo_markdown(resultado)
    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8", newline="\n")
    sys.stdout.buffer.write(markdown.encode("utf-8"))
    return 0 if resultado.limpo else 1


if __name__ == "__main__":
    raise SystemExit(main())
