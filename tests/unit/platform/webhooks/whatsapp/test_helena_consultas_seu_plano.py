"""As fontes da camada "seu plano" da Helena (DL-0167, HEL-3; pedidos (a) e (c) da T-MZO-B a T-MZO-A).

A fonte que o receptor injeta (`ConsultasPlanoAmh`, a mesma `consultas_plano` que o despachante passa ao
grafo) implementa de novo `FonteDeCobertura` e `FonteDeCpt` e, nova, `FonteDoCatalogoDeProcedimentos`. So'
LEITURA pela rota pinada: o que fica preso aqui:

  * a fonte satisfaz os tres protocolos de `maezo.contratos.fatos_do_plano` (e o de sempre);
  * cobertura e CPT: finalidade FIXA `atendimento_whatsapp`, base legal de execucao de contrato, o limite e os
    prazos da rota; recusa, falta de base legal, outro sujeito, prazo ou excecao = `None`; port sem a rota =
    `None` sem tocar na base legal; o procedimento pedido nunca vai a log;
  * catalogo: todas as paginas da MESMA foto viram um `CatalogoDeProcedimentos`; versao que muda, recusa no
    meio, paginas demais ou prazo = `None` (catalogo parcial nunca sai); a foto boa e' reaproveitada e a falha
    nao fica guardada;
  * ponta a ponta sobre o adaptador TINA real com os BYTES PINADOS vendorizados (executor falso, sem rede).

Dados 100% sinteticos; codigos de procedimento `99xxxxxx` ficticios.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.adapters.amh import tina
from maezo.contratos.fatos_do_plano import (
    CatalogoDeProcedimentos,
    FonteDeCobertura,
    FonteDeCpt,
    FonteDeFatosDoPlano,
    FonteDoCatalogoDeProcedimentos,
)
from maezo.platform.webhooks.whatsapp import helena_consultas as hc
from maezo.platform.webhooks.whatsapp.helena_consultas import ConsultasPlanoAmh
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import (
    CatalogoProcedimentosView,
    CoberturaView,
    CptVigenteView,
    ProcedimentoCatalogo,
)
from tests.unit.adapters.amh.test_tina import Executor

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRO = "amh:psr:v1:2b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_BASE_LEGAL = "base-legal:execucao-de-contrato"
_VERSAO = "a" * 64
_VERSAO_NOVA = "b" * 64
_COBERTURA = CoberturaView(REF, "Ambulatorial", True, (), False, (), "2026-10-15T12:00:00Z")
_CPT = CptVigenteView(REF, False, 0, None, "2026-10-15T12:00:00Z")
_REAL = Path(__file__).resolve().parents[5] / "config" / "integrations" / "amh" / "openapi"


def _pagina(pagina: int, total: int, *codigos: str, versao: str = _VERSAO) -> CatalogoProcedimentosView:
    return CatalogoProcedimentosView(
        versao_catalogo=versao,
        pagina=pagina,
        total_paginas=total,
        total_itens=0,
        itens=tuple(ProcedimentoCatalogo(c, f"Procedimento {c}", None, None) for c in codigos),
        fonte_atualizada_em="2026-10-19T12:00:00Z",
    )


class _BaseLegal:
    def __init__(self, valor: str | None = _BASE_LEGAL) -> None:
        self.valor = valor
        self.pedidas: list[tuple[str, str]] = []

    async def decisao(self, ref: str, purpose: str) -> str | None:
        self.pedidas.append((ref, purpose))
        return self.valor


class _PortDeSempre:
    """So' o `TinaPort` de sempre: nenhuma das rotas da camada 2."""

    async def get_elegibilidade(self, ref: str, **kw: Any) -> Any:  # pragma: no cover - nao chamado
        raise AssertionError

    async def get_carencias(self, ref: str, **kw: Any) -> Any:  # pragma: no cover - nao chamado
        raise AssertionError

    async def get_requisicoes(self, ref: str, **kw: Any) -> Any:  # pragma: no cover - nao chamado
        raise AssertionError


class _PortCompleto(_PortDeSempre):
    def __init__(
        self,
        *,
        cobertura: Any = None,
        cpt: Any = None,
        paginas: list[Any] | None = None,
        atraso: float = 0.0,
        falha: Exception | None = None,
    ) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []
        self._cobertura = cobertura if cobertura is not None else PortResult.ok(_COBERTURA)
        self._cpt = cpt if cpt is not None else PortResult.ok(_CPT)
        self._paginas = paginas if paginas is not None else [PortResult.ok(_pagina(1, 1, "99000001"))]
        self._atraso = atraso
        self._falha = falha

    async def _r(self, nome: str, kw: dict[str, Any]) -> None:
        self.chamadas.append((nome, kw))
        await asyncio.sleep(self._atraso)
        if self._falha is not None:
            raise self._falha

    async def get_cobertura(self, ref: str, **kw: Any) -> Any:
        await self._r("cobertura", {"ref": ref, **kw})
        return self._cobertura

    async def get_cpt_vigente(self, ref: str, **kw: Any) -> Any:
        await self._r("cpt", {"ref": ref, **kw})
        return self._cpt

    async def get_catalogo_procedimentos(self, **kw: Any) -> Any:
        await self._r("catalogo", kw)
        return self._paginas[kw["pagina"] - 1]


def _fonte(port: Any, base_legal: _BaseLegal | None = None) -> ConsultasPlanoAmh:
    return ConsultasPlanoAmh(
        port=port,
        consentimento=base_legal or _BaseLegal(),
        purpose_of_use="sharing_amh_internal",
    )


# --- os protocolos ---------------------------------------------------------------------------------


def test_a_fonte_injetada_satisfaz_os_protocolos_da_camada_2() -> None:
    fonte = _fonte(_PortCompleto())
    assert isinstance(fonte, FonteDeFatosDoPlano)
    assert isinstance(fonte, FonteDeCobertura)
    assert isinstance(fonte, FonteDeCpt)
    assert isinstance(fonte, FonteDoCatalogoDeProcedimentos)


def test_a_helena_continua_exportando_os_protocolos() -> None:
    from maezo.agents.helena import consultas_plano

    assert consultas_plano.FonteDoCatalogoDeProcedimentos is FonteDoCatalogoDeProcedimentos
    assert consultas_plano.FonteDeCobertura is FonteDeCobertura
    assert consultas_plano.FonteDeCpt is FonteDeCpt


# --- cobertura -------------------------------------------------------------------------------------


async def test_cobertura_le_com_a_finalidade_fixa_a_base_legal_e_o_limite() -> None:
    port, base = _PortCompleto(), _BaseLegal()
    view = await _fonte(port, base).consultar_cobertura(REF, "99000001")
    assert view is _COBERTURA
    assert base.pedidas == [(REF, "atendimento_whatsapp")]
    [(nome, kw)] = port.chamadas
    assert nome == "cobertura"
    assert kw == {
        "ref": REF,
        "purpose_of_use": "atendimento_whatsapp",
        "consent_decision_ref": _BASE_LEGAL,
        "procedimento": "99000001",
        "limite": hc.LIMITE_COBERTURA,
        "timeout_seconds": hc.PRAZO_LEITURA_COBERTURA_S,
    }


@pytest.mark.parametrize(
    "resultado",
    [
        PortResult.refused(Reason.NOT_FOUND),
        PortResult.refused(Reason.SCOPE_NOT_SUPPORTED),
        PortResult.ok(CoberturaView(OUTRO, None, None, (), False, (), None)),
    ],
)
async def test_cobertura_recusa_ou_outro_sujeito_vira_none(resultado: Any) -> None:
    assert await _fonte(_PortCompleto(cobertura=resultado)).consultar_cobertura(REF, "99000001") is None


async def test_cobertura_sem_a_rota_nem_pede_a_base_legal() -> None:
    base = _BaseLegal()
    assert await _fonte(_PortDeSempre(), base).consultar_cobertura(REF, "99000001") is None
    assert base.pedidas == []


async def test_cobertura_sem_base_legal_nao_le() -> None:
    port = _PortCompleto()
    assert await _fonte(port, _BaseLegal(None)).consultar_cobertura(REF, "99000001") is None
    assert port.chamadas == []


async def test_cobertura_nunca_levanta_e_respeita_o_prazo(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _fonte(_PortCompleto(falha=RuntimeError("x"))).consultar_cobertura(REF, "1") is None
    monkeypatch.setattr(hc, "PRAZO_TOTAL_S", 0.01)
    assert await _fonte(_PortCompleto(atraso=1.0)).consultar_cobertura(REF, "1") is None


async def test_o_procedimento_pedido_nunca_vai_a_log() -> None:
    with structlog.testing.capture_logs() as logs:
        await _fonte(_PortCompleto()).consultar_cobertura(REF, "ressonancia magnetica de joelho")
    assert logs and all("joelho" not in repr(e) and REF not in repr(e) for e in logs)
    assert logs[-1]["event"] == "helena_cobertura_lida" and logs[-1]["motivo"] == "ok"


# --- CPT -------------------------------------------------------------------------------------------


async def test_cpt_le_com_a_finalidade_fixa_e_a_base_legal() -> None:
    port, base = _PortCompleto(), _BaseLegal()
    assert await _fonte(port, base).consultar_cpt(REF) is _CPT
    assert base.pedidas == [(REF, "atendimento_whatsapp")]
    [(nome, kw)] = port.chamadas
    assert nome == "cpt"
    assert kw == {
        "ref": REF,
        "purpose_of_use": "atendimento_whatsapp",
        "consent_decision_ref": _BASE_LEGAL,
        "timeout_seconds": hc.PRAZO_LEITURA_S,
    }


@pytest.mark.parametrize(
    "resultado",
    [
        PortResult.refused(Reason.RATE_LIMITED),
        PortResult.ok(CptVigenteView(OUTRO, False, 0, None, None)),
    ],
)
async def test_cpt_recusa_ou_outro_sujeito_vira_none(resultado: Any) -> None:
    assert await _fonte(_PortCompleto(cpt=resultado)).consultar_cpt(REF) is None


async def test_cpt_sem_a_rota_ou_com_falha_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _fonte(_PortDeSempre()).consultar_cpt(REF) is None
    assert await _fonte(_PortCompleto(falha=RuntimeError("x"))).consultar_cpt(REF) is None
    monkeypatch.setattr(hc, "PRAZO_TOTAL_CPT_S", 0.01)
    assert await _fonte(_PortCompleto(atraso=1.0)).consultar_cpt(REF) is None


# --- catalogo --------------------------------------------------------------------------------------


async def test_catalogo_junta_todas_as_paginas_da_mesma_foto() -> None:
    port = _PortCompleto(
        paginas=[
            PortResult.ok(_pagina(1, 2, "99000001", "99000002")),
            PortResult.ok(_pagina(2, 2, "99000003")),
        ]
    )
    catalogo = await _fonte(port).consultar_catalogo_procedimentos()
    assert isinstance(catalogo, CatalogoDeProcedimentos)
    assert catalogo.versao_catalogo == _VERSAO
    assert [i.codigo for i in catalogo.itens] == ["99000001", "99000002", "99000003"]
    assert [kw["pagina"] for _, kw in port.chamadas] == [1, 2]
    assert all(
        kw["purpose_of_use"] == "atendimento_whatsapp"
        and kw["consent_decision_ref"] == hc.BASE_LEGAL_DO_CATALOGO
        and kw["tamanho"] == hc.TAMANHO_PAGINA_CATALOGO
        for _, kw in port.chamadas
    )


async def test_catalogo_vazio_e_um_catalogo_e_nao_uma_falha() -> None:
    port = _PortCompleto(paginas=[PortResult.ok(_pagina(1, 0))])
    catalogo = await _fonte(port).consultar_catalogo_procedimentos()
    assert catalogo is not None and catalogo.itens == ()


@pytest.mark.parametrize(
    "paginas",
    [
        [
            PortResult.ok(_pagina(1, 2, "99000001")),
            PortResult.ok(_pagina(2, 2, "99000002", versao=_VERSAO_NOVA)),
        ],
        [PortResult.ok(_pagina(1, 2, "99000001")), PortResult.refused(Reason.TIMEOUT)],
        [PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)],
    ],
    ids=["versao_mudou", "recusa_no_meio", "rota_desligada"],
)
async def test_catalogo_parcial_nunca_sai(paginas: list[Any]) -> None:
    assert await _fonte(_PortCompleto(paginas=paginas)).consultar_catalogo_procedimentos() is None


async def test_catalogo_com_paginas_demais_nao_sai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hc, "MAX_PAGINAS_CATALOGO", 1)
    port = _PortCompleto(
        paginas=[PortResult.ok(_pagina(1, 2, "99000001")), PortResult.ok(_pagina(2, 2, "99000002"))]
    )
    assert await _fonte(port).consultar_catalogo_procedimentos() is None
    assert len(port.chamadas) == 1


async def test_catalogo_sem_a_rota_com_excecao_ou_prazo_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _fonte(_PortDeSempre()).consultar_catalogo_procedimentos() is None
    assert await _fonte(_PortCompleto(falha=RuntimeError("x"))).consultar_catalogo_procedimentos() is None
    monkeypatch.setattr(hc, "PRAZO_TOTAL_CATALOGO_S", 0.01)
    assert await _fonte(_PortCompleto(atraso=1.0)).consultar_catalogo_procedimentos() is None


async def test_a_foto_boa_e_reaproveitada_e_a_falha_nao_guarda(monkeypatch: pytest.MonkeyPatch) -> None:
    falhou = _fonte(_PortCompleto(paginas=[PortResult.refused(Reason.TIMEOUT)]))
    assert await falhou.consultar_catalogo_procedimentos() is None
    falhou.port = _PortCompleto()  # type: ignore[assignment]
    assert await falhou.consultar_catalogo_procedimentos() is not None

    port = _PortCompleto()
    fonte = _fonte(port)
    primeira = await fonte.consultar_catalogo_procedimentos()
    assert await fonte.consultar_catalogo_procedimentos() is primeira
    assert len(port.chamadas) == 1
    monkeypatch.setattr(hc, "VALIDADE_DO_CATALOGO_S", 0.0)
    await fonte.consultar_catalogo_procedimentos()
    assert len(port.chamadas) == 2


# --- ponta a ponta sobre o adaptador TINA real (bytes pinados, executor falso) ----------------------


def _adaptador() -> tuple[tina.AmhTinaAdapter, Executor]:
    executor = Executor()
    adapter = tina.AmhTinaAdapter(
        (_REAL / "tina.openapi.yaml").read_bytes(),
        executor=executor,
        cobertura_openapi_bytes=(_REAL / "tina-cobertura.openapi.yaml").read_bytes(),
        cpt_openapi_bytes=(_REAL / "tina-cpt.openapi.yaml").read_bytes(),
        procedimentos_openapi_bytes=(_REAL / "tina-procedimentos.openapi.yaml").read_bytes(),
    )
    return adapter, executor


async def test_ponta_a_ponta_sobre_o_adaptador_pinado() -> None:
    from tests.unit.adapters.amh.test_tina_cobertura import _corpo as corpo_cobertura
    from tests.unit.adapters.amh.test_tina_cpt import _corpo as corpo_cpt

    adapter, executor = _adaptador()
    executor.bodies[tina.OP_COBERTURA] = corpo_cobertura()
    executor.bodies[tina.OP_CPT] = corpo_cpt()
    executor.bodies[tina.OP_PROCEDIMENTOS] = {
        "versao_catalogo": _VERSAO,
        "pagina": 1,
        "total_paginas": 1,
        "total_itens": 1,
        "itens": [{"codigo": "99000001", "descricao": "Exame Exemplo", "nome_tuss": None, "grupo": "Exames"}],
        "fonte_atualizada_em": "2026-10-19T12:00:00Z",
    }
    fonte = _fonte(adapter)
    assert isinstance(fonte.port, tina.AmhTinaAdapter)

    cobertura = await fonte.consultar_cobertura(REF, "99000001")
    assert cobertura is not None and cobertura.portable_subject_ref == REF and cobertura.itens
    cpt = await fonte.consultar_cpt(REF)
    assert cpt is not None and cpt.portable_subject_ref == REF
    catalogo = await fonte.consultar_catalogo_procedimentos()
    assert catalogo is not None
    assert catalogo.itens == (ProcedimentoCatalogo("99000001", "Exame Exemplo", None, "Exames"),)
    operacoes = [r.operation for r in executor.calls]
    assert operacoes == [tina.OP_COBERTURA, tina.OP_CPT, tina.OP_PROCEDIMENTOS]


async def test_ponta_a_ponta_sem_as_rotas_carregadas_a_fonte_cala() -> None:
    executor = Executor()
    adapter = tina.AmhTinaAdapter((_REAL / "tina.openapi.yaml").read_bytes(), executor=executor)
    fonte = _fonte(adapter)
    assert await fonte.consultar_cobertura(REF, "99000001") is None
    assert await fonte.consultar_cpt(REF) is None
    assert await fonte.consultar_catalogo_procedimentos() is None
    assert executor.calls == []
