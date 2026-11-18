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
        "segmento": {"regiao": "SP", "etapa": "cotacao", "numero_de_pessoas": 2},
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


def _registrar(monkeypatch: pytest.MonkeyPatch, *camps: Campanha) -> None:
    monkeypatch.setattr(cp, "REGISTRO_DE_CAMPANHAS", tuple(camps))


async def test_recovery_send_refused_without_permission_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    camp = _campanha()
    _registrar(monkeypatch, camp)
    gate = gate_ratificado()
    d = await PortaoDeCampanha().avaliar_envio(camp, _pedido(permissao=None), gate=gate)
    assert not d.permitido and d.autorizacao is None
    assert d.motivo is Motivo.RECONTACT_PERMISSION_ABSENT
    assert "autorizado" not in gate.contagens


async def test_recovery_send_refused_when_list_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    camp = _campanha()
    _registrar(monkeypatch, camp)
    gate = gate_ratificado(supressao=SupressaoFixa(falha=True))
    d = await PortaoDeCampanha().avaliar_envio(camp, _pedido(), gate=gate)
    assert d.motivo is Motivo.SUPPRESSION_SOURCE_UNAVAILABLE and not d.permitido
    d2 = await PortaoDeCampanha().avaliar_envio(
        camp, _pedido(), gate=OutboundGate(registro_ratificado(), supressao=None)
    )
    assert d2.motivo is Motivo.SUPPRESSION_SOURCE_UNAVAILABLE


@pytest.mark.parametrize("proibido", CRITERIOS_PROIBIDOS)
async def test_idade_e_faixa_etaria_fora_de_qualquer_criterio(
    proibido: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = AsyncMock()
    camp = _campanha(segmento={"regiao": "SP", proibido: "x"})
    _registrar(monkeypatch, camp)
    with pytest.raises(CampanhaRecusadaError) as exc:
        await PortaoDeCampanha().avaliar_envio(camp, _pedido(), gate=gate)
    assert exc.value.motivo is MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED
    gate.avaliar.assert_not_called()


def test_lista_positiva_fechada(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        frozenset({"regiao", "modalidade", "numero_de_pessoas", "produto", "etapa"}) == CRITERIOS_PERMITIDOS
    )
    assert not set(CRITERIOS_PROIBIDOS) & CRITERIOS_PERMITIDOS
    portao = PortaoDeCampanha()
    ok = _campanha(
        segmento={"regiao": "SP", "modalidade": "familiar", "numero_de_pessoas": 3, "etapa": "proposta"}
    )
    _registrar(monkeypatch, ok)
    portao.admitir(ok)
    for ruim in (
        {"etapa": "retomada"},
        {"numero_de_pessoas": 0},
        {"numero_de_pessoas": True},
        {"regiao": "a b"},
        {"regiao": 5},
        {},
    ):
        with pytest.raises(CampanhaRecusadaError) as exc:
            ruim_camp = _campanha(segmento=ruim)
            _registrar(monkeypatch, ruim_camp)
            portao.admitir(ruim_camp)
        assert exc.value.motivo is MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED


def test_valor_de_criterio_com_saude_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    for seg in ({"produto": "diabetes"}, {"regiao": "CID J45"}):
        camp = _campanha(segmento=seg)
        _registrar(monkeypatch, camp)
        with pytest.raises(CampanhaRecusadaError) as exc:
            PortaoDeCampanha().admitir(camp)
        assert exc.value.motivo is MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED


@pytest.mark.parametrize(
    "seg",
    [
        {"produto": "plano_senior_60"},
        {"produto": "terceira_idade"},
        {"regiao": "idade_60"},
        {"regiao": "nascidos_1960"},
        {"modalidade": "faixa_59"},
        {"produto": "gestante"},
        {"modalidade": "pcd"},
        {"regiao": "oncologia"},
    ],
)
async def test_idade_e_saude_nao_entram_pelo_valor(
    seg: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    camp = _campanha(segmento=seg)
    _registrar(monkeypatch, camp)
    gate = AsyncMock()
    with pytest.raises(CampanhaRecusadaError) as exc:
        await PortaoDeCampanha().avaliar_envio(camp, _pedido(), gate=gate)
    assert exc.value.motivo is MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED
    gate.avaliar.assert_not_called()


def test_campanha_parada_por_padrao_e_registro_vazio(monkeypatch: pytest.MonkeyPatch) -> None:
    assert REGISTRO_DE_CAMPANHAS == ()
    c = Campanha("c", "f", {"regiao": "SP"}, "aprov")
    assert c.parada is True
    _registrar(monkeypatch, c)
    with pytest.raises(CampanhaRecusadaError) as exc:
        PortaoDeCampanha().admitir(c)
    assert exc.value.motivo is MotivoCampanha.CAMPAIGN_STOPPED


@pytest.mark.parametrize("ref", [None, ""])
def test_sem_aprovacao_humana_recusa(ref: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    camp = _campanha(aprovacao_humana_ref=ref)
    _registrar(monkeypatch, camp)
    with pytest.raises(CampanhaRecusadaError) as exc:
        PortaoDeCampanha().admitir(camp)
    assert exc.value.motivo is MotivoCampanha.CAMPAIGN_APPROVAL_MISSING


async def test_portao_admite_antes_do_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = AsyncMock()
    boa = _campanha()
    _registrar(monkeypatch, boa, _campanha(parada=True), _campanha(aprovacao_humana_ref=None))
    for camp in (_campanha(parada=True), _campanha(aprovacao_humana_ref=None)):
        with pytest.raises(CampanhaRecusadaError):
            await PortaoDeCampanha().avaliar_envio(camp, _pedido(), gate=gate)
    gate.avaliar.assert_not_called()
    await PortaoDeCampanha().avaliar_envio(boa, _pedido(), gate=gate)
    gate.avaliar.assert_awaited_once()


async def test_campanha_fora_do_registro_recusa() -> None:
    gate = AsyncMock()
    with pytest.raises(CampanhaRecusadaError) as exc:
        await PortaoDeCampanha().avaliar_envio(_campanha(), _pedido(), gate=gate)
    assert exc.value.motivo is MotivoCampanha.CAMPAIGN_NOT_REGISTERED
    gate.avaliar.assert_not_called()


async def test_finalidade_divergente_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    camp = _campanha()
    _registrar(monkeypatch, camp)
    gate = AsyncMock()
    with pytest.raises(CampanhaRecusadaError) as exc:
        await PortaoDeCampanha().avaliar_envio(camp, _pedido(finalidade="outra_finalidade"), gate=gate)
    assert exc.value.motivo is MotivoCampanha.CAMPAIGN_PURPOSE_MISMATCH
    gate.avaliar.assert_not_called()


def test_nada_montado() -> None:
    alvo = Path(cp.__file__).resolve()
    for py in (_RAIZ / "src").rglob("*.py"):
        if py.resolve() == alvo:
            continue
        txt = py.read_text(encoding="utf-8")
        assert "communications.campanha" not in txt and "import campanha" not in txt, py
        assert "from .campanha" not in txt and "from . import campanha" not in txt, py
