"""Programa do Lucas contra a DMN REAL no motor (plano lucas-numero-unico §6(a)).

O MESMO corpus do lane unitario (`tests/evals/lucas/casos.json`), o MESMO executor
(`tests/evals/lucas/programa.py`), mas com `CibSevenDmnTransport` avaliando
`lucas_billing_admissibility` e `lucas_escalation_routing` no CIB Seven — implantadas pelo
`conftest.py` deste pacote a partir de `spec/processes/dmn/`. Sem mock de motor (ADR-0011).

Duas coisas ficam provadas aqui e nao no lane unitario:
  1. o leitor local (`DmnDraftLocal`) e o motor concordam caso a caso: cada caso entrega o MESMO
     esperado e, alem disso, o mesmo registro de decisao que a rodada local;
  2. a proveniencia vem do motor: o `dmn_refs` de cada caso que avaliou DMN cita o id da definicao
     implantada, nunca o rotulo `draft-local`.

As DMN sao DRAFT: os esperados refletem o DRAFT ate o time aprovar (§6(a), risco declarado).
Motor fora do ar -> SKIP explicito pelo `conftest.py` raiz de integracao, nunca verde silencioso.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from tests.evals.lucas.programa import DmnDraftLocal, carregar_casos, executar_caso

pytestmark = pytest.mark.integration

CASOS = carregar_casos()

#: Campos de decisao que as duas rodadas (local x motor) tem de entregar iguais.
_CAMPOS_DE_DECISAO = (
    "route",
    "admissibilidade",
    "roteamento_escalacao",
    "motivo_humano",
    "motivo_categoria",
    "severidade",
    "grupo_humano",
    "desfecho",
    "process_started",
    "dmn_avaliadas",
    "envios",
)


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c["id"])
async def test_caso_no_motor_real(caso: dict[str, Any], dmn: CibSevenDmnTransport) -> None:
    registro = await executar_caso(caso, dmn=dmn)
    assert registro["divergencias"] == [], registro["divergencias"]

    local = await executar_caso(caso, dmn=DmnDraftLocal())
    assert {k: registro[k] for k in _CAMPOS_DE_DECISAO} == {k: local[k] for k in _CAMPOS_DE_DECISAO}

    for chave, ref in registro["dmn_refs"].items():
        assert ref.startswith(f"{chave}#"), ref
        assert "draft-local" not in ref, ref


async def test_todo_j3_escala_no_motor_real(dmn: CibSevenDmnTransport) -> None:
    j3 = [await executar_caso(c, dmn=dmn) for c in CASOS if c["jornada"] == "J3"]
    # 6 desde DL-0082 (08/10/2026): os 3 casos de `inadimplencia` sairam da J3 para a J2 — a pergunta
    # passa pela DMN de admissibilidade com os fatos, e so' a regra de atraso escala.
    assert len(j3) >= 6
    assert all(r["route"] == "escalate_human" for r in j3)
    assert all(r["decisao_cancelamento"] is None for r in j3)
    # Nao-vacuidade: a DMN de roteamento da escalacao RODOU no motor em todo J3.
    assert all("lucas_escalation_routing" in r["dmn_refs"] for r in j3)
