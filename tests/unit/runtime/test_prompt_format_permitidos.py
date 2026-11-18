"""DL-0191 (unidade U1b): `render_fatos_para_prompt` so' deixa o modelo ler a lista FECHADA.

Antes, o renderizador nomeava os booleanos e despejava TODA outra chave de `facts` no repr
"demais dados do caso". Qualquer campo novo que um montador acrescentasse ao dicionario do
dossie chegava ao prompt sem ninguem decidir. Agora:

1. `permitidos` e' keyword OBRIGATORIA, sem default: chamar sem ela e' `TypeError`.
2. Chave de `facts` fora da lista nao chega ao prompt (falha fechada por descarte) e o log
   registra so' o nome e a quantidade, nunca o valor.
3. Booleano declarado fora da lista e' erro do chamador (`PromptFormatError`).
4. O descarte e' VISIVEL: `maezo_prompt_fatos_descartados_total{agente}` conta as chaves
   descartadas, com `agente` de vocabulario fechado (`observability.PROMPT_FATOS_AGENTES`).
5. A CERCA: toda chamada ao renderizador em `src/` passa `permitidos=` por nome e `agente=` como
   literal do vocabulario fechado. Chamada nova sem elas falha aqui, inclusive por alias de
   import ou acesso por atributo; o conjunto de literais e' o vocabulario (paridade).

Todos os valores sao SINTETICOS.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.platform.observability import PROMPT_FATOS_AGENTES
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
        render_fatos_para_prompt({"a": 1}, booleanos={}, agente="marina")  # type: ignore[call-arg]


def test_sem_agente_e_type_error() -> None:
    with pytest.raises(TypeError, match="agente"):
        render_fatos_para_prompt({"a": 1}, booleanos={}, permitidos=frozenset({"a"}))  # type: ignore[call-arg]


def test_chave_fora_da_lista_nao_chega_ao_prompt() -> None:
    facts = {"permitida": "VALOR-OK-0001", "intrusa": "SENTINELA-INTRUSA-0001"}

    saida = render_fatos_para_prompt(
        facts, booleanos={}, permitidos=frozenset({"permitida"}), agente="marina"
    )

    assert "SENTINELA-INTRUSA-0001" not in saida
    assert "intrusa" not in saida
    assert saida.split(FATOS_CONTEXTO_ROTULO, 1)[1] == "{'permitida': 'VALOR-OK-0001'}"


def test_lista_vazia_nao_deixa_passar_nada() -> None:
    saida = render_fatos_para_prompt(
        {"x": "SENTINELA-X-0001", "y": 2}, booleanos={}, permitidos=frozenset(), agente="marina"
    )

    assert saida.split(FATOS_CONTEXTO_ROTULO, 1)[1] == "{}"


def test_o_descarte_registra_so_o_nome_e_a_quantidade() -> None:
    facts = {"ok": 1, "zeta": "SENTINELA-ZETA-0001", "alfa": "SENTINELA-ALFA-0001"}

    with structlog.testing.capture_logs() as logs:
        render_fatos_para_prompt(facts, booleanos={}, permitidos=frozenset({"ok"}), agente="marina")

    eventos = [e for e in logs if e["event"] == "prompt_fatos_chaves_fora_da_lista_descartadas"]
    assert len(eventos) == 1
    assert eventos[0]["chaves"] == ["alfa", "zeta"]
    assert eventos[0]["quantidade"] == 2
    assert eventos[0]["agente"] == "marina"
    assert "SENTINELA-" not in repr(logs)


def test_sem_descarte_nao_ha_log() -> None:
    with structlog.testing.capture_logs() as logs:
        render_fatos_para_prompt({"ok": 1}, booleanos={}, permitidos=frozenset({"ok"}), agente="marina")

    assert not logs


def test_o_recorte_nao_muta_o_dicionario_do_chamador() -> None:
    facts: dict[str, Any] = {"ok": 1, "fora": 2}

    render_fatos_para_prompt(facts, booleanos={}, permitidos=frozenset({"ok"}), agente="marina")

    assert facts == {"ok": 1, "fora": 2}


def test_booleano_fora_da_lista_e_erro_do_chamador() -> None:
    with pytest.raises(PromptFormatError, match="fora de `permitidos`"):
        render_fatos_para_prompt(
            {"b": False}, booleanos={"b": "rotulo"}, permitidos=frozenset(), agente="marina"
        )


def test_booleano_pontilhado_vale_pela_chave_de_topo() -> None:
    saida = render_fatos_para_prompt(
        {"adequacao": {"c": False, "resto": 1}},
        booleanos={"adequacao.c": "rotulo"},
        permitidos=frozenset({"adequacao"}),
        agente="andre",
    )

    assert "NAO       rotulo" in saida
    assert saida.split(FATOS_CONTEXTO_ROTULO, 1)[1] == "{'adequacao': {'resto': 1}}"


def test_booleano_permitido_e_ausente_continua_sem_dado() -> None:
    saida = render_fatos_para_prompt(
        {}, booleanos={"b": "rotulo"}, permitidos=frozenset({"b"}), agente="marina"
    )

    assert "SEM DADO  rotulo" in saida


# --- a cerca AST --------------------------------------------------------------------------


def _cercar(fonte: str, arquivo: str) -> tuple[int, list[str], set[str]]:
    """Le as chamadas ao renderizador do runtime em `fonte`: quantas, as que violam a cerca e os
    literais de `agente=`.

    Reconhece: o nome importado de `maezo.runtime.prompt_format` (com ou sem `as`) e qualquer
    `<expr>.render_fatos_para_prompt(...)`. Viola a cerca a chamada sem `permitidos=` por nome,
    ou sem `agente=` como literal `str` do vocabulario fechado. Um `**kwargs` nao conta: a cerca
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
    agentes: set[str] = set()
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
        kws = {kw.arg: kw.value for kw in no.keywords}
        if "permitidos" not in kws:
            achados.append(f"{arquivo}:{no.lineno} (sem permitidos)")
        agente = kws.get("agente")
        if not (isinstance(agente, ast.Constant) and isinstance(agente.value, str)):
            achados.append(f"{arquivo}:{no.lineno} (agente sem literal)")
        elif agente.value not in PROMPT_FATOS_AGENTES:
            achados.append(f"{arquivo}:{no.lineno} (agente {agente.value!r} fora do vocabulario)")
        else:
            agentes.add(agente.value)
    for no in ast.walk(arvore):
        if (
            isinstance(no, ast.Name)
            and no.id in locais
            and isinstance(no.ctx, ast.Load)
            and id(no) not in funcs_de_chamada
        ):
            achados.append(f"{arquivo}:{no.lineno} (referencia fora de chamada)")
    return chamadas, achados, agentes


def _cercar_src() -> tuple[int, list[str], set[str]]:
    total = 0
    achados: list[str] = []
    agentes: set[str] = set()
    for arquivo in sorted(_SRC.rglob("*.py")):
        fonte = arquivo.read_text(encoding="utf-8")
        if _NOME not in fonte:
            continue
        chamadas, sem, nomes = _cercar(fonte, str(arquivo.relative_to(_RAIZ)))
        total += chamadas
        achados.extend(sem)
        agentes |= nomes
    return total, achados, agentes


def test_toda_chamada_em_src_passa_permitidos_e_agente() -> None:
    total, achados, _ = _cercar_src()

    assert not achados, f"chamada ao renderizador de fatos fora da cerca: {achados} (DL-0191)"
    # Guarda contra cerca vazia: rafael (pelo alias), marina, valentina, gustavo, beatriz,
    # carolina, andre, lucas e os dois do fernando = 10 chamadas.
    assert total >= 10, f"a cerca achou so' {total} chamadas — o reconhecimento quebrou?"


def test_paridade_entre_o_vocabulario_e_os_sitios() -> None:
    """O rotulo `agente` do contador e' EXATAMENTE o conjunto de agentes que chamam o renderizador.

    E cada um e' agente conhecido da telemetria de desfecho (CC-09): rotulo novo nao nasce aqui.
    """
    from maezo.runtime.turn_telemetry import _DESFECHO_VOCAB

    _, _, agentes = _cercar_src()

    assert agentes == PROMPT_FATOS_AGENTES
    assert set(_DESFECHO_VOCAB) >= PROMPT_FATOS_AGENTES


_SEM_AGENTE = "permitidos=frozenset()"


@pytest.mark.parametrize(
    "fonte",
    [
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, agente='marina')\n",
        f"from {_MODULO} import {_NOME} as r\nx = r({{}}, booleanos={{}}, agente='marina')\n",
        (
            "from maezo.runtime import prompt_format as pf\n"
            f"x = pf.{_NOME}({{}}, booleanos={{}}, agente='marina')\n"
        ),
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, **kw)\n",
        f"from {_MODULO} import {_NOME}\nf = {_NOME}\n",
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, {_SEM_AGENTE})\n",
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, {_SEM_AGENTE}, agente=a)\n",
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, {_SEM_AGENTE}, agente='zeca')\n",
    ],
    ids=[
        "nome-direto",
        "alias-de-import",
        "atributo",
        "kwargs",
        "alias-por-atribuicao",
        "sem-agente",
        "agente-variavel",
        "agente-fora-do-vocabulario",
    ],
)
def test_a_cerca_acusa_chamada_fora_dela(fonte: str) -> None:
    _, achados, _ = _cercar(fonte, "sintetico.py")

    assert achados


def test_a_cerca_aceita_chamada_com_permitidos_e_agente() -> None:
    fonte = (
        f"from {_MODULO} import {_NOME}\nx = {_NOME}({{}}, booleanos={{}}, {_SEM_AGENTE}, agente='marina')\n"
    )

    chamadas, achados, agentes = _cercar(fonte, "sintetico.py")

    assert chamadas == 1
    assert not achados
    assert agentes == {"marina"}


# --- o descarte e' visivel no contador ---------------------------------------------------------


def _contador(agente: str) -> float:
    from maezo.platform.observability import get_metrics_collector

    valor = get_metrics_collector().registry.get_sample_value(
        "maezo_prompt_fatos_descartados_total", {"agente": agente}
    )
    return valor or 0.0


def test_o_descarte_soma_no_contador_do_agente() -> None:
    antes = _contador("valentina")

    render_fatos_para_prompt(
        {"ok": 1, "a": 2, "b": 3}, booleanos={}, permitidos=frozenset({"ok"}), agente="valentina"
    )

    assert _contador("valentina") == antes + 2


def test_sem_descarte_o_contador_nao_anda() -> None:
    antes = _contador("lucas")

    render_fatos_para_prompt({"ok": 1}, booleanos={}, permitidos=frozenset({"ok"}), agente="lucas")

    assert _contador("lucas") == antes


def test_agente_fora_do_vocabulario_conta_como_outro() -> None:
    antes = _contador("outro")

    render_fatos_para_prompt({"x": 1}, booleanos={}, permitidos=frozenset(), agente="zeca")

    assert _contador("outro") == antes + 1
    assert _contador("zeca") == 0.0


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
