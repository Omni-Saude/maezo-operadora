#!/usr/bin/env python3
"""Cerca de CI da decisao N5: `engine-rest` sem autenticacao e' aceito SO' em dev.

Por que esta cerca existe
--------------------------
O engine CIB Seven de dev expoe o `engine-rest` na 8080 sem autenticacao (medido:
`GET /user` sem credencial devolve 200; `deploy/cibseven/Dockerfile`). Quem alcanca a porta dirige
o motor. Em dev isso e' aceito porque a fronteira e' o Security Group e o Cloudflare Access, e
porque exigir credencial atinge worker, agentes e canal (o pacote C, ainda pendente). O plano de
autoridade nativa (`docs/plans/portal-autoridade-nativa-dev.md`, D-A) mantem isso na Onda 4: o
plugin humano entra no MESMO engine, e a divida D7 (bypass pelo REST) continua aberta em dev.

O dono decidiu em 23/09/2026 (plano §6, N5): aceito SO' em dev, com uma cerca de CI que reprova
staging e producao (T1.9). Esta e' a cerca.

Por que ela e' declarativa
---------------------------
Pelo digest da imagem nao da' para saber se o engine e' o aberto (`Dockerfile`, `Dockerfile.human`)
ou o `secured*` (engine-rest por certificado, sem fallback anonimo). Entao cada ambiente que nao e'
dev e que sobe um engine tem de DIZER como o `engine-rest` autentica, num lugar que a revisao le':

    engine_rest_authentication = "client-certificate"

A variavel do modulo nasce quando o primeiro ambiente nao-dev nascer; ate' la', a cerca e' o que
impede copiar o dev para staging e subir o engine aberto. Ela nao prova que a imagem cumpre o que
a declaracao diz — prova que ninguem subiu um engine fora de dev sem assumir essa obrigacao.

O que ela reprova
------------------
  1. `deploy/aws-ecs/envs/<nome>/` fora de dev sem a declaracao. Todo ambiente ECS conta: e' o
     formato do dev, que sobe o engine.
  2. `deploy/**/envs/<nome>/` (Terraform) fora de dev que menciona `cibseven`/`engine-rest` fora
     de comentario, sem a declaracao.
  3. Declaracao com outro valor, com valor que a cerca nao resolve (funcao, `var.` sem default,
     interpolacao), ou `variable` sem default e sem `terraform.tfvars`/`*.auto.tfvars`. Indirecao
     nao resolvivel e' ACHADO, nao silencio. TODA declaracao conta, nao so' a efetiva: nome
     repetido nao e' erro para o Terraform e a ultima vence (ja' custou um FHIR_BASE_URL errado).
  4. A mencao do nome em forma que a cerca nao avalia (dentro de string, `lookup(...)`).
  5. Helm (`deploy/helm/<chart>/values*.yaml`, efetivo = base + overlay): fora de dev,
     `cibseven.inCluster.enabled` ligado sem `cibseven.inCluster.engineRestAuthentication:
     client-certificate`, ou com a imagem oficial `cibseven/cibseven` (aberta por construcao), ou
     `enabled` que nao e' booleano.
  6. Cerca cega: nenhum ambiente encontrado, ou o dev conhecido (`dev-sa-east-1`) ausente.

Dev e' uma ALLOWLIST EXATA de caminhos (`AMBIENTES_DEV`), nunca um padrao: `dev-*` isentaria
`dev-prod` ou `dev-us-east-1` amanha sem ninguem decidir isso (revisao de seguranca do PR #482).
Um ambiente dev novo entra aqui por PR, com a justificativa. O Helm nao tem overlay dev hoje, entao
todo `values*.yaml` e' avaliado como nao-dev (`OVERLAYS_HELM_DEV` vazio).

O analisador de HCL e' o de `check_canal_simular.py` (endurecido pela RV-371): comentarios e
heredocs saem, `local.`/`var.` sao resolvidos como o Terraform resolveria.

Fora do escopo, de proposito: manifests kustomize e o repositorio `gitops` (k3s/Argo) nao sao
lidos por esta cerca. Este repositorio nao entrega o engine por eles hoje; se passar a entregar,
a cerca tem de ser estendida no mesmo PR, e ate' la' eles nao estao cobertos.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from scripts.ci.check_canal_simular import (  # noqa: E402
    _atributos,
    _escopo_do_diretorio,
    _fecha,
    _fim_expressao,
    _linha,
    _mascarar,
)

ATRIBUTO = "engine_rest_authentication"
VALOR_EXIGIDO = "client-certificate"
#: O ambiente dev que existe hoje. Se ele sumir, a cerca nao sabe se esta' olhando o lugar certo.
AMBIENTE_DEV_CONHECIDO = "dev-sa-east-1"
#: Os UNICOS diretorios de ambiente onde o engine-rest aberto e' aceito (N5). Caminho exato,
#: relativo a raiz, com o motivo. Qualquer outro diretorio de `envs/` e' nao-dev.
AMBIENTES_DEV: dict[str, str] = {
    f"deploy/aws-ecs/envs/{AMBIENTE_DEV_CONHECIDO}": "o engine de dev (servico cibseven, ECS).",
    "deploy/cloudflare/envs/dev": "a borda Cloudflare do MESMO dev (tunel para o engine de dev).",
}
#: Overlays Helm dev (`deploy/helm/<chart>/values-<x>.yaml`, caminho exato). Nenhum hoje.
OVERLAYS_HELM_DEV: frozenset[str] = frozenset()
CHAVE_HELM = "engineRestAuthentication"
#: A imagem oficial nao autentica o engine-rest (deploy/cibseven/Dockerfile, "O QUE ISTO NAO
#: RESOLVE"). Declarar `client-certificate` com ela seria declarar o que nao existe.
IMAGEM_OFICIAL_ABERTA = "cibseven/cibseven"

JUSTIFICATIVA = (
    "o engine-rest sem autenticacao e' aceito SO' em dev (decisao N5, plano "
    "portal-autoridade-nativa-dev §6): quem alcanca a porta dirige o motor."
)

_SOBE_ENGINE = re.compile(r"cibseven|engine-rest", re.IGNORECASE)
_ATRIBUICAO = re.compile(rf"(?<![\w.]){ATRIBUTO}\s*=(?!=)")
_VARIAVEL = re.compile(rf'\bvariable\s+"{ATRIBUTO}"\s*\{{')
_REFERENCIA = re.compile(rf"\b(?:var|local)\.{ATRIBUTO}\b")
_NOME = re.compile(rf"(?<![\w]){ATRIBUTO}(?![\w])")


def e_dev(caminho_relativo: str) -> bool:
    """Igualdade exata contra a allowlist; nunca prefixo nem padrao."""
    return caminho_relativo in AMBIENTES_DEV


@dataclass(frozen=True)
class Declaracao:
    onde: str
    expressao: str
    valor: str | None


# ---------------------------------------------------------------------------------------------
# Terraform.
# ---------------------------------------------------------------------------------------------
def _ambientes(raiz: Path) -> list[Path]:
    deploy = raiz / "deploy"
    if not deploy.is_dir():
        return []
    achados: set[Path] = set()
    for envs in deploy.rglob("envs"):
        relativo = envs.relative_to(raiz)
        if not envs.is_dir() or any(p.startswith(".") for p in relativo.parts):
            continue
        for filho in envs.iterdir():
            if filho.is_dir() and not filho.name.startswith(".") and _arquivos_hcl(filho):
                achados.add(filho)
    return sorted(achados)


def _tfvars_carregado(arquivo: Path) -> bool:
    """Os unicos tfvars que o Terraform le' sem `-var-file`: o resto (`.example`) nao conta."""
    return arquivo.name == "terraform.tfvars" or arquivo.name.endswith(".auto.tfvars")


def _arquivos_hcl(diretorio: Path) -> list[Path]:
    return sorted(
        p for p in diretorio.iterdir() if p.is_file() and (p.suffix == ".tf" or _tfvars_carregado(p))
    )


def _avaliar_ambiente(raiz: Path, env: Path) -> list[str]:
    nome = env.name
    rotulo = env.relative_to(raiz).as_posix()
    arquivos = _arquivos_hcl(env)
    escopo = _escopo_do_diretorio(env)
    textos = {a: _mascarar(a.read_text(encoding="utf-8", errors="replace")) for a in arquivos}

    # tfvars sobrepoe o default da variable, como no Terraform.
    for arquivo, (texto, e_string) in textos.items():
        if arquivo.suffix != ".tf":
            escopo.variaveis.update(_atributos(texto, e_string, 0, len(texto)))

    ecs = env.parent.parent.name == "aws-ecs"
    sobe_engine = ecs or any(_SOBE_ENGINE.search(texto) for texto, _ in textos.values())
    if e_dev(rotulo) or not sobe_engine:
        return []

    achados: list[str] = []
    declaracoes: list[Declaracao] = []
    for arquivo, (texto, e_string) in textos.items():
        relativo = arquivo.relative_to(raiz).as_posix()
        contabilizadas: set[int] = set()
        for casamento in _ATRIBUICAO.finditer(texto):
            if e_string[casamento.start()]:
                continue
            fim = _fim_expressao(texto, e_string, casamento.end(), len(texto))
            expressao = texto[casamento.end() : fim].strip()
            declaracoes.append(
                Declaracao(
                    f"{relativo}:{_linha(texto, casamento.start())}", expressao, escopo.resolver(expressao)
                )
            )
            contabilizadas.add(casamento.start())
        for casamento in _REFERENCIA.finditer(texto):
            if not e_string[casamento.start()]:
                contabilizadas.add(casamento.end() - len(ATRIBUTO))
        for casamento in _VARIAVEL.finditer(texto):
            if e_string[casamento.start()]:
                continue
            contabilizadas.add(texto.index(ATRIBUTO, casamento.start()))
            fim = _fecha(texto, e_string, casamento.end() - 1, "{", "}")
            corpo = _atributos(texto, e_string, casamento.end(), fim - 1) if fim > 0 else {}
            onde = f"{relativo}:{_linha(texto, casamento.start())}"
            if "default" in corpo:
                declaracoes.append(Declaracao(onde, corpo["default"], escopo.resolver(corpo["default"])))
            if ATRIBUTO not in escopo.variaveis:
                achados.append(
                    f"{onde}: variable {ATRIBUTO!r} sem default e sem valor em terraform.tfvars/"
                    f"*.auto.tfvars — um valor escolhido no `apply` e' invisivel para a revisao. "
                    f"({JUSTIFICATIVA})"
                )
        for casamento in _NOME.finditer(texto):
            if casamento.start() not in contabilizadas:
                achados.append(
                    f"{relativo}:{_linha(texto, casamento.start())}: {ATRIBUTO} aparece em forma que "
                    f"esta cerca nao consegue avaliar (string, funcao, objeto) — reprovado por "
                    f'precaucao; declare `{ATRIBUTO} = "{VALOR_EXIGIDO}"`.'
                )

    if not declaracoes:
        achados.append(
            f"{rotulo}: ambiente {nome!r} nao e' dev e sobe um engine, mas nao declara "
            f'`{ATRIBUTO} = "{VALOR_EXIGIDO}"`. Copiar o dev para outro ambiente sobe o engine-rest '
            f"ABERTO. ({JUSTIFICATIVA})"
        )
    for declaracao in declaracoes:
        if declaracao.valor is None:
            achados.append(
                f"{declaracao.onde}: {ATRIBUTO} = {declaracao.expressao!r} nao e' um valor que a "
                f"cerca consiga resolver — indirecao nao resolvivel e' REPROVACAO, nao silencio. "
                f"({JUSTIFICATIVA})"
            )
        elif declaracao.valor != VALOR_EXIGIDO:
            achados.append(
                f"{declaracao.onde}: {ATRIBUTO} = {declaracao.valor!r} no ambiente {nome!r}; fora "
                f"de dev o unico valor aceito e' {VALOR_EXIGIDO!r}. ({JUSTIFICATIVA})"
            )
    return achados


def checar_terraform(raiz: Path = REPO_ROOT) -> list[str]:
    ambientes = _ambientes(raiz)
    if not ambientes:
        return ["nenhum ambiente encontrado em deploy/**/envs/ — a cerca esta' cega, nao aprovada."]
    achados: list[str] = []
    ecs = {e.name for e in ambientes if e.parent.parent.name == "aws-ecs"}
    if AMBIENTE_DEV_CONHECIDO not in ecs:
        achados.append(
            f"deploy/aws-ecs/envs/{AMBIENTE_DEV_CONHECIDO} nao encontrado — o ambiente dev que esta "
            f"cerca conhece sumiu; atualize AMBIENTE_DEV_CONHECIDO junto com a mudanca."
        )
    for env in ambientes:
        achados.extend(_avaliar_ambiente(raiz, env))
    return achados


# ---------------------------------------------------------------------------------------------
# Helm.
# ---------------------------------------------------------------------------------------------
def _mesclar(base: Any, overlay: Any) -> Any:
    """Semantica de values do Helm: mapas se mesclam, o resto e' substituido, `null` apaga."""
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay
    saida = dict(base)
    for chave, valor in overlay.items():
        if valor is None:
            saida.pop(chave, None)
        else:
            saida[chave] = _mesclar(base.get(chave), valor)
    return saida


def _imagem_oficial(repositorio: object) -> bool:
    if not isinstance(repositorio, str):
        return True  # nao resolvivel: fail-closed
    nome = repositorio.strip().lower()
    for prefixo in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        nome = nome.removeprefix(prefixo)
    return nome in (IMAGEM_OFICIAL_ABERTA, "library/" + IMAGEM_OFICIAL_ABERTA)


def _avaliar_values(rotulo: str, valores: Any) -> list[str]:
    if rotulo in OVERLAYS_HELM_DEV:
        return []
    cib = valores.get("cibseven") if isinstance(valores, dict) else None
    in_cluster = cib.get("inCluster") if isinstance(cib, dict) else None
    if in_cluster is None:
        return []
    if not isinstance(in_cluster, dict):
        return [f"{rotulo}: cibseven.inCluster nao e' um mapa — a cerca nao consegue avaliar."]
    ligado = in_cluster.get("enabled", False)
    if not isinstance(ligado, bool):
        return [
            f"{rotulo}: cibseven.inCluster.enabled = {ligado!r} nao e' booleano — a cerca nao "
            f"consegue provar que o engine in-cluster esta' desligado. ({JUSTIFICATIVA})"
        ]
    if not ligado:
        return []
    achados: list[str] = []
    if in_cluster.get(CHAVE_HELM) != VALOR_EXIGIDO:
        achados.append(
            f"{rotulo}: engine in-cluster ligado fora de dev sem cibseven.inCluster.{CHAVE_HELM}: "
            f"{VALOR_EXIGIDO} (valor: {in_cluster.get(CHAVE_HELM)!r}). ({JUSTIFICATIVA})"
        )
    imagem = in_cluster.get("image")
    repositorio = imagem.get("repository") if isinstance(imagem, dict) else None
    if _imagem_oficial(repositorio):
        achados.append(
            f"{rotulo}: engine in-cluster fora de dev com a imagem {repositorio!r}; a imagem "
            f"oficial {IMAGEM_OFICIAL_ABERTA} nao autentica o engine-rest. ({JUSTIFICATIVA})"
        )
    return achados


def checar_helm(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    helm = raiz / "deploy" / "helm"
    charts = sorted(p.parent for p in helm.glob("*/values.yaml")) if helm.is_dir() else []
    for chart in charts:
        try:
            base = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as erro:
            achados.append(f"{(chart / 'values.yaml').relative_to(raiz).as_posix()}: YAML invalido ({erro}).")
            continue
        achados.extend(_avaliar_values((chart / "values.yaml").relative_to(raiz).as_posix(), base))
        for overlay in sorted(chart.glob("values-*.yaml")):
            rotulo = overlay.relative_to(raiz).as_posix()
            try:
                valores = yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as erro:
                achados.append(f"{rotulo}: YAML invalido ({erro}).")
                continue
            achados.extend(_avaliar_values(rotulo, _mesclar(base, valores)))
    return achados


def executar(raiz: Path = REPO_ROOT) -> list[str]:
    return checar_terraform(raiz) + checar_helm(raiz)


def main() -> int:
    achados = executar(REPO_ROOT)
    if achados:
        print("check_engine_rest_auth: REPROVADO", file=sys.stderr)
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        return 1
    print(
        "check_engine_rest_auth: ok — engine-rest sem autenticacao so' em dev; todo ambiente "
        f'fora de dev que sobe um engine declara {ATRIBUTO} = "{VALOR_EXIGIDO}".'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
