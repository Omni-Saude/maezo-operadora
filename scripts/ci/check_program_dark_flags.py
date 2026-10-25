#!/usr/bin/env python3
"""Cerca de CI: as flags dark do Programa de Completude (W5 CLNT) seguem provadamente OFF.

Por que esta cerca existe
--------------------------
`docs/plans/maezo-completion-program.md` §W5 5.1 tem, como criterio de aceitacao,
"flags provadamente OFF em prod". As flags dark registradas nesta cerca foram construidas
dark (#955 canal DSR, #960/#963 paineis staff, #965 pontes de erasure; D-36 do dono,
25/10/2026, a superficie interna de estruturas de ajuste de comissao) e a SENTENCA que cada
docstring delas declara e' sempre a mesma: virar a chave e ato da onda ROLL (ou, na D-36,
ato proprio do dono pos-ratificacao A8 Forma 1) e **nenhum `deploy/**` pode liga-la neste
programa** (`src/maezo/portal/api/config.py:51,60,70,83`;
`src/maezo/platform/identity_bridges.py:24`). Ate este pacote essa proibicao era so' prosa:
a cerca de DL-0049 (`check_portal_direct_completion.py`) cobre SOMENTE
`MAEZO_PORTAL_DIRECT_COMPLETION`/`MAEZO_PORTAL_CORS_ORIGINS`, e a reconciliacao de env
(`check_chart_env_reconciliation.py`) confere declared-vs-read, NUNCA a ausencia de uma flag.

O que ela reprova
------------------
  1. O default deixando de ser `False` literal no codigo para qualquer uma das flags
     registradas de `PortalSettings` — um default ligado abre a superficie em todo ambiente que NAO a
     declara, o contrario de dark.
  2. A flag deixando de existir (exatamente uma vez) na classe — cerca cega reprovada, nao
     silenciada.
  3. O `env_prefix` de `PortalSettings` deixando de ser o literal `"MAEZO_PORTAL_"` — os
     nomes de env pinados abaixo so' significam o que dizem sob esse prefixo.
  4. O nome de env de qualquer uma das flags registradas (quatro do portal + as DUAS
     pontes de erasure) aparecendo em QUALQUER arquivo de `deploy/**` — um template nao tem ambiente
     provavel, e a sentenca de cada flag e' "nenhum `deploy/**` pode liga-la": mencao nenhuma
     e' aceitavel neste programa, nem comentada, nem "desligada" (o mesmo padrao de
     `_fora_do_terraform` da cerca de DL-0049).
  5. As constantes `TITULAR_BRIDGE_FLAG_ENV`/`THREAD_BRIDGE_FLAG_ENV` deixando de valer os
     nomes pinados — renomear a flag no codigo sem renomear a cerca deixa-a apontando para
     um nome que ninguem mais declara (e vice-versa).
  6. `deploy/**` inexistente ou vazio no checkout — varrer nada nao e' prova.

O que ela NAO faz
------------------
Nao flipa flag, nao decide se/quando a ROLL habilita, nao cobre as flags dark de postura
PROPRIA anterior ao programa (Helena/Lucas por DL do dono; `denial_notice_owner`,
`document_request_host_installed`, `agent_ingress_enabled`, lembretes — algumas declaradas
em `deploy/**` dev-sa-east-1 POR DECISAO, ver
`docs/plans/w5-5-1-flags-reancoragem.md` §3) e nao substitui os testes de comportamento
fail-closed de cada flag (`test_dsr_endpoint.py`, `test_human_session.py`,
`test_identity_bridges.py`) — cobra o perimetro que as docstrings shipped assumiram.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    # Explicit, idempotent insert so the dotted import below resolves the same whether this
    # module is run directly or imported by pytest (`pythonpath = ["."]` covers it there).
    sys.path.insert(0, str(REPO_ROOT))

#: `(arquivo, classe, campo, default exigido)` — as flags do portal construidas dark pelo
#: programa. O default literal `False` e' o que torna as cercas de `deploy/**` (abaixo)
#: significativas: sem ele, a flag ligaria todo ambiente que NAO a declara.
CONFIG_PORTAL = Path("src/maezo/portal/api/config.py")
ENV_PREFIX_PORTAL = "MAEZO_PORTAL_"
DEFAULTS_EXIGIDOS: tuple[tuple[Path, str, str, object], ...] = (
    (CONFIG_PORTAL, "PortalSettings", "lgpd_dsr_channel", False),
    (CONFIG_PORTAL, "PortalSettings", "staff_documents_panel", False),
    (CONFIG_PORTAL, "PortalSettings", "staff_operations_panel", False),
    (CONFIG_PORTAL, "PortalSettings", "staff_commission_policies", False),
)

#: As flags dark registradas nesta cerca e os nomes de env EXATOS que as ligam. Os quatro
#: primeiros derivam de `env_prefix` + campo (conferidos contra a arvore pela cerca 3); os
#: dois ultimos sao constantes de modulo em `identity_bridges.py` (cerca 5).
#: `bridge_flag_enabled` so' habilita com o literal `true` — OFF e' todo o resto, inclusive
#: ausente. (A flag `staff_admin_panel` do D-40 segue o registro do PR dela, fora desta
#: cerca — acrescentar o flag DE OUTRO pacote aqui e' decisao do pacote dele.)
ENV_FLAGS_PROGRAMA: tuple[str, ...] = (
    "MAEZO_PORTAL_LGPD_DSR_CHANNEL",
    "MAEZO_PORTAL_STAFF_DOCUMENTS_PANEL",
    "MAEZO_PORTAL_STAFF_OPERATIONS_PANEL",
    "MAEZO_PORTAL_STAFF_COMMISSION_POLICIES",
    "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED",
    "MAEZO_ERASURE_BRIDGE_THREAD_ENABLED",
)

MODULO_PONTES = "maezo.platform.identity_bridges"
ENV_PONTES_PINADOS: dict[str, str] = {
    "TITULAR_BRIDGE_FLAG_ENV": "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED",
    "THREAD_BRIDGE_FLAG_ENV": "MAEZO_ERASURE_BRIDGE_THREAD_ENABLED",
}


# ---------------------------------------------------------------------------------------
# Cerca 1, 2 e 3 — os defaults no codigo e o prefixo de env que da' significado aos nomes.
# ---------------------------------------------------------------------------------------
def _arvore(raiz: Path, relativo: Path) -> ast.Module | None:
    caminho = raiz / relativo
    if not caminho.is_file():
        return None
    try:
        return ast.parse(caminho.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _classe(arvore: ast.Module, nome: str) -> ast.ClassDef | None:
    for no in ast.walk(arvore):
        if isinstance(no, ast.ClassDef) and no.name == nome:
            return no
    return None


def _env_prefix_da_classe(arvore: ast.Module, classe: str) -> str | None:
    """O literal `env_prefix` do `SettingsConfigDict` da classe, ou None (reprovado)."""
    definicao = _classe(arvore, classe)
    if definicao is None:
        return None
    for no in definicao.body:
        if not isinstance(no, ast.Assign) or not no.targets:
            continue
        alvo = no.targets[0]
        if not (isinstance(alvo, ast.Name) and alvo.id == "model_config"):
            continue
        if not isinstance(no.value, ast.Call):
            continue
        for palavra in no.value.keywords:
            if palavra.arg == "env_prefix" and isinstance(palavra.value, ast.Constant):
                valor = palavra.value.value
                return valor if isinstance(valor, str) else None
    return None


def checar_defaults_desligados(raiz: Path = REPO_ROOT) -> list[str]:
    """Um default ligado tornaria dark uma flag que nao e'; um campo sumido cega a cerca."""
    achados: list[str] = []
    arvores: dict[Path, ast.Module | None] = {}
    for relativo, classe, campo, esperado in DEFAULTS_EXIGIDOS:
        if relativo not in arvores:
            arvores[relativo] = _arvore(raiz, relativo)
        arvore = arvores[relativo]
        if arvore is None:
            achados.append(f"{relativo}: nao foi possivel analisar — a cerca precisa deste arquivo.")
            continue
        prefixo = _env_prefix_da_classe(arvore, classe)
        if prefixo != ENV_PREFIX_PORTAL:
            achados.append(
                f"{relativo}: o `env_prefix` de {classe} devia ser o literal "
                f"{ENV_PREFIX_PORTAL!r} (obteve {prefixo!r}) — os nomes de env pinados nesta "
                f"cerca so' significam o que dizem sob esse prefixo."
            )
        definicao = _classe(arvore, classe)
        if definicao is None:
            achados.append(f"{relativo}: classe {classe} nao encontrada — cerca cega.")
            continue
        encontrados = [
            no
            for no in definicao.body
            if isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name) and no.target.id == campo
        ]
        if len(encontrados) != 1:
            achados.append(
                f"{relativo}: {classe}.{campo} deveria ser declarado exatamente uma vez "
                f"({len(encontrados)} encontrada(s)) — sem isso a cerca nao sabe qual default vale."
            )
            continue
        valor = encontrados[0].value
        if (
            not isinstance(valor, ast.Constant)
            or valor.value != esperado
            or (type(valor.value) is not type(esperado))
        ):
            achados.append(
                f"{relativo}: {classe}.{campo} tem de ter default literal {esperado!r} — a "
                f"docstring da flag declara DARK: virar a chave e ato da onda ROLL e nenhum "
                f"deploy pode liga-la. Um default ligado abre a superficie em todo ambiente "
                f"que NAO a declara."
            )
    return achados


# ---------------------------------------------------------------------------------------
# Cerca 4 — nenhuma mencao as flags em deploy/** (a sentenca shipped de cada uma delas).
# ---------------------------------------------------------------------------------------
def checar_deploy_sem_mencao(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    deploy = raiz / "deploy"
    if not deploy.is_dir():
        return ["deploy/: arvore ausente do checkout — varrer nada nao e' prova de flag OFF."]
    arquivos = [c for c in sorted(deploy.rglob("*")) if c.is_file()]
    if not arquivos:
        return ["deploy/: nenhuma arquivo para varrer — varrer nada nao e' prova de flag OFF."]
    for caminho in arquivos:
        try:
            texto = caminho.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            achados.append(
                f"{caminho.relative_to(raiz)}: ilegivel como UTF-8 — a cerca nao consegue "
                f"provar a ausencia das flags aqui; reprovado por precaucao."
            )
            continue
        for nome in ENV_FLAGS_PROGRAMA:
            if nome in texto:
                linha = next((i for i, linha in enumerate(texto.splitlines(), start=1) if nome in linha), 0)
                achados.append(
                    f"{caminho.relative_to(raiz)}:{linha}: {nome} declarado/mencionado em "
                    f"deploy — a docstring da flag declara DARK e NENHUM deploy/** pode liga-la "
                    f"neste programa; um template nao tem ambiente provavel, entao mencao nenhuma "
                    f"e' aceitavel (nem comentada)."
                )
    return achados


# ---------------------------------------------------------------------------------------
# Cerca 5 — as constantes das pontes continuam valendo os nomes pinados.
# ---------------------------------------------------------------------------------------
def checar_flags_das_pontes(raiz: Path = REPO_ROOT) -> list[str]:
    # `raiz` nao e' lido (a cerca importa o modulo real) e existe so' para as tres cercas
    # terem a MESMA assinatura `(raiz)` — `executar` as agrega sem saber quais leem arvore.
    # ARG001 nao esta no select do ruff deste repo; nenhum noqa inerte aqui (test_noqa_hygiene).
    del raiz
    achados: list[str] = []
    try:
        modulo = importlib.import_module(MODULO_PONTES)
    except Exception as exc:  # fail-closed: modulo ilegivel e reprova, nao silencia
        return [f"{MODULO_PONTES}: nao foi possivel importar ({type(exc).__name__}) — cerca cega."]
    for atributo, pinado in ENV_PONTES_PINADOS.items():
        observado = getattr(modulo, atributo, None)
        if observado != pinado:
            achados.append(
                f"{MODULO_PONTES}.{atributo} vale {observado!r} (pinado {pinado!r}) — o nome de "
                f"env da ponte mudou no codigo sem a cerca acompanhar; renomear flag e ato da "
                f"ROLL com cerca re-pinada no mesmo PR."
            )
    return achados


def executar(raiz: Path = REPO_ROOT) -> list[str]:
    return checar_defaults_desligados(raiz) + checar_deploy_sem_mencao(raiz) + checar_flags_das_pontes(raiz)


def main() -> int:
    achados = executar(REPO_ROOT)
    if achados:
        print("check_program_dark_flags: REPROVADO", file=sys.stderr)
        for a in achados:
            print(f"  - {a}", file=sys.stderr)
        return 1
    print(
        "check_program_dark_flags: ok — as 5 flags dark do programa com default literal OFF no "
        "codigo, zero mencao em deploy/** e os nomes de env das pontes batendo com os pinos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
