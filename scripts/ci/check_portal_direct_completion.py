#!/usr/bin/env python3
"""Cerca de CI: a conclusao DIRETA do portal e o CORS do portal so' podem existir em dev.

Por que esta cerca existe
--------------------------
`docs/decisions-log.md` DL-0049 registra um atalho: `POST /api/v1/portal/tasks/{id}/completion`
completa uma tarefa de ESCALATION chamando `POST {engine}/task/{id}/complete` direto, em vez do
envelope assinado + outbox durável que a ADR-0049 D5/D6/D7 desenha. O atalho existe porque o rele'
D6 esta' declarado NAO ESTABELECIDO em `src/maezo/portal/engine/README.md` e, sem ele, o portal
mostra a fila e nao fecha o caso.

Um atalho assim e' aceitavel em `dev-sa-east-1`, atras do Cloudflare Access, enquanto o rele' de
#427 nao existe. Nao e' aceitavel em nenhum outro ambiente — a chamada nao tem assinatura, nao tem
receipt, nao tem cerca otimista do lado do motor e nao tem reentrega. E a decisao de onde ligar nao
pode depender de alguem lembrar: e' isto que a cerca mecaniza. A mesma regra vale para
`MAEZO_PORTAL_CORS_ORIGINS`: aceitar uma origem de OUTRO dominio COM credenciais e' o que permite a
pagina de teste (`platform/testchannel/paginas/escalonamento.html`, demonstracao declarada sem
autenticacao propria) falar com o portal. Fora de dev isso transformaria a pagina numa porta de
entrada, que e' exatamente o que a Frente 3.3 do mandato proibe.

O que ela reprova
------------------
  1. `MAEZO_PORTAL_DIRECT_COMPLETION` ligado, ou `MAEZO_PORTAL_DIRECT_COMPLETION_ENGINE_ORIGIN` /
     `..._TIMEOUT_SECONDS` presentes, em qualquer `deploy/**` que nao seja `dev-sa-east-1`.
  2. `MAEZO_PORTAL_CORS_ORIGINS` com valor nao vazio fora de `dev-sa-east-1`.
  3. Qualquer uma das quatro entregue por `valueFrom` (segredo), ou com valor que o analisador
     nao consegue resolver, em QUALQUER ambiente: um portao invisivel nao e' um portao.
  4. Qualquer uma delas declarada fora do Terraform que esta cerca analisa (Helm, compose, k8s):
     um template nao tem ambiente provavel.
  5. O default deixando de ser desligado no CODIGO: `PortalSettings.direct_completion` e
     `PortalProductionSettings.direct_completion` tem de ser literalmente `False`, e
     `PortalSettings.cors_origins` literalmente `""`. Um default ligado tornaria os itens 1 e 2
     decorativos — o portao passaria a estar aberto em todo ambiente que NAO o declara.
  6. O CORS deixando de ser condicional: `create_app` so' pode instalar `CORSMiddleware` dentro de
     um `if`, e nunca com curinga (`allow_origins=["*"]`, `allow_origin_regex`,
     `allow_credentials` sem allowlist).
  7. A rota deixando de recusar com o portao desligado: `tasks.py::complete_task` tem de devolver
     `completion_error("completion_unavailable")` ANTES do primeiro `await` — antes de ler cookie,
     de resolver sessao e de tocar em qualquer dependencia.

Por que ela reaproveita o analisador de `check_canal_simular`
-------------------------------------------------------------
Aquela cerca ja passou por uma revisao adversarial (RV-371) que mostrou 6 de 7 evasoes passando
quando se casava expressao regular contra o texto dos `.tf`: bastava quebrar o objeto em duas
linhas ou trocar `value = "1"` por `value = local.ligar`. O analisador que ficou de la' resolve
`local.x` / `var.x` como o Terraform resolveria e trata **indireção nao resolvivel como ACHADO**.
Escrever um segundo analisador aqui seria escrever de novo os mesmos seis bugs. O que e' proprio
desta cerca e' a POLITICA (quais nomes, onde podem estar ligados) e as cercas de codigo do item 5
ao 7 — nao o parser de HCL.

Ela NAO tenta decidir se o atalho deve existir. Essa decisao e' do dono e esta' registrada em
DL-0049; aqui so' se cobra o perimetro que aquela decisao assumiu.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    # Explicit, idempotent insert so the dotted import below resolves the same whether this module
    # is run directly (`python scripts/ci/check_portal_direct_completion.py`, where sys.path[0] is
    # scripts/ci, NOT the repo root) or imported by pytest (`pythonpath = ["."]` covers it there).
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.check_canal_simular import (  # noqa: E402
    AMBIENTE_PERMITIDO,
    _inventario,
    _ligado,
)

#: Os quatro nomes que esta cerca governa, com o ESCOPO de cada um e o porque. A justificativa
#: nao e' decoracao: e' o que um revisor le' quando a cerca reprova.
SOMENTE_DEV = "somente-dev"


@dataclass(frozen=True)
class Politica:
    """Como o valor conta como "ligado", e por que o portao existe."""

    #: `"booleano"` -> `_ligado()` decide (1/true/yes/on); `"presenca"` -> qualquer valor nao
    #: vazio conta como ligado, porque o valor em si E' a capacidade (uma origem, uma URL).
    forma: str
    justificativa: str


POLITICAS: dict[str, Politica] = {
    "MAEZO_PORTAL_DIRECT_COMPLETION": Politica(
        "booleano",
        "abre `POST /api/v1/portal/tasks/{id}/completion`, que completa a tarefa no motor SEM "
        "envelope assinado, receipt, outbox nem cerca otimista — o contrario do que a ADR-0049 "
        "D5/D6/D7 exige (DL-0049).",
    ),
    "MAEZO_PORTAL_DIRECT_COMPLETION_ENGINE_ORIGIN": Politica(
        "presenca",
        "e' o endereco do motor que a conclusao direta chama; sem ele o atalho nao alcanca nada, "
        "com ele alcanca o engine-rest, que nesta distribuicao nao tem autenticacao.",
    ),
    "MAEZO_PORTAL_DIRECT_COMPLETION_TIMEOUT_SECONDS": Politica(
        "presenca",
        "metade complete-or-absent do perfil interino; presente fora de dev significa que alguem "
        "montou o perfil onde ele nao pode existir.",
    ),
    "MAEZO_PORTAL_CORS_ORIGINS": Politica(
        "presenca",
        "faz o portal aceitar uma origem de outro dominio COM credenciais. Existe para a pagina "
        "de teste, que e' demonstracao declarada sem autenticacao propria; fora de dev "
        "transformaria essa pagina em porta de entrada (Frente 3.3).",
    ),
}

#: Os arquivos de codigo cujas cercas (itens 5 a 7) precisam continuar valendo.
CONFIG_IDENTIDADE = Path("src/maezo/portal/api/config.py")
CONFIG_PRODUCAO = Path("src/maezo/gateway/staff_cases/production_config.py")
APP = Path("src/maezo/portal/api/app.py")
ROTAS = Path("src/maezo/portal/api/tasks.py")

#: `(arquivo, classe, campo, default exigido)`.
DEFAULTS_EXIGIDOS: tuple[tuple[Path, str, str, object], ...] = (
    (CONFIG_IDENTIDADE, "PortalSettings", "direct_completion", False),
    (CONFIG_IDENTIDADE, "PortalSettings", "cors_origins", ""),
    (CONFIG_PRODUCAO, "PortalProductionSettings", "direct_completion", False),
)

CODIGO_RECUSA = "completion_unavailable"
FUNCAO_ROTA = "complete_task"
MIDDLEWARE_CORS = "CORSMiddleware"
CURINGAS_PROIBIDOS = ("allow_origin_regex",)


# ---------------------------------------------------------------------------------------
# Cerca 1 e 2 e 3 — a configuracao efetiva das task definitions.
# ---------------------------------------------------------------------------------------
def checar_ligacao_fora_de_dev(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    _, entradas, textos = _inventario(raiz)

    avaliadas: dict[tuple[Path, str], int] = {}
    for entrada in entradas:
        for nome in POLITICAS:
            if nome in entrada.nome_cru or entrada.nome == nome:
                avaliadas[(entrada.arquivo, nome)] = avaliadas.get((entrada.arquivo, nome), 0) + 1
        nome = entrada.nome
        politica = POLITICAS.get(nome) if nome is not None else None
        if nome is None or politica is None:
            continue
        onde = entrada.onde(raiz)
        if entrada.de_segredo:
            achados.append(
                f"{onde}: {nome} entregue por `valueFrom` — este portao existe para ser VISIVEL "
                f"na task definition; vindo de segredo, nem a cerca nem o review o veem. "
                f"({politica.justificativa})"
            )
            continue
        if entrada.valor is None:
            achados.append(
                f"{onde}: {nome} = {entrada.valor_cru!r} nao e' um literal que a cerca consiga "
                f"resolver — indireção nao resolvivel e' REPROVACAO, nao silencio: sem resolve-la "
                f"nao ha como provar que o portao esta desligado. ({politica.justificativa})"
            )
            continue
        ligado = _ligado(entrada.valor) if politica.forma == "booleano" else bool(entrada.valor.strip())
        if ligado and entrada.ambiente != AMBIENTE_PERMITIDO:
            achados.append(
                f"{onde}: {nome}={entrada.valor!r} no ambiente "
                f"{entrada.ambiente or '<fora de envs/>'} — portao de escopo {SOMENTE_DEV}, so' "
                f"pode ser ligado em {AMBIENTE_PERMITIDO}. ({politica.justificativa})"
            )

    # Declaracao que a cerca NAO conseguiu ler como entrada de ambiente: reprova em vez de
    # ignorar. Comentarios ja sairam do texto mascarado, entao mencao aqui e' codigo.
    for arquivo, texto in textos.items():
        for nome in POLITICAS:
            ocorrencias = texto.count(nome)
            if ocorrencias > avaliadas.get((arquivo, nome), 0):
                achados.append(
                    f"{arquivo.relative_to(raiz)}: {nome} aparece em forma que esta cerca nao "
                    f"consegue avaliar como entrada de `environment` ({ocorrencias} mencao(oes), "
                    f"{avaliadas.get((arquivo, nome), 0)} avaliada(s)) — reprovado por precaucao."
                )
    achados.extend(_fora_do_terraform(raiz))
    return achados


def _fora_do_terraform(raiz: Path) -> list[str]:
    """Portao declarado num manifesto que esta cerca nao analisa (Helm, compose, k8s...).

    Nao ha como provar o ambiente de um template, entao qualquer ocorrencia reprova.
    """
    achados: list[str] = []
    deploy = raiz / "deploy"
    if not deploy.is_dir():
        return achados
    for caminho in sorted(deploy.rglob("*")):
        if not caminho.is_file() or caminho.suffix == ".tf":
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for nome in POLITICAS:
            if nome in texto:
                achados.append(
                    f"{caminho.relative_to(raiz)}: {nome} declarado fora do Terraform que esta "
                    f"cerca analisa — um template nao tem ambiente provavel, entao um portao "
                    f"{SOMENTE_DEV} nao pode ser ligado por ele."
                )
    return achados


# ---------------------------------------------------------------------------------------
# Cerca 5 — os defaults no codigo.
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


def checar_defaults_desligados(raiz: Path = REPO_ROOT) -> list[str]:
    """Um default ligado tornaria as cercas de ambiente decorativas."""
    achados: list[str] = []
    for relativo, classe, campo, esperado in DEFAULTS_EXIGIDOS:
        arvore = _arvore(raiz, relativo)
        if arvore is None:
            achados.append(f"{relativo}: nao foi possivel analisar — a cerca precisa deste arquivo.")
            continue
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
                f"{relativo}: {classe}.{campo} tem de ter default literal {esperado!r} "
                f"(DL-0049: desligado por padrao). Um default ligado abre o portao em todo "
                f"ambiente que NAO o declara, que e' o oposto do que as cercas de `deploy/**` "
                f"conseguem ver."
            )
    return achados


# ---------------------------------------------------------------------------------------
# Cerca 6 — CORS condicional, sem curinga.
# ---------------------------------------------------------------------------------------
def checar_cors_condicional(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    arvore = _arvore(raiz, APP)
    if arvore is None:
        achados.append(f"{APP}: nao foi possivel analisar — a cerca precisa deste arquivo.")
        return achados
    instalacoes = [
        no
        for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and any(isinstance(a, ast.Name) and a.id == MIDDLEWARE_CORS for a in no.args)
    ]
    if not instalacoes:
        # Nenhum CORS: e' o estado mais seguro possivel, nada a cobrar.
        return achados
    dentro_de_if: set[int] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, ast.If):
            continue
        for filho in ast.walk(no):
            if isinstance(filho, ast.Call) and any(id(filho) == id(c) for c in instalacoes):
                dentro_de_if.add(id(filho))
    if any(id(chamada) not in dentro_de_if for chamada in instalacoes):
        achados.append(
            f"{APP}: `{MIDDLEWARE_CORS}` instalado FORA de um `if` — o CORS do portal so' pode "
            f"existir quando a allowlist estiver preenchida, e ela ja' nasce vazia (DL-0049)."
        )
    for chamada in instalacoes:
        for palavra in chamada.keywords:
            if palavra.arg in CURINGAS_PROIBIDOS:
                achados.append(
                    f"{APP}: `{palavra.arg}` em `{MIDDLEWARE_CORS}` — uma expressao casa origens "
                    f"que ninguem declarou; a allowlist e' uma lista de strings exatas."
                )
            if palavra.arg == "allow_origins":
                for elemento in ast.walk(palavra.value):
                    if isinstance(elemento, ast.Constant) and elemento.value == "*":
                        achados.append(
                            f"{APP}: `allow_origins` com curinga `*` — com credenciais o "
                            f"navegador recusa, e sem elas a rota nao autentica ninguem."
                        )
    return achados


# ---------------------------------------------------------------------------------------
# Cerca 7 — a rota recusa antes de tocar em dependencia.
# ---------------------------------------------------------------------------------------
def checar_recusa_antes_do_await(raiz: Path = REPO_ROOT) -> list[str]:
    """O 501 do portao desligado nao pode depender de sessao, cookie ou motor.

    Se a recusa vier DEPOIS do primeiro `await`, um portal com o portao fechado ja teria
    resolvido sessao e potencialmente tocado numa dependencia para responder "desligado".
    """
    achados: list[str] = []
    arvore = _arvore(raiz, ROTAS)
    if arvore is None:
        achados.append(f"{ROTAS}: nao foi possivel analisar — a cerca precisa deste arquivo.")
        return achados
    funcao = next(
        (
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.AsyncFunctionDef | ast.FunctionDef) and no.name == FUNCAO_ROTA
        ),
        None,
    )
    if funcao is None:
        achados.append(f"{ROTAS}: funcao {FUNCAO_ROTA} nao encontrada — cerca cega.")
        return achados
    recusa_em: list[int] = []
    await_em: list[int] = []
    for no in ast.walk(funcao):
        if isinstance(no, ast.Await):
            await_em.append(no.lineno)
        if (
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Name)
            and no.func.id == "completion_error"
            and any(isinstance(a, ast.Constant) and a.value == CODIGO_RECUSA for a in no.args)
        ):
            recusa_em.append(no.lineno)
    if not recusa_em:
        achados.append(
            f"{ROTAS}: {FUNCAO_ROTA} nao recusa com `completion_error({CODIGO_RECUSA!r})` — o "
            f"portao desligado deixou de ter resposta declarada (DL-0049)."
        )
        return achados
    if await_em and min(recusa_em) > min(await_em):
        achados.append(
            f"{ROTAS}: {FUNCAO_ROTA} recusa o portao desligado na linha {min(recusa_em)}, DEPOIS "
            f"do primeiro `await` (linha {min(await_em)}) — a recusa tem de vir antes de qualquer "
            f"dependencia ser tocada."
        )
    return achados


def executar(raiz: Path = REPO_ROOT) -> list[str]:
    return (
        checar_ligacao_fora_de_dev(raiz)
        + checar_defaults_desligados(raiz)
        + checar_cors_condicional(raiz)
        + checar_recusa_antes_do_await(raiz)
    )


def main() -> int:
    achados = executar(REPO_ROOT)
    if achados:
        print("check_portal_direct_completion: REPROVADO", file=sys.stderr)
        for a in achados:
            print(f"  - {a}", file=sys.stderr)
        return 1
    print(
        "check_portal_direct_completion: ok — conclusao direta e CORS desligados por padrao, "
        f"ligaveis so' em {AMBIENTE_PERMITIDO}, CORS condicional sem curinga e a rota recusando "
        "antes de tocar em dependencia."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
