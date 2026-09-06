"""Unit tests for `tests/integration/agents/_engine_helpers.py::mapa_de_variavel_estruturada` —
the shape-tolerant reader of a STRUCTURED engine process variable (§Delta-3, regressao P-12).

Mesma disciplina de `test_engine_rest_vars.py` neste diretorio: `_engine_helpers.py` e' um wrapper
httpx auto-contido (importa so' `asyncio`/`json`/`httpx`), entao a parte PURA dele e' exercitada
aqui, sem docker-compose. ADR-0011 governa a lane de INTEGRACAO (motor real, nunca mockado); isto
nao mocka motor nenhum — sao as tres FORMAS DE FIO que o motor pode devolver para a mesma variavel,
escritas como literais.

POR QUE ISTO EXISTE. A assercao que consome esta funcao roda contra o motor
(`test_helena_escalation.py::_assert_falha_tecnica_roteada_pela_r6_com_severidade_nula`), e a
rodada de motor nao e' do autor deste conserto. Esta funcao e' o UNICO pedaco daquela assercao cujo
comportamento nao da' para provar sem o motor — e e' justamente onde um erro passaria despercebido,
porque a forma errada tambem e' um `dict`. As tres formas:

  1. `Object` desserializado  -> `{"type": "Object", "value": {"grupo_atendimento": ...}}`
     (o caso de `roteamento`, gravada por `camunda:mapDecisionResult="singleResult"`);
  2. `Json`/SPIN com `deserializeValue=false` -> `value` e' a STRING JSON crua;
  3. `Json`/SPIN com o DEFAULT do endpoint -> `value` e' a INTROSPECAO do bean `SpinJsonNode`
     (`{'array': True, 'nodeType': ...}`), um dict que NAO e' o dado — a armadilha que
     `tests/integration/processes/engine_rest.py::get_variable` documenta como live-confirmada.

(3) TEM de ser recusada: aceita, a assercao morreria depois por `KeyError` dizendo a coisa errada —
exatamente o modo de falha que este §Delta-3 esta consertando (a primeira redacao da assercao leu
`grupo_atendimento` de um lugar onde ele nao esta e morreu com `KeyError: 'grupo_atendimento'`).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.integration.agents._engine_helpers import mapa_de_variavel_estruturada

#: As saidas que a regra `r6` da `escalation_routing` emite — as chaves que o chamador exige.
_SAIDAS_R6: set[str] = {"prioridade", "grupo_atendimento", "sla_ack", "sla_resolucao"}

#: O mapa real, como o motor o produz para `falha_tecnica` (DMN `r6`).
_ROTEAMENTO: dict[str, Any] = {
    "prioridade": "P3",
    "grupo_atendimento": "atendimento-humano",
    "sla_ack": "PT4H",
    "sla_resolucao": "PT24H",
}

#: A introspecao do bean SPIN que o DEFAULT do endpoint devolve para uma variavel `Json` — um dict
#: SEM nenhuma das chaves de dados (engine_rest.py::get_variable, live-confirmado).
_BEAN_SPIN: dict[str, Any] = {
    "array": False,
    "nodeType": "OBJECT",
    "dataFormatName": "application/json",
    "value": True,
    "object": True,
}


def test_aceita_object_ja_desserializado() -> None:
    """Forma (1): `value` ja' e' o objeto JSON. E' a forma esperada de `roteamento`."""
    entrada = {"type": "Object", "value": dict(_ROTEAMENTO), "valueInfo": {}}
    assert mapa_de_variavel_estruturada(entrada, _SAIDAS_R6) == _ROTEAMENTO


def test_aceita_string_json_crua() -> None:
    """Forma (2): `value` e' a STRING JSON (`deserializeValue=false`)."""
    entrada = {"type": "Json", "value": json.dumps(_ROTEAMENTO), "valueInfo": {}}
    assert mapa_de_variavel_estruturada(entrada, _SAIDAS_R6) == _ROTEAMENTO


def test_recusa_a_introspeccao_do_bean_spin() -> None:
    """Forma (3): um dict que NAO e' o dado. Recusado — este e' o teste que impede a funcao de
    devolver a armadilha e a assercao de morrer depois por `KeyError`."""
    entrada = {"type": "Json", "value": dict(_BEAN_SPIN), "valueInfo": {}}
    assert mapa_de_variavel_estruturada(entrada, _SAIDAS_R6) is None


def test_recusa_mapa_incompleto() -> None:
    """Um mapa a que falta UMA das chaves esperadas nao serve: a assercao pede as duas saidas da
    `r6` e um mapa parcial as leria pela metade."""
    parcial = {k: v for k, v in _ROTEAMENTO.items() if k != "prioridade"}
    entrada = {"type": "Object", "value": parcial, "valueInfo": {}}
    assert mapa_de_variavel_estruturada(entrada, _SAIDAS_R6) is None


@pytest.mark.parametrize(
    "valor",
    [
        "rO0ABXNyABFqYXZhLnV0aWwuSGFzaE1hcA==",  # base64 de objeto java-serializado
        "",
        "nao e json",
        None,
        42,
        ["atendimento-humano"],
    ],
)
def test_recusa_toda_forma_que_nao_carrega_o_mapa(valor: Any) -> None:
    """Base64 de objeto java-serializado, texto que nao e' JSON, vazio, nao-string e nao-dict: a
    funcao devolve `None` (nunca levanta), e o chamador falha com as duas formas observadas."""
    entrada = {"type": "Object", "value": valor, "valueInfo": {"objectTypeName": "java.util.HashMap"}}
    assert mapa_de_variavel_estruturada(entrada, _SAIDAS_R6) is None


def test_recusa_entrada_sem_value() -> None:
    """Uma entrada sem `value` nenhum (forma inesperada do motor) nao levanta `KeyError` aqui."""
    assert mapa_de_variavel_estruturada({"type": "Null"}, _SAIDAS_R6) is None


def test_string_json_de_lista_nao_passa_por_mapa() -> None:
    """JSON valido mas que nao e' objeto: recusado pelo `isinstance(valor, dict)`, nao por
    `KeyError` mais tarde."""
    entrada = {"type": "Json", "value": json.dumps(["atendimento-humano", "P3"])}
    assert mapa_de_variavel_estruturada(entrada, _SAIDAS_R6) is None


def test_chaves_extras_sao_toleradas() -> None:
    """A guarda e' `chaves_esperadas <= chaves`, nao igualdade: um motor que acrescente uma saida a
    tabela nao quebra a leitura (a assercao seguinte e' que compara valor a valor com a DMN viva)."""
    entrada = {"type": "Object", "value": {**_ROTEAMENTO, "sla_extra": "PT1H"}}
    mapa = mapa_de_variavel_estruturada(entrada, _SAIDAS_R6)
    assert mapa is not None
    assert mapa["grupo_atendimento"] == "atendimento-humano"
    assert mapa["prioridade"] == "P3"
