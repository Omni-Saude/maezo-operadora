"""DL-0191 (unidade U1b): `render_fatos_para_prompt` so' deixa o modelo ler a lista FECHADA.

Antes, o renderizador nomeava os booleanos e despejava TODA outra chave de `facts` no repr
"demais dados do caso". Qualquer campo novo que um montador acrescentasse ao dicionario do
dossie chegava ao prompt sem ninguem decidir. Agora:

1. `permitidos` e' keyword OBRIGATORIA, sem default: chamar sem ela e' `TypeError`.
2. Chave de `facts` fora da lista nao chega ao prompt (falha fechada por descarte) e o log
   registra so' o nome e a quantidade, nunca o valor.
3. Booleano declarado fora da lista e' erro do chamador (`PromptFormatError`).
4. A CERCA: toda chamada ao renderizador em `src/` passa `permitidos=` por nome. Chamada nova
   sem ela falha aqui, inclusive por alias de import ou acesso por atributo.

Todos os valores sao SINTETICOS.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.runtime.prompt_format import (
    FATOS_CONTEXTO_ROTULO,
    PromptFormatError,
    render_fatos_para_prompt,
)

_RAIZ = Path(__file__).resolve().parents[3]
_SRC = _RAIZ / "src"
_NOME = "render_fatos_para_prompt"
_MODULO = "maezo.runtime.prompt_format"


# --- o renderizador -----------------------------------------------------------------------


def test_sem_permitidos_e_type_error() -> None:
    with pytest.raises(TypeError, match="permitidos"):
        render_fatos_para_prompt({"a": 1}, booleanos={})  # type: ignore[call-arg]


def test_chave_fora_da_lista_nao_chega_ao_prompt() -> None:
    facts = {"permitida": "VALOR-OK-0001", "intrusa": "SENTINELA-INTRUSA-0001"}

    saida = render_fatos_para_prompt(facts, booleanos={}, permitidos=frozenset({"permitida"}))

    assert "SENTINELA-INTRUSA-0001" not in saida
    assert "intrusa" not in saida
    assert saida.split(FATOS_CONTEXTO_ROTULO, 1)[1] == "{'permitida': 'VALOR-OK-0001'}"


def test_lista_vazia_nao_deixa_passar_nada() -> None:
    saida = render_fatos_para_prompt({"x": "SENTINELA-X-0001", "y": 2}, booleanos={}, permitidos=frozenset())

    assert saida.split(FATOS_CONTEXTO_ROTULO, 1)[1] == "{}"


def test_o_descarte_registra_so_o_nome_e_a_quantidade() -> None:
    facts = {"ok": 1, "zeta": "SENTINELA-ZETA-0001", "alfa": "SENTINELA-ALFA-0001"}

    with structlog.testing.capture_logs() as logs:
        render_fatos_para_prompt(facts, booleanos={}, permitidos=frozenset({"ok"}))

    eventos = [e for e in logs if e["event"] == "prompt_fatos_chaves_fora_da_lista_descartadas"]
    assert len(eventos) == 1
    assert eventos[0]["chaves"] == ["alfa", "zeta"]
    assert eventos[0]["quantidade"] == 2
    assert "SENTINELA-" not in repr(logs)


def test_sem_descarte_nao_ha_log() -> None:
    with structlog.testing.capture_logs() as logs:
        render_fatos_para_prompt({"ok": 1}, booleanos={}, permitidos=frozenset({"ok"}))

    assert not logs


def test_o_recorte_nao_muta_o_dicionario_do_chamador() -> None:
    facts: dict[str, Any] = {"ok": 1, "fora": 2}

    render_fatos_para_prompt(facts, booleanos={}, permitidos=frozenset({"ok"}))

    assert facts == {"ok": 1, "fora": 2}


def test_booleano_fora_da_lista_e_erro_do_chamador() -> None:
    with pytest.raises(PromptFormatError, match="fora de `permitidos`"):
        render_fatos_para_prompt({"b": False}, booleanos={"b": "rotulo"}, permitidos=frozenset())


def test_booleano_pontilhado_vale_pela_chave_de_topo() -> None:
    saida = render_fatos_para_prompt(
        {"adequacao": {"c": False, "resto": 1}},
        booleanos={"adequacao.c": "rotulo"},
        permitidos=frozenset({"adequacao"}),
    )

    assert "NAO       rotulo" in saida
    assert saida.split(FATOS_CONTEXTO_ROTULO, 1)[1] == "{'adequacao': {'resto': 1}}"


def test_booleano_permitido_e_ausente_continua_sem_dado() -> None:
    saida = render_fatos_para_prompt({}, booleanos={"b": "rotulo"}, permitidos=frozenset({"b"}))

    assert "SEM DADO  rotulo" in saida


# --- a cerca AST --------------------------------------------------------------------------


def _chamadas_sem_permitidos(fonte: str, arquivo: str) -> tuple[int, list[str]]:
    """Conta as chamadas ao renderizador do runtime em `fonte` e lista as que nao passam
    `permitidos=` por nome.

    Reconhece: o nome importado de `maezo.runtime.prompt_format` (com ou sem `as`) e qualquer
    `<expr>.render_fatos_para_prompt(...)`. Um `**kwargs` nao conta como `permitidos`: a cerca
    nao consegue ler o que vai nele. Referencia ao nome importado FORA de chamada (alias por
    atribuicao, passagem como argumento) tambem acusa: seria um caminho que a cerca nao ve'.
    """
    arvore = ast.parse(fonte)
    locais: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module == _MODULO:
            for alias in no.names:
                if alias.name == _NOME:
                    locais.add(alias.asname or alias.name)

    chamadas = 0
    achados: list[str] = []
    funcs_de_chamada: set[int] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        func = no.func
        alvo = (isinstance(func, ast.Name) and func.id in locais) or (
            isinstance(func, ast.Attribute) and func.attr == _NOME
        )
        if not alvo:
            continue
        funcs_de_chamada.add(id(func))
        chamadas += 1
        if not any(kw.arg == "permitidos" for kw in no.keywords):
            achados.append(f"{arquivo}:{no.lineno}")
    for no in ast.walk(arvore):
        if (
            isinstance(no, ast.Name)
            and no.id in locais
            and isinstance(no.ctx, ast.Load)
            and id(no) not in funcs_de_chamada
        ):
            achados.append(f"{arquivo}:{no.lineno} (referencia fora de chamada)")
    return chamadas, achados


def test_toda_chamada_em_src_passa_permitidos() -> None:
    total = 0
    achados: list[str] = []
    for arquivo in sorted(_SRC.rglob("*.py")):
        fonte = arquivo.read_text(encoding="utf-8")
        if _NOME not in fonte:
            continue
        chamadas, sem = _chamadas_sem_permitidos(fonte, str(arquivo.relative_to(_RAIZ)))
        total += chamadas
        achados.extend(sem)

    assert not achados, f"chamada ao renderizador de fatos sem `permitidos=`: {achados} (DL-0191)"
    # Guarda contra cerca vazia: os 11 sitios de hoje (rafael, marina, valentina, gustavo, beatriz,
    # carolina, andre, lucas e os dois do fernando = 10 chamadas diretas; o rafael chama pelo alias).
    assert total >= 10, f"a cerca achou so' {total} chamadas — o reconhecimento quebrou?"


@pytest.mark.parametrize(
    "fonte",
    [
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}})\n",
        f"from {_MODULO} import {_NOME} as r\nx = r({{}}, booleanos={{}})\n",
        f"from maezo.runtime import prompt_format as pf\nx = pf.{_NOME}({{}}, booleanos={{}})\n",
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, **kw)\n",
        f"from {_MODULO} import {_NOME}\nf = {_NOME}\n",
    ],
    ids=["nome-direto", "alias-de-import", "atributo", "kwargs", "alias-por-atribuicao"],
)
def test_a_cerca_acusa_chamada_nova_sem_permitidos(fonte: str) -> None:
    _, achados = _chamadas_sem_permitidos(fonte, "sintetico.py")

    assert achados


def test_a_cerca_aceita_chamada_com_permitidos() -> None:
    fonte = f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, permitidos=frozenset())\n"

    chamadas, achados = _chamadas_sem_permitidos(fonte, "sintetico.py")

    assert chamadas == 1
    assert not achados


# --- todo sitio: booleanos dentro da lista ---------------------------------------------------


def _pares_booleanos_permitidos() -> list[tuple[str, dict[str, str], frozenset[str]]]:
    from maezo.agents.andre import graph as andre
    from maezo.agents.beatriz import graph as beatriz
    from maezo.agents.carolina import graph as carolina
    from maezo.agents.fernando import graph as fernando
    from maezo.agents.gustavo import graph as gustavo
    from maezo.agents.lucas import graph as lucas
    from maezo.agents.marina import graph as marina
    from maezo.agents.rafael import graph as rafael
    from maezo.agents.valentina import graph as valentina

    return [
        ("rafael", rafael._FATOS_BOOLEANOS, rafael._PERMITIDOS_PROMPT),
        ("marina-contas", marina._FATOS_BOOLEANOS_CONTAS, marina._PERMITIDOS_PROMPT_CONTAS),
        ("marina-recurso", marina._FATOS_BOOLEANOS_RECURSO, marina._PERMITIDOS_PROMPT_RECURSO),
        ("marina-reembolso", marina._FATOS_BOOLEANOS_REEMBOLSO, marina._PERMITIDOS_PROMPT_REEMBOLSO),
        ("valentina", valentina._FATOS_BOOLEANOS, valentina._PERMITIDOS_LEGADO_U1C),
        (
            "gustavo-ans",
            gustavo._FATOS_BOOLEANOS_ANS_SUBMIT,
            gustavo._PERMITIDOS_LEGADO_U1C_ANS_SUBMIT,
        ),
        ("gustavo-nip", gustavo._FATOS_BOOLEANOS_NIP, gustavo._PERMITIDOS_LEGADO_U1C_NIP),
        ("beatriz", beatriz._FATOS_BOOLEANOS, beatriz._PERMITIDOS_LEGADO_U1C),
        ("carolina", carolina._FATOS_BOOLEANOS, carolina._PERMITIDOS_LEGADO_U1C),
        (
            "andre",
            {**andre._FATOS_BOOLEANOS_PAGTO, **andre._FATOS_BOOLEANOS_ADEQUACAO},
            andre._PERMITIDOS_LEGADO_U1C,
        ),
        (
            "fernando-mensagem",
            fernando._FATOS_BOOLEANOS_MENSAGEM,
            fernando._PERMITIDOS_LEGADO_U1C_MENSAGEM,
        ),
        (
            "fernando-dossie",
            fernando._FATOS_BOOLEANOS_DOSSIE,
            fernando._PERMITIDOS_LEGADO_U1C_DOSSIE,
        ),
        ("lucas", lucas._FATOS_BOOLEANOS_MENSAGEM, lucas._PERMITIDOS_LEGADO_U1C),
    ]


@pytest.mark.parametrize("sitio", [p[0] for p in _pares_booleanos_permitidos()])
def test_os_booleanos_de_cada_sitio_estao_na_lista_dele(sitio: str) -> None:
    """O `PromptFormatError` do renderizador, provado em CI e nao em producao."""
    _, booleanos, permitidos = next(p for p in _pares_booleanos_permitidos() if p[0] == sitio)

    fora = sorted(c for c in booleanos if c.split(".", 1)[0] not in permitidos)
    assert not fora
