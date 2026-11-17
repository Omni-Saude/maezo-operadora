"""`auth_sla` no MOTOR REAL (CIB Seven, ADR-0011/ADR-0028): carater nao classificado recebe o prazo de r1.

Decisao do coordenador (17/11/2026, em nome do dono): o catch-all r4 (FIRST) devolve os MESMOS
prazos da urgencia (r1) — `sla_analise` PT2H, `sla_alerta` PT1H, `prazo_resposta_dias_uteis` nulo —
para carater nulo, vazio ou desconhecido, em qualquer categoria. Dado ausente nunca cai no
tratamento mais lento. Urgencia e eletivo seguem como antes.

O leitor sem motor de `tests/unit/spec/test_auth_sla_dmn.py` prova a mesma matriz sobre o XML;
ESTE arquivo prova o que so o motor responde: que a entrada PRESENTE E NULA e a string vazia nao
casam o literal `"urgencia"`/`"eletivo"` e caem no catch-all, e que a saida `integer` nula volta
nula. A tabela avaliada e a de `spec/processes/dmn/auth_sla.dmn`, publicada pelo
`_deploy_spec_artifacts` de `tests/integration/dmn/conftest.py`. Os esperados sao DERIVADOS da
tabela, nao de uma corrida anterior do motor: se o motor discordar, a divergencia e o achado.
Sem motor alcancavel, o pacote inteiro PULA com motivo explicito (`tests/integration/conftest.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnVersion

pytestmark = pytest.mark.integration

_KEY = "auth_sla"
_PRAZOS = ("sla_analise", "sla_alerta", "prazo_resposta_dias_uteis")
#: Saidas de prazo de r1 (urgencia), copiadas para r4.
_R1 = {"sla_analise": "PT2H", "sla_alerta": "PT1H", "prazo_resposta_dias_uteis": None}
_R2 = {"sla_analise": "P10D", "sla_alerta": "P7D", "prazo_resposta_dias_uteis": 10}
_R3 = {"sla_analise": "P5D", "sla_alerta": "P3D", "prazo_resposta_dias_uteis": 5}

_CATEGORIAS = ("consulta", "exame", "terapia", "alta_complexidade", "opme", "internacao", "", None)


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


async def _linha(dmn: CibSevenDmnTransport, carater: str | None, categoria: str | None) -> dict[str, Any]:
    rows, version = await dmn.evaluate(
        _KEY, {"carater_atendimento": carater, "categoria_procedimento": categoria}
    )
    assert isinstance(version, DmnVersion)
    assert version.key == _KEY
    assert len(rows) == 1, rows  # FIRST: exatamente uma linha vencedora
    return rows[0]


def _prazos(linha: dict[str, Any]) -> dict[str, Any]:
    return {k: linha.get(k) for k in _PRAZOS}


@pytest.mark.parametrize("categoria", _CATEGORIAS)
@pytest.mark.parametrize("carater", [None, "", "emergencia"])
async def test_carater_nao_classificado_recebe_os_prazos_de_r1(
    dmn: CibSevenDmnTransport, carater: str | None, categoria: str | None
) -> None:
    linha = await _linha(dmn, carater, categoria)
    assert _prazos(linha) == _R1, linha
    assert str(linha["fonte_regulatoria"]).startswith("Fail-safe:"), linha


@pytest.mark.parametrize("categoria", _CATEGORIAS)
async def test_urgencia_inalterada(dmn: CibSevenDmnTransport, categoria: str | None) -> None:
    linha = await _linha(dmn, "urgencia", categoria)
    assert _prazos(linha) == _R1, linha
    assert str(linha["fonte_regulatoria"]).startswith("Lei 9.656/1998 art. 35-C"), linha


@pytest.mark.parametrize(
    ("categoria", "esperado"),
    [
        ("alta_complexidade", _R2),
        ("opme", _R2),
        ("internacao", _R2),
        ("consulta", _R3),
        ("exame", _R3),
        ("terapia", _R3),
        ("", _R3),
        (None, _R3),
    ],
)
async def test_eletivo_inalterado(
    dmn: CibSevenDmnTransport, categoria: str | None, esperado: dict[str, Any]
) -> None:
    linha = await _linha(dmn, "eletivo", categoria)
    assert _prazos(linha) == esperado, linha
