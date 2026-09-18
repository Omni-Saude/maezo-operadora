"""A lista de codigos do prompt e as regras das quatro DMN sao o MESMO conjunto, por populacao.

POR QUE ESTE ARQUIVO EXISTE. `prompts.py` afirma, em comentario, sobre `SINTOMA_CODIGOS_BY_POPULATION`:

    "Every code exists verbatim in the deployed `spec/processes/dmn/triage_redflag_*.dmn` rule
     literals (set-identity verified)"

A verificacao nao existia. Procurada em `tests/` e em `scripts/ci/` na bateria de 13/09/2026 e nao
encontrada — a afirmacao era verdadeira quando escrita e nada garantia que continuasse.

O QUE ISSO CUSTA SE DERIVAR, nas duas direcoes:
  - codigo na LISTA sem regra na tabela: o modelo e' instruido a produzi-lo, o validador de schema
    o ACEITA (ele le' a mesma lista), a DMN nao casa nenhuma regra e o caso cai no vazio final.
    Um sintoma que o prompt anuncia como reconhecido termina sem bandeira;
  - codigo na REGRA fora da lista: a regra existe e nunca dispara, porque o modelo nunca produz
    aquele codigo — e o validador recusaria se produzisse (`ALLOWED_SINTOMA_CODIGOS`). Regra morta
    numa tabela clinica, que alguem leu e considerou coberta.

A cerca fica aqui, e nao em `scripts/ci/`, porque ela le' os mesmos artefatos que os testes de
grafo ja leem e roda no mesmo portao (`lint / type / unit`) — um script novo so' teria valor se
precisasse rodar fora do pytest, que nao e' o caso.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from maezo.agents.helena.prompts import SINTOMA_CODIGOS_BY_POPULATION

_DMN = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"

#: Populacao -> arquivo da tabela. Explicito, nao derivado do nome: um `glob` faria uma tabela
#: renomeada sumir da cerca em silencio, que e' o modo de falha que esta cerca existe para impedir.
TABELAS: dict[str, str] = {
    "adult": "triage_redflag_adult.dmn",
    "pediatric": "triage_redflag_pediatric.dmn",
    "gestante": "triage_redflag_gestante.dmn",
    "mental_health": "triage_redflag_mental_health.dmn",
}

RAIZ_DMN = Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn"


def codigos_da_tabela(xml: str) -> set[str]:
    """Literais de `sintoma_codigo` que aparecem nas regras de UMA tabela.

    Localiza a COLUNA pelo `inputExpression` (nunca pelo `label`, que e' texto para humano e ja'
    enganou a pagina do canal), e le' a entrada daquela coluna em cada regra. `-` e' o coringa do
    DMN e nao e' um codigo; entradas com varios literais (`"a","b"`) sao separadas.
    """
    raiz = ET.fromstring(xml)
    tabela = raiz.find(f".//{_DMN}decisionTable")
    assert tabela is not None, "decisionTable nao encontrada"

    entradas = tabela.findall(f"{_DMN}input")
    coluna = None
    for i, entrada in enumerate(entradas):
        expr = entrada.find(f"{_DMN}inputExpression/{_DMN}text")
        if expr is not None and (expr.text or "").strip() == "sintoma_codigo":
            coluna = i
            break
    assert coluna is not None, "coluna sintoma_codigo nao encontrada"

    achados: set[str] = set()
    for regra in tabela.findall(f"{_DMN}rule"):
        celulas = regra.findall(f"{_DMN}inputEntry")
        if coluna >= len(celulas):
            continue
        texto = celulas[coluna].find(f"{_DMN}text")
        bruto = (texto.text or "").strip() if texto is not None else ""
        if not bruto or bruto == "-":
            continue
        for pedaco in bruto.split(","):
            limpo = pedaco.strip().strip('"').strip()
            if limpo:
                achados.add(limpo)
    return achados


@pytest.mark.parametrize("populacao", sorted(TABELAS), ids=sorted(TABELAS))
def test_a_lista_do_prompt_e_as_regras_da_tabela_sao_o_mesmo_conjunto(populacao: str) -> None:
    """Identidade de conjunto, nas duas direcoes, por populacao."""
    caminho = RAIZ_DMN / TABELAS[populacao]
    na_tabela = codigos_da_tabela(caminho.read_text(encoding="utf-8"))
    na_lista = set(SINTOMA_CODIGOS_BY_POPULATION[populacao])

    sem_regra = sorted(na_lista - na_tabela)
    fora_da_lista = sorted(na_tabela - na_lista)

    assert not sem_regra, (
        f"{populacao}: codigo(s) no prompt SEM regra em {TABELAS[populacao]}: {sem_regra}. "
        "O modelo e' instruido a produzi-lo, o validador o aceita, e a tabela nao casa nada — "
        "o caso cai no vazio final sem bandeira."
    )
    assert not fora_da_lista, (
        f"{populacao}: codigo(s) na regra FORA do prompt em {TABELAS[populacao]}: "
        f"{fora_da_lista}. A regra nunca dispara, porque o modelo nunca produz esse codigo."
    )


def test_toda_populacao_do_prompt_tem_tabela() -> None:
    """Uma populacao nova no prompt sem tabela mapeada sairia da cerca em silencio."""
    assert set(SINTOMA_CODIGOS_BY_POPULATION) == set(TABELAS)


# --- Nao-vacuidade: a cerca tem de ACUSAR uma divergencia plantada -----------------------------

_TABELA_SINTETICA = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d" name="d" namespace="n">
  <decision id="dec" name="dec">
    <decisionTable hitPolicy="FIRST">
      <input id="i1"><inputExpression id="e1"><text>sintoma_codigo</text></inputExpression></input>
      <input id="i2"><inputExpression id="e2"><text>intensidade</text></inputExpression></input>
      <output id="o1" name="red_flag"/>
      <rule id="r1">
        <inputEntry id="c1"><text>"febre"</text></inputEntry>
        <inputEntry id="c2"><text>-</text></inputEntry>
        <outputEntry id="s1"><text>true</text></outputEntry>
      </rule>
      <rule id="r2">
        <inputEntry id="c3"><text>-</text></inputEntry>
        <inputEntry id="c4"><text>-</text></inputEntry>
        <outputEntry id="s2"><text>false</text></outputEntry>
      </rule>
    </decisionTable>
  </decision>
</definitions>
"""


def test_a_extracao_le_a_coluna_certa_e_ignora_o_coringa() -> None:
    assert codigos_da_tabela(_TABELA_SINTETICA) == {"febre"}


def test_a_cerca_acusa_codigo_da_lista_que_nao_tem_regra() -> None:
    """Mutacao RED, direcao 1: um codigo a mais na lista tem de ser acusado."""
    na_tabela = codigos_da_tabela(_TABELA_SINTETICA)
    lista_mutada = {"febre", "convulsao"}

    assert sorted(lista_mutada - na_tabela) == ["convulsao"]


def test_a_cerca_acusa_regra_com_codigo_fora_da_lista() -> None:
    """Mutacao RED, direcao 2: uma regra a mais na tabela tem de ser acusada."""
    mutada = _TABELA_SINTETICA.replace('<text>"febre"</text>', '<text>"letargia"</text>')
    na_tabela = codigos_da_tabela(mutada)

    assert sorted(na_tabela - {"febre"}) == ["letargia"]


def test_a_extracao_falha_alto_quando_a_coluna_some() -> None:
    """Renomear a coluna nao pode fazer a cerca passar a examinar zero codigos."""
    sem_coluna = _TABELA_SINTETICA.replace("<text>sintoma_codigo</text>", "<text>outra_coisa</text>")

    with pytest.raises(AssertionError, match="coluna sintoma_codigo"):
        codigos_da_tabela(sem_coluna)
