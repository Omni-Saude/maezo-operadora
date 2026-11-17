"""E-SF-15 — portao de campanha (C9), variante fechada. Registro/aceite do gate sao SINTETICOS."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from maezo.gateway.communications import campanha as cp
from maezo.gateway.communications.campanha import (
    CRITERIOS_PERMITIDOS,
    CRITERIOS_PROIBIDOS,
    REGISTRO_DE_CAMPANHAS,
    Campanha,
    CampanhaRecusadaError,
    MotivoCampanha,
    PortaoDeCampanha,
)
from maezo.gateway.communications.outbound_gate import Motivo, OutboundGate, PedidoDeEnvio
from tests.unit.esf13_apoio import (
    FINALIDADE_LEAD,
    TEMPLATE_LEAD,
    SupressaoFixa,
    aceite_os05_sintetico,
    gate_ratificado,
    registro_ratificado,
)

_RAIZ = Path(__file__).resolve().parents[3]


def _campanha(**kw: Any) -> Campanha:
    base: dict[str, Any] = {
        "campanha_ref": "camp-1",
        "finalidade": FINALIDADE_LEAD,
        "segmento": {"regiao": "sp", "etapa": "cotacao", "numero_de_pessoas": 2},
        "aprovacao_humana_ref": "aprov-1",
        "parada": False,
    }
    base.update(kw)
    return Campanha(**base)


def _pedido(**kw: Any) -> PedidoDeEnvio:
    base: dict[str, Any] = {
        "template_id": TEMPLATE_LEAD,
        "finalidade": FINALIDADE_LEAD,
        "tipo_titular": "lead",
        "titular_ref": "prospect-1",
        "canal_ref": "canal-1",
        "variaveis": ("1",),
        "permissao": aceite_os05_sintetico(),
        "chave_idempotencia": "k1",
    }
    base.update(kw)
    return PedidoDeEnvio(**base)


async def test_recovery_send_refused_without_permission_ref() -> None:
    gate = gate_ratificado()
    d = await PortaoDeCampanha().avaliar_envio(_campanha(), _pedido(permissao=None), gate=gate)
    assert not d.permitido and d.autorizacao is None
    assert d.motivo is Motivo.RECONTACT_PERMISSION_ABSENT
    assert "autorizado" not in gate.contagens


async def test_recovery_send_refused_when_list_unknown() -> None:
    gate = gate_ratificado(supressao=SupressaoFixa(falha=True))
    d = await PortaoDeCampanha().avaliar_envio(_campanha(), _pedido(), gate=gate)
    assert d.motivo is Motivo.SUPPRESSION_SOURCE_UNAVAILABLE and not d.permitido
    d2 = await PortaoDeCampanha().avaliar_envio(
        _campanha(), _pedido(), gate=OutboundGate(registro_ratificado(), supressao=None)
    )
    assert d2.motivo is Motivo.SUPPRESSION_SOURCE_UNAVAILABLE


@pytest.mark.parametrize("proibido", CRITERIOS_PROIBIDOS)
async def test_idade_e_faixa_etaria_fora_de_qualquer_criterio(proibido: str) -> None:
    gate = AsyncMock()
    camp = _campanha(segmento={"regiao": "sp", proibido: "x"})
    with pytest.raises(CampanhaRecusadaError) as exc:
        await PortaoDeCampanha().avaliar_envio(camp, _pedido(), gate=gate)
    assert exc.value.motivo is MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED
    gate.avaliar.assert_not_called()


def test_lista_positiva_fechada() -> None:
    assert (
        frozenset({"regiao", "modalidade", "numero_de_pessoas", "produto", "etapa"}) == CRITERIOS_PERMITIDOS
    )
    assert not set(CRITERIOS_PROIBIDOS) & CRITERIOS_PERMITIDOS
    portao = PortaoDeCampanha()
    portao.admitir(
        _campanha(
            segmento={
                "regiao": "sp",
                "modalidade": "pme",
                "numero_de_pessoas": 3,
                "produto": "plano-a",
                "etapa": "proposta",
            }
        )
    )
    for ruim in (
        {"etapa": "retomada"},
        {"numero_de_pessoas": 0},
        {"numero_de_pessoas": True},
        {"regiao": "a b"},
        {"regiao": 5},
        {},
    ):
        with pytest.raises(CampanhaRecusadaError) as exc:
            portao.admitir(_campanha(segmento=ruim))
        assert exc.value.motivo is MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED


def test_valor_de_criterio_com_saude_recusado() -> None:
    for seg in ({"produto": "diabetes"}, {"regiao": "CID J45"}):
        with pytest.raises(CampanhaRecusadaError) as exc:
            PortaoDeCampanha().admitir(_campanha(segmento=seg))
        assert exc.value.motivo is MotivoCampanha.PHI_IN_COMMERCIAL_INPUT


def test_campanha_parada_por_padrao_e_registro_vazio() -> None:
    assert REGISTRO_DE_CAMPANHAS == ()
    c = Campanha("c", "f", {"regiao": "sp"}, "aprov")
    assert c.parada is True
    with pytest.raises(CampanhaRecusadaError) as exc:
        PortaoDeCampanha().admitir(c)
    assert exc.value.motivo is MotivoCampanha.CAMPAIGN_STOPPED


@pytest.mark.parametrize("ref", [None, ""])
def test_sem_aprovacao_humana_recusa(ref: str | None) -> None:
    with pytest.raises(CampanhaRecusadaError) as exc:
        PortaoDeCampanha().admitir(_campanha(aprovacao_humana_ref=ref))
    assert exc.value.motivo is MotivoCampanha.CAMPAIGN_APPROVAL_MISSING


async def test_portao_admite_antes_do_gate() -> None:
    gate = AsyncMock()
    for camp in (_campanha(parada=True), _campanha(aprovacao_humana_ref=None)):
        with pytest.raises(CampanhaRecusadaError):
            await PortaoDeCampanha().avaliar_envio(camp, _pedido(), gate=gate)
    gate.avaliar.assert_not_called()
    await PortaoDeCampanha().avaliar_envio(_campanha(), _pedido(), gate=gate)
    gate.avaliar.assert_awaited_once()


def test_nada_montado() -> None:
    alvo = Path(cp.__file__).resolve()
    for py in (_RAIZ / "src").rglob("*.py"):
        if py.resolve() == alvo:
            continue
        txt = py.read_text(encoding="utf-8")
        assert "communications.campanha" not in txt and "import campanha" not in txt, py
        assert "from .campanha" not in txt and "from . import campanha" not in txt, py
