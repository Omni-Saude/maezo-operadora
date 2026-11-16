"""As fontes da camada "seu plano" da Helena (DL-0167, HEL-3; pedidos (a) e (c) da T-MZO-B a T-MZO-A).

A fonte que o receptor injeta (`ConsultasPlanoAmh`, a mesma `consultas_plano` que o despachante passa ao
grafo) implementa de novo `FonteDeCobertura` e `FonteDeCpt` e, nova, `FonteDoCatalogoDeProcedimentos`. So'
LEITURA pela rota pinada: o que fica preso aqui:

  * a fonte satisfaz os tres protocolos de `maezo.contratos.fatos_do_plano` (e o de sempre);
  * cobertura e CPT: finalidade FIXA `atendimento_whatsapp`, base legal de execucao de contrato, o limite e os
    prazos da rota; recusa, falta de base legal, outro sujeito, prazo ou excecao = `None`; port sem a rota =
    `None` sem tocar na base legal; o procedimento pedido nunca vai a log;
  * catalogo: todas as paginas da MESMA foto viram um `CatalogoDeProcedimentos`; recusa ou excecao no meio,
    pagina/contagem incoerente, paginas demais, prazo ou versao que muda de novo depois do UNICO recomeco da
    pagina 1 = `None` (catalogo parcial nunca sai); a foto boa (nao vazia) vale so' o `max-age=300` do
    contrato; depois de uma falha, uma espera curta sem ler; chamadas simultaneas leem uma vez; a espera pela
    trava tem prazo e nada levanta; nem codigo nem descricao vao a log;
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


def _pagina(
    pagina: int, total: int, *codigos: str, versao: str = _VERSAO, total_itens: int | None = None
) -> CatalogoProcedimentosView:
    return CatalogoProcedimentosView(
        versao_catalogo=versao,
        pagina=pagina,
        total_paginas=total,
        total_itens=len(codigos) if total_itens is None else total_itens,
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
        """As respostas saem na ORDEM das chamadas (e recomecam da primeira depois da ultima)."""
        await self._r("catalogo", kw)
        feitas = sum(1 for nome, _ in self.chamadas if nome == "catalogo")
        resposta = self._paginas[(feitas - 1) % len(self._paginas)]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    @property
    def leituras_do_catalogo(self) -> int:
        return sum(1 for nome, _ in self.chamadas if nome == "catalogo")


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


async def test_cpt_sem_base_legal_nao_le_e_registra_o_motivo() -> None:
    port = _PortCompleto()
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(port, _BaseLegal(None)).consultar_cpt(REF) is None
    assert port.chamadas == []
    assert logs[-1]["event"] == "helena_cpt_lida" and logs[-1]["motivo"] == "sem_base_legal"


async def test_cpt_sem_a_rota_nem_pede_a_base_legal() -> None:
    base = _BaseLegal()
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(_PortDeSempre(), base).consultar_cpt(REF) is None
    assert base.pedidas == []
    assert logs[-1]["motivo"] == "rota_nao_carregada"


async def test_nada_da_cpt_vai_a_log() -> None:
    cpt = CptVigenteView(REF, True, 3, "2027-01-31", "2026-10-15T12:00:00Z")
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(_PortCompleto(cpt=PortResult.ok(cpt))).consultar_cpt(REF) is cpt
    assert logs and all("2027-01-31" not in repr(e) and REF not in repr(e) for e in logs)
    assert set(logs[-1]) == {"event", "log_level", "motivo"}


# --- catalogo --------------------------------------------------------------------------------------


async def test_catalogo_junta_todas_as_paginas_da_mesma_foto() -> None:
    port = _PortCompleto(
        paginas=[
            PortResult.ok(_pagina(1, 2, "99000001", "99000002", total_itens=3)),
            PortResult.ok(_pagina(2, 2, "99000003", total_itens=3)),
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


async def test_catalogo_vazio_e_um_catalogo_mas_nao_fica_guardado() -> None:
    port = _PortCompleto(paginas=[PortResult.ok(_pagina(1, 0))])
    fonte = _fonte(port)
    for _ in range(2):
        catalogo = await fonte.consultar_catalogo_procedimentos()
        assert catalogo is not None and catalogo.itens == ()
    assert port.leituras_do_catalogo == 2


@pytest.mark.parametrize(
    "paginas",
    [
        # a mesma pagina 1 devolvida duas vezes
        [
            PortResult.ok(_pagina(1, 2, "99000001", total_itens=2)),
            PortResult.ok(_pagina(1, 2, "99000001", total_itens=2)),
        ],
        # total de paginas que muda no meio
        [
            PortResult.ok(_pagina(1, 2, "99000001", total_itens=2)),
            PortResult.ok(_pagina(2, 3, "99000002", total_itens=2)),
        ],
        # contagem que nao bate: total_itens=3 com 1 item
        [PortResult.ok(_pagina(1, 1, "99000001", total_itens=3))],
        # a primeira resposta nao e' a pagina 1
        [PortResult.ok(_pagina(2, 1, "99000001"))],
        # codigo repetido (o contrato: uma linha por codigo distinto)
        [PortResult.ok(_pagina(1, 1, "99000001", "99000001"))],
    ],
    ids=["pagina_repetida", "total_mudou", "contagem", "nao_e_a_pagina_1", "codigo_repetido"],
)
async def test_catalogo_incoerente_nunca_sai(paginas: list[Any]) -> None:
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(_PortCompleto(paginas=paginas)).consultar_catalogo_procedimentos() is None
    assert logs[-1]["motivo"] == "catalogo_inconsistente"


@pytest.mark.parametrize(
    "paginas",
    [
        [PortResult.ok(_pagina(1, 2, "99000001", total_itens=2)), PortResult.refused(Reason.TIMEOUT)],
        [PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)],
    ],
    ids=["recusa_no_meio", "rota_desligada"],
)
async def test_catalogo_parcial_nunca_sai(paginas: list[Any]) -> None:
    assert await _fonte(_PortCompleto(paginas=paginas)).consultar_catalogo_procedimentos() is None


async def test_versao_que_muda_recomeca_da_pagina_1_uma_vez() -> None:
    port = _PortCompleto(
        paginas=[
            PortResult.ok(_pagina(1, 2, "99000001", total_itens=2)),
            PortResult.ok(_pagina(2, 2, "99000002", total_itens=2, versao=_VERSAO_NOVA)),
            PortResult.ok(_pagina(1, 2, "99000001", total_itens=2, versao=_VERSAO_NOVA)),
            PortResult.ok(_pagina(2, 2, "99000002", total_itens=2, versao=_VERSAO_NOVA)),
        ]
    )
    catalogo = await _fonte(port).consultar_catalogo_procedimentos()
    assert catalogo is not None and catalogo.versao_catalogo == _VERSAO_NOVA
    assert [kw["pagina"] for _, kw in port.chamadas] == [1, 2, 1, 2]


async def test_versao_que_muda_de_novo_depois_do_recomeco_nao_sai() -> None:
    port = _PortCompleto(
        paginas=[
            PortResult.ok(_pagina(1, 2, "99000001", total_itens=2)),
            PortResult.ok(_pagina(2, 2, "99000002", total_itens=2, versao=_VERSAO_NOVA)),
            PortResult.ok(_pagina(1, 2, "99000001", total_itens=2, versao=_VERSAO_NOVA)),
            PortResult.ok(_pagina(2, 2, "99000002", total_itens=2)),
        ]
    )
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(port).consultar_catalogo_procedimentos() is None
    assert port.leituras_do_catalogo == 4
    assert logs[-1]["motivo"] == "versao_mudou"


async def test_catalogo_com_paginas_demais_para_na_pagina_1() -> None:
    port = _PortCompleto(
        paginas=[PortResult.ok(_pagina(1, hc.MAX_PAGINAS_CATALOGO + 1, "99000001", total_itens=999))]
    )
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(port).consultar_catalogo_procedimentos() is None
    assert port.leituras_do_catalogo == 1
    assert logs[-1]["motivo"] == "paginas_demais"


async def test_excecao_so_na_pagina_2_vira_none() -> None:
    port = _PortCompleto(paginas=[PortResult.ok(_pagina(1, 2, "99000001", total_itens=2)), RuntimeError("x")])
    assert await _fonte(port).consultar_catalogo_procedimentos() is None
    assert port.leituras_do_catalogo == 2


async def test_catalogo_sem_a_rota_com_excecao_ou_prazo_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _fonte(_PortDeSempre()).consultar_catalogo_procedimentos() is None
    assert await _fonte(_PortCompleto(falha=RuntimeError("x"))).consultar_catalogo_procedimentos() is None
    monkeypatch.setattr(hc, "PRAZO_TOTAL_CATALOGO_S", 0.01)
    assert await _fonte(_PortCompleto(atraso=1.0)).consultar_catalogo_procedimentos() is None


async def test_a_foto_boa_e_reaproveitada_so_pelo_max_age_do_contrato(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hc.VALIDADE_DO_CATALOGO_S == 300.0  # `Cache-Control: private, max-age=300` do contrato
    port = _PortCompleto()
    fonte = _fonte(port)
    primeira = await fonte.consultar_catalogo_procedimentos()
    assert primeira is not None
    assert await fonte.consultar_catalogo_procedimentos() is primeira
    assert port.leituras_do_catalogo == 1
    monkeypatch.setattr(hc, "VALIDADE_DO_CATALOGO_S", 0.0)
    await fonte.consultar_catalogo_procedimentos()
    assert port.leituras_do_catalogo == 2


async def test_a_falha_espera_um_pouco_antes_de_ler_de_novo(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _PortCompleto(paginas=[PortResult.refused(Reason.TIMEOUT)])
    fonte = _fonte(port)
    assert await fonte.consultar_catalogo_procedimentos() is None
    assert await fonte.consultar_catalogo_procedimentos() is None
    assert port.leituras_do_catalogo == 1
    monkeypatch.setattr(hc, "ESPERA_APOS_FALHA_DO_CATALOGO_S", 0.0)
    fonte.port = _PortCompleto()  # type: ignore[assignment]
    assert await fonte.consultar_catalogo_procedimentos() is not None


async def test_chamadas_simultaneas_com_falha_leem_uma_vez_so() -> None:
    port = _PortCompleto(paginas=[PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)], atraso=0.01)
    fonte = _fonte(port)
    resultados = await asyncio.gather(*(fonte.consultar_catalogo_procedimentos() for _ in range(10)))
    assert resultados == [None] * 10
    assert port.leituras_do_catalogo == 1


async def test_chamadas_simultaneas_com_sucesso_leem_uma_vez_so() -> None:
    port = _PortCompleto(atraso=0.01)
    fonte = _fonte(port)
    resultados = await asyncio.gather(*(fonte.consultar_catalogo_procedimentos() for _ in range(10)))
    assert all(r is resultados[0] and r is not None for r in resultados)
    assert port.leituras_do_catalogo == 1


async def test_a_espera_pela_trava_tem_prazo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hc, "PRAZO_DA_TRAVA_DO_CATALOGO_S", 0.01)
    port = _PortCompleto()
    fonte = _fonte(port)
    await fonte._trava_catalogo.acquire()
    try:
        with structlog.testing.capture_logs() as logs:
            assert await fonte.consultar_catalogo_procedimentos() is None
    finally:
        fonte._trava_catalogo.release()
    assert port.leituras_do_catalogo == 0
    assert logs[-1]["motivo"] == "trava_ocupada"


async def test_o_catalogo_nunca_levanta_nem_pela_trava() -> None:
    fonte = _fonte(_PortCompleto())
    fonte._trava_catalogo = object()  # type: ignore[assignment]
    assert await fonte.consultar_catalogo_procedimentos() is None


async def test_nada_do_catalogo_vai_a_log() -> None:
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(_PortCompleto()).consultar_catalogo_procedimentos() is not None
    assert logs and all("99000001" not in repr(e) and "Procedimento" not in repr(e) for e in logs)
    assert set(logs[-1]) == {"event", "log_level", "motivo", "itens"}


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
