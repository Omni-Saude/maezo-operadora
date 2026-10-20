"""`ConsultasPlanoAmh.consultar_cpt` (rota `tina-cpt`, manifest v1.15; caso C1 do dono e DPO de 20/10/2026,
DL-0130).

NUNCA levanta: qualquer recusa, 404, 429, prazo, falta de base legal, rota nao carregada ou resposta de OUTRO
sujeito vira `None`. O log so' leva o motivo (token fechado), nunca o conteudo da CPT nem a ref. Dados 100%
sinteticos.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
import structlog

from maezo.agents.helena.consultas_plano import FonteDeCpt
from maezo.platform.webhooks.whatsapp import helena_consultas
from maezo.platform.webhooks.whatsapp.helena_consultas import ConsultasPlanoAmh
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.ports.tina import CptVigenteView

pytestmark = pytest.mark.anyio

_REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_OUTRO = "amh:psr:v1:2b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _view(ref: str = _REF) -> CptVigenteView:
    return CptVigenteView(
        portable_subject_ref=ref,
        tem_cpt_vigente=True,
        cpt_vigentes=1,
        termina_em="2027-03-01",
        fonte_atualizada_em="2026-10-20T12:00:00Z",
    )


class _Consentimento:
    def __init__(self, base_legal: str | None = "base-legal-do-teste") -> None:
        self.base_legal = base_legal
        self.finalidades: list[str] = []

    async def decisao(self, ref: str, finalidade: str) -> str | None:
        self.finalidades.append(finalidade)
        return self.base_legal


class _Porta:
    def __init__(self, resultado: Any = None, *, atraso: float = 0.0, erro: Exception | None = None) -> None:
        self.resultado = resultado if resultado is not None else PortResult.ok(_view())
        self.atraso = atraso
        self.erro = erro
        self.chamadas: list[dict[str, Any]] = []

    async def get_elegibilidade(self, *_: Any, **__: Any) -> Any:  # pragma: no cover
        raise AssertionError("nao consultado")

    async def get_cpt_vigente(self, ref: str, **kwargs: Any) -> Any:
        self.chamadas.append({"ref": ref, **kwargs})
        if self.atraso:
            await asyncio.sleep(self.atraso)
        if self.erro is not None:
            raise self.erro
        return self.resultado


class _PortaSemCpt:
    async def get_elegibilidade(self, *_: Any, **__: Any) -> Any:  # pragma: no cover
        raise AssertionError("nao consultado")


def _consultas(porta: Any, consentimento: _Consentimento | None = None) -> ConsultasPlanoAmh:
    return ConsultasPlanoAmh(
        port=cast(Any, porta),
        consentimento=cast(Any, consentimento or _Consentimento()),
        purpose_of_use="sharing_amh_internal",
    )


def test_implementa_o_protocolo_da_fonte() -> None:
    assert isinstance(_consultas(_Porta()), FonteDeCpt)


async def test_sucesso_devolve_a_view_com_a_finalidade_fixa() -> None:
    porta = _Porta()
    consentimento = _Consentimento()
    with structlog.testing.capture_logs() as logs:
        view = await _consultas(porta, consentimento).consultar_cpt(_REF)
    assert view == _view()
    [chamada] = porta.chamadas
    assert chamada["purpose_of_use"] == "atendimento_whatsapp"
    assert chamada["consent_decision_ref"] == "base-legal-do-teste"
    assert consentimento.finalidades == ["atendimento_whatsapp"]
    [linha] = [e for e in logs if e["event"] == "helena_cpt_lida"]
    assert linha == {"event": "helena_cpt_lida", "log_level": "info", "motivo": "ok"}
    assert _REF not in repr(logs) and "2027" not in repr(logs)


@pytest.mark.parametrize(
    ("porta", "motivo"),
    [
        (_PortaSemCpt(), "rota_nao_carregada"),
        (_Porta(PortResult.refused(PortFailureReason.SCOPE_NOT_SUPPORTED)), "scope_not_supported"),
        (_Porta(PortResult.refused(PortFailureReason.NOT_FOUND)), "not_found"),
        (_Porta(PortResult.refused(PortFailureReason.RATE_LIMITED)), "rate_limited"),
        (_Porta(PortResult.refused(PortFailureReason.CONTRACT_VIOLATION)), "contract_violation"),
        (_Porta(PortResult.ok(_view(_OUTRO))), "fatos_de_outro_sujeito"),
    ],
)
async def test_falha_vira_none_e_so_o_motivo_vai_a_log(porta: Any, motivo: str) -> None:
    with structlog.testing.capture_logs() as logs:
        view = await _consultas(porta).consultar_cpt(_REF)
    assert view is None
    [linha] = [e for e in logs if e["event"] == "helena_cpt_lida"]
    assert linha["motivo"] == motivo
    assert "2027" not in repr(logs)


async def test_sem_base_legal_nao_consulta() -> None:
    porta = _Porta()
    view = await _consultas(porta, _Consentimento(None)).consultar_cpt(_REF)
    assert view is None and porta.chamadas == []


async def test_excecao_do_port_vira_none() -> None:
    with structlog.testing.capture_logs() as logs:
        view = await _consultas(_Porta(erro=RuntimeError("detalhe-sensivel"))).consultar_cpt(_REF)
    assert view is None
    assert "detalhe-sensivel" not in repr(logs)


async def test_prazo_estourado_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helena_consultas, "PRAZO_TOTAL_CPT_S", 0.01)
    with structlog.testing.capture_logs() as logs:
        view = await _consultas(_Porta(atraso=1.0)).consultar_cpt(_REF)
    assert view is None
    [linha] = [e for e in logs if e["event"] == "helena_cpt_lida"]
    assert linha["motivo"] == "prazo_estourado"
