#!/usr/bin/env python3
"""Cerca de CI: o roteador Helena -> Lucas do numero unico so' liga em dev, e so' por um `if`.

Por que esta cerca existe
--------------------------
`MAEZO_ROTEADOR_LUCAS` (ADR-0062, plano `docs/plans/lucas-numero-unico.md` §4) liga o roteamento
por conversa no receptor do WhatsApp: com ele ligado, a conversa pode sair da Helena e ir para o
Lucas, que atende cobranca com uma fonte de fatos SIMULADA (`MAEZO_LUCAS_FONTE_COBRANCA`). Isso e'
aceitavel em `dev-sa-east-1`, com o time testando, e em nenhum outro lugar enquanto nao existir
fonte real, DMN aprovada e DPA do provedor do Lucas. A decisao de onde ligar nao pode depender de
alguem lembrar: e' isto que a cerca mecaniza.

O que ela reprova (itens 1-5 de §4; 6-7 entram na onda (f), quando os simbolos existirem)
-----------------------------------------------------------------------------------------
  1. `MAEZO_ROTEADOR_LUCAS` ligado, ou `MAEZO_LUCAS_FONTE_COBRANCA` presente, em qualquer
     `deploy/**` que nao seja `dev-sa-east-1`.
  2. Qualquer um dos tres nomes (os dois acima e `MAEZO_LUCAS_INATIVIDADE_MINUTOS`) entregue por
     `valueFrom`, ou com valor que o analisador nao consegue resolver, em QUALQUER ambiente.
  3. Qualquer um dos tres declarado fora do Terraform que esta cerca analisa (Helm, compose, k8s),
     ou mencionado num `.tf` em forma que nao vira entrada de `environment`.
  4. O default de `WhatsAppWebhookSettings.roteador_lucas_enabled` diferente do LITERAL `False`
     (direto ou como `Field(default=False, ...)`).
  5. Construcao de `ConversaRouter` ou `LucasTurno` (inclusive por alias de import ou atributo de
     modulo) em qualquer arquivo de `src/maezo/` fora do CORPO de um
     `if <algo>.roteador_lucas_enabled:` — nem no `else`, nem sob um `if` negado ou de outro nome.

Por que ela reaproveita o analisador de `check_canal_simular`
-------------------------------------------------------------
Pelo mesmo motivo de `check_portal_direct_completion.py`: o analisador de HCL de la' passou pela
revisao adversarial RV-371 (6 de 7 evasoes passavam quando se casava regex contra o texto dos
`.tf`) e resolve `local.x`/`var.x` como o Terraform, tratando indirecao nao resolvivel como
ACHADO. Um segundo parser aqui repetiria os seis bugs. O que e' proprio desta cerca e' a POLITICA
e as cercas de codigo dos itens 4 e 5.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.check_canal_simular import (  # noqa: E402
    AMBIENTE_PERMITIDO,
    _inventario,
    _ligado,
)

SOMENTE_DEV = "somente-dev"


@dataclass(frozen=True)
class Politica:
    #: `"booleano"`: `_ligado()` decide. `"presenca"`: qualquer valor nao vazio conta como ligado.
    #: `"visivel"`: nunca "ligado" (o valor e' inerte), mas os itens 2 e 3 valem igual.
    forma: str
    justificativa: str


POLITICAS: dict[str, Politica] = {
    "MAEZO_ROTEADOR_LUCAS": Politica(
        "booleano",
        "liga o roteamento por conversa do numero unico: a conversa pode sair da Helena para o "
        "Lucas, que atende cobranca com fatos SIMULADOS (ADR-0062).",
    ),
    "MAEZO_LUCAS_FONTE_COBRANCA": Politica(
        "presenca",
        "escolhe a fonte dos fatos de cobranca do Lucas; so' existe a simulada, e presente fora "
        "de dev significa que alguem montou o Lucas onde ele nao pode existir.",
    ),
    "MAEZO_LUCAS_INATIVIDADE_MINUTOS": Politica(
        "visivel",
        "janela de inatividade da conversa com o Lucas; inerte sozinha, mas tem de ser legivel "
        "na task definition como as outras duas.",
    ),
}

SETTINGS = Path("src/maezo/platform/webhooks/whatsapp/settings.py")
CLASSE_SETTINGS = "WhatsAppWebhookSettings"
CAMPO_INTERRUPTOR = "roteador_lucas_enabled"
ROTEAMENTO = Path("src/maezo/platform/webhooks/whatsapp/roteamento.py")
CLASSE_ROTEADOR = "ConversaRouter"
#: As classes cuja CONSTRUCAO so' pode acontecer com o interruptor ligado.
CONSTRUCOES_GOVERNADAS = frozenset({CLASSE_ROTEADOR, "LucasTurno"})
CODIGO = Path("src/maezo")


# ---------------------------------------------------------------------------------------
# Itens 1, 2 e 3 — a configuracao efetiva das task definitions.
# ---------------------------------------------------------------------------------------
def checar_ligacao_fora_de_dev(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    _, entradas, textos = _inventario(raiz)

    avaliadas: dict[tuple[Path, str], int] = {}
    for entrada in entradas:
        for vigiado in POLITICAS:
            if vigiado in entrada.nome_cru or entrada.nome == vigiado:
                avaliadas[(entrada.arquivo, vigiado)] = avaliadas.get((entrada.arquivo, vigiado), 0) + 1
        nome = entrada.nome
        politica = POLITICAS.get(nome) if nome is not None else None
        if nome is None or politica is None:
            continue
        onde = entrada.onde(raiz)
        if entrada.de_segredo:
            achados.append(
                f"{onde}: {nome} entregue por `valueFrom` — este interruptor existe para ser "
                f"VISIVEL na task definition; vindo de segredo, nem a cerca nem o review o veem. "
                f"({politica.justificativa})"
            )
            continue
        if entrada.valor is None:
            achados.append(
                f"{onde}: {nome} = {entrada.valor_cru!r} nao e' um literal que a cerca consiga "
                f"resolver — indirecao nao resolvivel e' REPROVACAO, nao silencio. "
                f"({politica.justificativa})"
            )
            continue
        if politica.forma == "booleano":
            ligado = _ligado(entrada.valor)
        elif politica.forma == "presenca":
            ligado = bool(entrada.valor.strip())
        else:
            ligado = False
        if ligado and entrada.ambiente != AMBIENTE_PERMITIDO:
            achados.append(
                f"{onde}: {nome}={entrada.valor!r} no ambiente "
                f"{entrada.ambiente or '<fora de envs/>'} — interruptor de escopo {SOMENTE_DEV}, "
                f"so' pode ser ligado em {AMBIENTE_PERMITIDO}. ({politica.justificativa})"
            )

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
    achados: list[str] = []
    deploy = raiz / "deploy"
    if not deploy.is_dir():
        return achados
    for caminho in sorted(deploy.rglob("*")):
        if not caminho.is_file() or caminho.suffix == ".tf":
            continue
        # Um `.tftest.hcl` nao deploya nada: so' afirma o que o `.tf` (ja' analisado) produz.
        if caminho.name.endswith(".tftest.hcl"):
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for nome in POLITICAS:
            if nome in texto:
                achados.append(
                    f"{caminho.relative_to(raiz)}: {nome} declarado fora do Terraform que esta "
                    f"cerca analisa — um template nao tem ambiente provavel."
                )
    return achados


# ---------------------------------------------------------------------------------------
# Item 4 — o default no codigo.
# ---------------------------------------------------------------------------------------
def _arvore(caminho: Path) -> ast.Module | None:
    if not caminho.is_file():
        return None
    try:
        return ast.parse(caminho.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _default_literal(valor: ast.expr | None) -> ast.expr | None:
    """O `default` de `campo: T = <valor>`: o proprio valor, ou o `default=` de `Field(...)`."""
    if isinstance(valor, ast.Call):
        nome = valor.func.id if isinstance(valor.func, ast.Name) else getattr(valor.func, "attr", "")
        if nome != "Field":
            return None
        for palavra in valor.keywords:
            if palavra.arg == "default":
                return palavra.value
        return valor.args[0] if valor.args else None
    return valor


def checar_default_desligado(raiz: Path = REPO_ROOT) -> list[str]:
    arvore = _arvore(raiz / SETTINGS)
    if arvore is None:
        return [f"{SETTINGS}: nao foi possivel analisar — a cerca precisa deste arquivo."]
    classe = next(
        (no for no in ast.walk(arvore) if isinstance(no, ast.ClassDef) and no.name == CLASSE_SETTINGS),
        None,
    )
    if classe is None:
        return [f"{SETTINGS}: classe {CLASSE_SETTINGS} nao encontrada — cerca cega."]
    campos = [
        no
        for no in classe.body
        if isinstance(no, ast.AnnAssign)
        and isinstance(no.target, ast.Name)
        and no.target.id == CAMPO_INTERRUPTOR
    ]
    if len(campos) != 1:
        return [
            f"{SETTINGS}: {CLASSE_SETTINGS}.{CAMPO_INTERRUPTOR} deveria ser declarado exatamente "
            f"uma vez ({len(campos)} encontrada(s))."
        ]
    default = _default_literal(campos[0].value)
    if not (isinstance(default, ast.Constant) and default.value is False):
        return [
            f"{SETTINGS}: {CLASSE_SETTINGS}.{CAMPO_INTERRUPTOR} tem de ter default LITERAL `False` "
            f"(ADR-0062): um default ligado abre o roteador em todo ambiente que NAO o declara, "
            f"que e' o oposto do que a cerca de `deploy/**` consegue ver."
        ]
    return []


# ---------------------------------------------------------------------------------------
# Item 5 — construcao so' dentro do `if` do interruptor.
# ---------------------------------------------------------------------------------------
def _nomes_governados(arvore: ast.Module) -> set[str]:
    """Os nomes pelos quais este modulo alcanca uma classe governada (inclusive `as` alias)."""
    nomes = set(CONSTRUCOES_GOVERNADAS)
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom):
            for alias in no.names:
                if alias.name in CONSTRUCOES_GOVERNADAS and alias.asname:
                    nomes.add(alias.asname)
    return nomes


def _construcoes(arvore: ast.Module) -> list[ast.Call]:
    nomes = _nomes_governados(arvore)
    achadas: list[ast.Call] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        if (isinstance(no.func, ast.Name) and no.func.id in nomes) or (
            isinstance(no.func, ast.Attribute) and no.func.attr in CONSTRUCOES_GOVERNADAS
        ):
            achadas.append(no)
    return achadas


def _e_teste_do_interruptor(teste: ast.expr) -> bool:
    return isinstance(teste, ast.Attribute) and teste.attr == CAMPO_INTERRUPTOR


def _protegidas(arvore: ast.Module) -> set[int]:
    """`id()` de toda chamada que mora no CORPO (nao no `else`) de um `if <x>.roteador_lucas_enabled`."""
    dentro: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.If) and _e_teste_do_interruptor(no.test):
            for comando in no.body:
                for filho in ast.walk(comando):
                    dentro.add(id(filho))
    return dentro


def checar_construcao_condicional(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    roteamento = _arvore(raiz / ROTEAMENTO)
    if roteamento is None or not any(
        isinstance(no, ast.ClassDef) and no.name == CLASSE_ROTEADOR for no in ast.walk(roteamento)
    ):
        achados.append(
            f"{ROTEAMENTO}: classe {CLASSE_ROTEADOR} nao encontrada — com o nome trocado, esta "
            f"cerca procuraria uma construcao que nao existe mais (cerca cega)."
        )
    codigo = raiz / CODIGO
    for caminho in sorted(codigo.rglob("*.py")) if codigo.is_dir() else []:
        arvore = _arvore(caminho)
        if arvore is None:
            continue
        protegidas = _protegidas(arvore)
        for chamada in _construcoes(arvore):
            if id(chamada) not in protegidas:
                nome = ast.unparse(chamada.func)
                achados.append(
                    f"{caminho.relative_to(raiz)}:{chamada.lineno}: `{nome}(...)` construido fora do "
                    f"corpo de um `if settings.{CAMPO_INTERRUPTOR}:` — desligado, o roteador nem pode "
                    f"existir (ADR-0062)."
                )
    return achados


def executar(raiz: Path = REPO_ROOT) -> list[str]:
    return (
        checar_ligacao_fora_de_dev(raiz)
        + checar_default_desligado(raiz)
        + checar_construcao_condicional(raiz)
    )


def main() -> int:
    achados = executar(REPO_ROOT)
    if achados:
        print("check_roteador_lucas: REPROVADO", file=sys.stderr)
        for a in achados:
            print(f"  - {a}", file=sys.stderr)
        return 1
    print(
        "check_roteador_lucas: ok — roteador desligado por padrao, ligavel so' em "
        f"{AMBIENTE_PERMITIDO}, visivel na task definition e construido so' dentro do `if`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
