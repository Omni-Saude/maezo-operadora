"""MPTT P5 — as tres decisoes avaliadas pelo MOTOR REAL (CIB Seven, ADR-0011/ADR-0028), nunca por mock.

O leitor de teste de `tests/unit/spec/test_prestador_trust_tier_dmn.py` prova a forma e a
aritmetica sem motor; ESTE arquivo prova o que so o motor responde:

  - `prestador_trust_scorecard` (COLLECT + SUM) devolve UMA linha com `pontuacao_confianca`
    inteira — o motor aceita a agregacao sobre a saida unica;
  - o teste unario FEEL `null` casa a entrada PRESENTE E NULA (o leitor de unidade assume isso;
    aqui o motor confirma) — e e isso que torna o tier fail-closed;
  - intervalos semiabertos (`[0.80..0.90)`) e a disjuncao `< 0, > 1` sao lidos como o leitor le;
  - `prestador_trust_motivos` (COLLECT sem agregacao) devolve uma linha por feature.

Os esperados abaixo sao DERIVADOS da tabela (mesmos cenarios do teste de unidade), nao de uma
corrida anterior do motor: se o motor discordar, a divergencia e o achado.
Sem motor alcancavel, o pacote inteiro PULA com motivo explicito (`tests/integration/conftest.py`).
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


async def _rows(dmn: CibSevenDmnTransport, key: str, variables: dict[str, Any]) -> list[dict[str, Any]]:
    rows, version = await dmn.evaluate(key, variables)
    assert isinstance(version, DmnVersion)
    assert version.key == key
    return rows


def _sc(
    ordens: Any = 50, taxa: Any = 0.95, lotes: Any = 50, percentil: Any = 30.0, meses: Any = 24
) -> dict[str, Any]:
    return {
        "f1_ordens_decididas": ordens,
        "f1_taxa_liberacao_humana": taxa,
        "f2_lotes_janela": lotes,
        "f2_percentil_glosa_liquida_pares_tenant": percentil,
        "f5_meses_relacionamento": meses,
    }


def _ti(**sobrescritas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pontuacao_confianca": 85,
        "f1_ordens_decididas": 50,
        "f1_taxa_liberacao_humana": 0.95,
        "f5_meses_relacionamento": 24,
        "pagamentos_liberados_humanos": 40,
        "red_flag_pico_faturamento": False,
        "fraude_monitorar_12m": False,
    }
    base.update(sobrescritas)
    return base


@pytest.mark.parametrize(
    ("variaveis", "esperado"),
    [
        (_sc(), 85),
        (_sc(None, None, None, None, None), 0),
        (_sc(0, None, 0, None, 0), 35),
        (_sc(ordens=9, taxa=1.0), 65),
        (_sc(taxa=0.8999), 65),
        (_sc(ordens=10, taxa=0.9), 85),
        (_sc(percentil=60.0), 70),
        (_sc(percentil=90.0), 55),
        (_sc(meses=11), 70),
        (_sc(taxa=1.2), 45),
    ],
)
async def test_scorecard_collect_sum_no_motor(
    dmn: CibSevenDmnTransport, variaveis: dict[str, Any], esperado: int
) -> None:
    rows = await _rows(dmn, "prestador_trust_scorecard", variaveis)
    assert len(rows) == 1, rows
    assert rows[0]["pontuacao_confianca"] == esperado


async def test_motivos_uma_linha_por_feature_no_motor(dmn: CibSevenDmnTransport) -> None:
    rows = await _rows(dmn, "prestador_trust_motivos", _sc(ordens=50, taxa=0.85, percentil=90.0, meses=3))
    assert sorted((r["motivo_codigo"], r["pontos_perdidos"]) for r in rows) == [
        ("F1_LIBERACAO_ABAIXO_DE_90", 20),
        ("F2_GLOSA_LIQUIDA_ACIMA_P75", 30),
        ("F5_RELACIONAMENTO_CURTO", 15),
    ]


@pytest.mark.parametrize(
    ("variaveis", "tier", "motivo"),
    [
        (_ti(), "TRUSTED", "GOLD_CARD_ATINGIDO"),
        (_ti(pontuacao_confianca=None), "WATCH", "ENTRADA_AUSENTE_PONTUACAO"),
        (_ti(red_flag_pico_faturamento=None), "WATCH", "ENTRADA_AUSENTE_RED_FLAG_PICO"),
        (_ti(fraude_monitorar_12m=True), "WATCH", "RED_FLAG_FRAUDE_MONITORAR_12M"),
        (_ti(red_flag_pico_faturamento=True), "WATCH", "RED_FLAG_PICO_FATURAMENTO"),
        (_ti(pagamentos_liberados_humanos=9), "WATCH", "PRESTADOR_NOVO_OU_BAIXO_VOLUME"),
        (_ti(pontuacao_confianca=86), "WATCH", "PONTUACAO_FORA_DA_ESCALA"),
        (_ti(f1_ordens_decididas=9, f1_taxa_liberacao_humana=1.0), "STANDARD", "PONTUACAO_STANDARD"),
        (_ti(f1_taxa_liberacao_humana=0.8999), "STANDARD", "PONTUACAO_STANDARD"),
        (_ti(pontuacao_confianca=39), "WATCH", "PONTUACAO_ABAIXO_DO_CORTE_WATCH"),
    ],
)
async def test_tier_first_fail_closed_no_motor(
    dmn: CibSevenDmnTransport, variaveis: dict[str, Any], tier: str, motivo: str
) -> None:
    rows = await _rows(dmn, "prestador_tier", variaveis)
    assert rows, "FIRST sem linha casada — a tabela tem catch-all"
    assert (rows[0]["prestador_tier"], rows[0]["motivo_tier"]) == (tier, motivo)
