"""DEC-31 D31-2 — `pagto_comite_coordenacao` avaliada pelo MOTOR REAL (CIB Seven, ADR-0028), nunca por mock.

Decisao do dono (#1084, 2026-11-02; refina a DEC-30 D3): "a coordinator can take over if one member
voted yes and the other did not answer before deadline". A cerca estatica
(`tests/unit/spec/test_sp_op_pagto_001_comite_colegiado_fence.py`) prova a forma da tabela; ESTE
arquivo prova o que so o motor responde: a precedencia FIRST, os testes unarios `1`/`0`/`> 0`/`> 1`
contra inteiros (inclusive `Long`, como o JUEL dos listeners grava os contadores), e o catch-all
fail-closed para fato ausente/nulo.

Os esperados sao DERIVADOS da decisao do dono, nao de uma corrida anterior do motor: se o motor
discordar, a divergencia e o achado. Sem motor alcancavel o pacote inteiro PULA com motivo explicito.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnVersion

pytestmark = pytest.mark.integration


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


def _fatos(sla: Any = True, aprovar: Any = 1, nao_aprovar: Any = 0) -> dict[str, Any]:
    return {
        "comite_sla_estourado": sla,
        "comite_votos_aprovar": aprovar,
        "comite_votos_nao_aprovar": nao_aprovar,
    }


@pytest.mark.parametrize(
    ("variaveis", "assuncao", "motivo"),
    [
        # A regra do dono: SLA estourado, exatamente 1 APROVAR, nenhuma recusa.
        (_fatos(), "PERMITIDA", "SLA_ESTOURADO_UM_APROVAR_SEM_RECUSA"),
        # Qualquer voto que nao aprove (recusa, cancelamento, pedido de info, vazio) mantem a recusa.
        (_fatos(nao_aprovar=1), "RECUSADA", "VOTO_NAO_APROVAR_NA_RODADA"),
        (_fatos(aprovar=0, nao_aprovar=1), "RECUSADA", "VOTO_NAO_APROVAR_NA_RODADA"),
        (_fatos(sla=False, nao_aprovar=2), "RECUSADA", "VOTO_NAO_APROVAR_NA_RODADA"),
        # Zero votos: a coordenacao nunca libera sozinha.
        (_fatos(aprovar=0), "RECUSADA", "NENHUM_VOTO_APROVAR"),
        # SLA nao estourado: a coordenacao nao assume o comite.
        (_fatos(sla=False), "RECUSADA", "SLA_COMITE_NAO_ESTOURADO"),
        # Dois APROVAR: e o caminho do comite, nao a assuncao.
        (_fatos(aprovar=2), "RECUSADA", "MAIS_DE_UM_VOTO_APROVAR"),
        # Fail-closed: fato ausente/nulo nunca vira PERMITIDA.
        (_fatos(sla=None), "RECUSADA", "FATOS_DA_RODADA_AUSENTES"),
        (_fatos(aprovar=None), "RECUSADA", "FATOS_DA_RODADA_AUSENTES"),
        (_fatos(nao_aprovar=None), "RECUSADA", "FATOS_DA_RODADA_AUSENTES"),
        (_fatos(None, None, None), "RECUSADA", "FATOS_DA_RODADA_AUSENTES"),
    ],
)
async def test_pagto_comite_coordenacao_first_fail_closed_no_motor(
    dmn: CibSevenDmnTransport, variaveis: dict[str, Any], assuncao: str, motivo: str
) -> None:
    rows, version = await dmn.evaluate("pagto_comite_coordenacao", variaveis)
    assert isinstance(version, DmnVersion) and version.key == "pagto_comite_coordenacao"
    assert rows, "FIRST sem linha casada — a tabela tem catch-all"
    assert (rows[0]["assuncao_comite"], rows[0]["motivo_assuncao_comite"]) == (assuncao, motivo)


async def test_pagto_comite_coordenacao_contadores_long_no_motor(dmn: CibSevenDmnTransport) -> None:
    """Os listeners JUEL gravam os contadores como `Long`; a tabela (typeRef integer) os aceita."""
    variaveis = {
        "comite_sla_estourado": {"value": True, "type": "Boolean"},
        "comite_votos_aprovar": {"value": 1, "type": "Long"},
        "comite_votos_nao_aprovar": {"value": 0, "type": "Long"},
    }
    rows, _version = await dmn.evaluate("pagto_comite_coordenacao", variaveis)
    assert rows and rows[0]["assuncao_comite"] == "PERMITIDA"
